from datetime import datetime, timezone
from functools import lru_cache
import json
from pathlib import Path
from typing import Annotated, Any, Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import AnyHttpUrl, field_validator, model_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict


def _as_sequence(value: Any) -> Any:
    """Accept a comma-separated list as well as a JSON array.

    These settings are usually written into a shell-sourced .env, where the
    quotes in ["a","b"] are eaten before the process ever sees them. Requiring
    strict JSON means a one-character mistake stops the app from starting with
    an error that does not say which setting is wrong — a bad trade for a
    list of domains.
    """
    if not isinstance(value, str):
        return value
    text = value.strip()
    if not text:
        return ()
    if text.startswith("["):
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            # Most likely a shell-stripped ["a","b"]; fall through and treat
            # the bracketed body as a plain comma-separated list.
            text = text[1:-1] if text.endswith("]") else text[1:]
    return [item.strip().strip("\"'") for item in text.split(",") if item.strip()]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="CANVAS_HELPER_",
        env_file=None,
        extra="ignore",
    )

    canvas_base_url: AnyHttpUrl = "https://canvas.uts.edu.au"
    deployment_mode: Literal["local_desktop", "server"] = "local_desktop"
    environment: Literal["development", "test", "production"] = "development"
    database_url: str = "sqlite+aiosqlite:///./canvas_helper.db"
    db_pool_pre_ping: bool = True
    db_pool_recycle_seconds: int = 1800
    data_dir: Path = Path.home() / ".canvas-helper"
    frontend_dir: Path | None = None
    allowed_hosts: tuple[str, ...] = ("127.0.0.1", "localhost", "testserver")
    allowed_origins: tuple[str, ...] = (
        "http://127.0.0.1:5173",
        "http://localhost:5173",
        "http://127.0.0.1:8000",
        "http://localhost:8000",
        "http://testserver",
    )
    local_header_name: str = "X-Canvas-Helper"
    local_header_value: str = "1"
    action_token_ttl_seconds: int = 120
    magic_link_ttl_seconds: int = 900

    # Academic calendar. Canvas often omits term dates, so the app infers the
    # current teaching period from these. Override them for other institutions.
    academic_timezone: str = "Australia/Sydney"
    term_start_months: Annotated[tuple[int, ...], NoDecode] = (1, 7)

    # Abuse limits for the public server mode. Magic links are unauthenticated,
    # so they are the one endpoint an anonymous caller can use to burn SMTP
    # quota or grow the token table.
    magic_link_per_email_per_hour: int = 5
    magic_link_per_ip_per_hour: int = 20
    allowed_email_domains: Annotated[tuple[str, ...], NoDecode] = ()

    # Background retention sweeps. Zero disables an individual sweep.
    retention_sweep_seconds: int = 60 * 60
    sync_job_retention_days: int = 7
    magic_link_retention_days: int = 1
    user_session_retention_days: int = 7
    note_revision_keep_per_note: int = 50
    session_ttl_seconds: int = 60 * 60 * 24 * 30
    session_rotation_seconds: int = 60 * 60 * 24
    session_cookie_name: str = "canvas_helper_session"
    csrf_cookie_name: str = "canvas_helper_csrf"
    session_cookie_secure: bool = True
    public_url: str = "http://127.0.0.1:5173"
    email_backend: Literal["development", "smtp"] = "development"
    email_from: str = "Canvas Helper <noreply@localhost>"
    smtp_host: str | None = None
    smtp_port: int = 587
    smtp_username: str | None = None
    smtp_password: str | None = None
    smtp_starttls: bool = True
    smtp_use_ssl: bool = False
    credential_encryption_key: str | None = None
    credential_key_version: int = 1
    keyring_service: str = "canvas-helper"
    allow_development_secret_file: bool = False
    anthropic_api_key: str | None = None
    anthropic_model: str | None = None
    openai_api_key: str | None = None
    openai_model: str | None = None
    ai_request_timeout_seconds: float = 60.0
    smtp_timeout_seconds: float = 15.0
    sync_wait_max_seconds: float = 30.0
    worker_poll_seconds: float = 2.0
    sync_event_poll_seconds: float = 3.0
    debug: bool = False

    @field_validator("canvas_base_url")
    @classmethod
    def require_https_canvas(cls, value: AnyHttpUrl) -> AnyHttpUrl:
        if value.scheme != "https" or not value.host:
            raise ValueError("Canvas base URL must be HTTPS with a host")
        return value

    @field_validator("academic_timezone")
    @classmethod
    def require_known_timezone(cls, value: str) -> str:
        try:
            ZoneInfo(value)
        except (ZoneInfoNotFoundError, ValueError) as exc:
            raise ValueError(f"Unknown IANA time zone: {value}") from exc
        return value

    @field_validator("term_start_months", mode="before")
    @classmethod
    def split_month_numbers(cls, value: Any) -> Any:
        parsed = _as_sequence(value)
        if isinstance(parsed, (list, tuple)):
            return tuple(int(item) for item in parsed)
        return parsed

    @field_validator("term_start_months")
    @classmethod
    def require_month_numbers(cls, value: tuple[int, ...]) -> tuple[int, ...]:
        if not value:
            raise ValueError("At least one term start month is required")
        if any(month < 1 or month > 12 for month in value):
            raise ValueError("Term start months must be between 1 and 12")
        return tuple(sorted(set(value)))

    @field_validator("allowed_email_domains", mode="before")
    @classmethod
    def normalize_email_domains(cls, value: Any) -> Any:
        parsed = _as_sequence(value)
        if isinstance(parsed, (list, tuple)):
            return tuple(
                str(item).strip().lstrip("@").casefold()
                for item in parsed
                if str(item).strip()
            )
        return parsed

    @property
    def tzinfo(self) -> ZoneInfo:
        return ZoneInfo(self.academic_timezone)

    def current_term_start(self, now: datetime | None = None) -> datetime:
        """Start of the teaching period that contains ``now``, as UTC."""
        local_now = (now or datetime.now(timezone.utc)).astimezone(self.tzinfo)
        started = [month for month in self.term_start_months if month <= local_now.month]
        if started:
            year, month = local_now.year, max(started)
        else:
            year, month = local_now.year - 1, max(self.term_start_months)
        return datetime(year, month, 1, tzinfo=self.tzinfo).astimezone(timezone.utc)

    def email_domain_allowed(self, email: str) -> bool:
        if not self.allowed_email_domains:
            return True
        domain = email.rsplit("@", 1)[-1].casefold()
        return domain in self.allowed_email_domains

    @model_validator(mode="after")
    def production_is_fail_closed(self) -> "Settings":
        if self.environment != "production":
            return self
        if self.debug:
            raise ValueError("debug must be disabled in production")
        if self.deployment_mode == "server":
            if not self.credential_encryption_key:
                raise ValueError(
                    "credential_encryption_key is required for production server mode"
                )
            if not self.session_cookie_secure:
                raise ValueError("secure session cookies are required in production")
            if not self.public_url.startswith("https://"):
                raise ValueError("public_url must use HTTPS in production server mode")
            if self.email_backend != "smtp":
                raise ValueError("SMTP email is required in production server mode")
            if not self.smtp_host or not self.smtp_username or not self.smtp_password:
                raise ValueError("complete SMTP credentials are required in production")
            if not self.database_url.startswith("postgresql+asyncpg://"):
                raise ValueError("PostgreSQL is required in production server mode")
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()
