import hashlib
import hmac
import logging
import secrets
import uuid
from datetime import datetime, timedelta, timezone
from urllib.parse import quote

from fastapi import BackgroundTasks, HTTPException, Request, Response
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from .config import Settings
from .email import EmailBackend
from .models import MagicLinkToken, User, UserSession

logger = logging.getLogger("canvas_helper.auth")

UNSAFE_METHODS = {"POST", "PUT", "PATCH", "DELETE"}

# How long a just-replaced session cookie keeps working, so that requests
# already in flight during rotation are not rejected.
ROTATION_GRACE_SECONDS = 120


def token_hash(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def aware(value: datetime) -> datetime:
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


def client_address(request: Request) -> str:
    """The caller's address, trusting the proxy chain uvicorn already parsed."""
    client = request.client
    return client.host if client else "unknown"


class RateLimited(HTTPException):
    def __init__(self, retry_after: int = 3600):
        super().__init__(
            status_code=429,
            detail="Too many sign-in requests. Try again later.",
            headers={"Retry-After": str(retry_after)},
        )


class AuthService:
    def __init__(self, settings: Settings, email_backend: EmailBackend):
        self.settings = settings
        self.email_backend = email_backend

    def _requester_hash(self, address: str) -> str:
        """Keyed digest of a client address; the address itself is never stored."""
        key = (
            self.settings.credential_encryption_key
            or self.settings.public_url
            or "canvas-helper"
        ).encode()
        return hmac.new(key, address.encode(), hashlib.sha256).hexdigest()

    async def _enforce_link_quota(
        self, session: AsyncSession, email: str, requester: str
    ) -> None:
        window_start = datetime.now(timezone.utc) - timedelta(hours=1)
        per_email = self.settings.magic_link_per_email_per_hour
        per_ip = self.settings.magic_link_per_ip_per_hour
        if per_email > 0:
            used = await session.scalar(
                select(func.count())
                .select_from(MagicLinkToken)
                .where(
                    MagicLinkToken.email == email,
                    MagicLinkToken.created_at >= window_start,
                )
            )
            if (used or 0) >= per_email:
                raise RateLimited()
        if per_ip > 0:
            used = await session.scalar(
                select(func.count())
                .select_from(MagicLinkToken)
                .where(
                    MagicLinkToken.requester_hash == requester,
                    MagicLinkToken.created_at >= window_start,
                )
            )
            if (used or 0) >= per_ip:
                raise RateLimited()

    def _set_cookies(
        self, response: Response, session_token: str, csrf_token: str
    ) -> None:
        common = {
            "secure": self.settings.session_cookie_secure,
            "samesite": "lax",
            "path": "/",
            "max_age": self.settings.session_ttl_seconds,
        }
        response.set_cookie(
            self.settings.session_cookie_name,
            session_token,
            httponly=True,
            **common,
        )
        response.set_cookie(
            self.settings.csrf_cookie_name,
            csrf_token,
            httponly=False,
            **common,
        )

    def clear_cookies(self, response: Response) -> None:
        response.delete_cookie(self.settings.session_cookie_name, path="/")
        response.delete_cookie(self.settings.csrf_cookie_name, path="/")

    async def request_link(
        self,
        session: AsyncSession,
        email: str,
        *,
        request: Request | None = None,
        background: BackgroundTasks | None = None,
    ) -> None:
        email = email.strip().casefold()
        if not self.settings.email_domain_allowed(email):
            raise HTTPException(
                status_code=403,
                detail="This email domain is not allowed to sign in here.",
            )
        requester = self._requester_hash(
            client_address(request) if request is not None else "unknown"
        )
        await self._enforce_link_quota(session, email, requester)
        raw = secrets.token_urlsafe(32)
        session.add(
            MagicLinkToken(
                email=email,
                token_hash=token_hash(raw),
                requester_hash=requester,
                expires_at=datetime.now(timezone.utc)
                + timedelta(seconds=self.settings.magic_link_ttl_seconds),
            )
        )
        await session.commit()
        url = (
            f"{self.settings.public_url.rstrip('/')}/"
            f"?magic_token={quote(raw, safe='')}"
        )
        # Delivery can be slow or wedged; never hold the request open for it.
        if background is not None:
            background.add_task(self._deliver, email, url)
        else:
            await self._deliver(email, url)

    async def _deliver(self, email: str, url: str) -> None:
        try:
            await self.email_backend.send_magic_link(email, url)
        except Exception:
            # The address is deliberately omitted; it is account PII.
            logger.exception("Magic-link delivery failed")

    async def verify(
        self, session: AsyncSession, response: Response, raw_token: str
    ) -> User:
        now = datetime.now(timezone.utc)
        digest = token_hash(raw_token)
        token = await session.scalar(
            select(MagicLinkToken).where(MagicLinkToken.token_hash == digest)
        )
        if (
            token is None
            or token.consumed_at is not None
            or aware(token.expires_at) <= now
        ):
            raise HTTPException(status_code=400, detail="Magic link is invalid or expired")
        consumed = await session.execute(
            update(MagicLinkToken)
            .where(
                MagicLinkToken.id == token.id,
                MagicLinkToken.consumed_at.is_(None),
            )
            .values(consumed_at=now)
        )
        if consumed.rowcount != 1:
            await session.rollback()
            raise HTTPException(status_code=400, detail="Magic link is invalid or expired")
        user = await session.scalar(select(User).where(User.email == token.email))
        if user is None:
            user = User(
                id=str(uuid.uuid4()),
                email=token.email,
                display_name=token.email.split("@", 1)[0],
            )
            session.add(user)
            await session.flush()
        session_token = secrets.token_urlsafe(32)
        csrf_token = secrets.token_urlsafe(32)
        session.add(
            UserSession(
                user_id=user.id,
                token_hash=token_hash(session_token),
                csrf_hash=token_hash(csrf_token),
                expires_at=now
                + timedelta(seconds=self.settings.session_ttl_seconds),
                rotated_at=now,
            )
        )
        await session.commit()
        self._set_cookies(response, session_token, csrf_token)
        return user

    async def authenticate(
        self,
        request: Request,
        response: Response,
        session: AsyncSession,
        *,
        require_csrf: bool = True,
    ) -> tuple[User, UserSession]:
        raw = request.cookies.get(self.settings.session_cookie_name)
        if not raw:
            raise HTTPException(status_code=401, detail="Authentication is required")
        digest = token_hash(raw)
        now = datetime.now(timezone.utc)
        login = await session.scalar(
            select(UserSession).where(UserSession.token_hash == digest)
        )
        on_grace_token = False
        if login is None:
            # A concurrent request may have just rotated this session.
            login = await session.scalar(
                select(UserSession).where(UserSession.previous_token_hash == digest)
            )
            if login is not None:
                grace_until = login.previous_expires_at
                if grace_until is None or aware(grace_until) <= now:
                    login = None
                else:
                    on_grace_token = True
        if (
            login is None
            or login.revoked_at is not None
            or aware(login.expires_at) <= now
        ):
            self.clear_cookies(response)
            raise HTTPException(status_code=401, detail="Authentication is required")
        if require_csrf and request.method in UNSAFE_METHODS:
            header = request.headers.get("X-CSRF-Token")
            cookie = request.cookies.get(self.settings.csrf_cookie_name)
            accepted = [login.csrf_hash]
            if on_grace_token and login.previous_csrf_hash:
                accepted.append(login.previous_csrf_hash)
            if (
                not header
                or not cookie
                or not secrets.compare_digest(header, cookie)
                or not any(
                    secrets.compare_digest(token_hash(header), candidate)
                    for candidate in accepted
                )
            ):
                raise HTTPException(status_code=403, detail="CSRF validation failed")
        user = await session.get(User, login.user_id)
        if user is None:
            raise HTTPException(status_code=401, detail="Authentication is required")
        if not on_grace_token and (now - aware(login.rotated_at)).total_seconds() >= (
            self.settings.session_rotation_seconds
        ):
            new_session_token = secrets.token_urlsafe(32)
            new_csrf_token = secrets.token_urlsafe(32)
            login.previous_token_hash = login.token_hash
            login.previous_csrf_hash = login.csrf_hash
            login.previous_expires_at = now + timedelta(seconds=ROTATION_GRACE_SECONDS)
            login.token_hash = token_hash(new_session_token)
            login.csrf_hash = token_hash(new_csrf_token)
            login.rotated_at = now
            login.expires_at = now + timedelta(
                seconds=self.settings.session_ttl_seconds
            )
            await session.commit()
            self._set_cookies(response, new_session_token, new_csrf_token)
        request.state.auth_session = login
        return user, login

    async def logout(
        self,
        request: Request,
        response: Response,
        session: AsyncSession,
    ) -> None:
        _, login = await self.authenticate(request, response, session)
        login.revoked_at = datetime.now(timezone.utc)
        await session.commit()
        self.clear_cookies(response)
