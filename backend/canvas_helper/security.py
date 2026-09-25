import os
import re
import unicodedata
from pathlib import Path
from urllib.parse import urlsplit

import nh3
from fastapi import HTTPException, Request
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse

from .config import Settings

_SAFE_COMPONENT = re.compile(r"[^A-Za-z0-9._() \-\u0080-\uffff]+")


class LocalAccessMiddleware(BaseHTTPMiddleware):
    def __init__(self, app, settings: Settings):
        super().__init__(app)
        self.settings = settings

    async def dispatch(self, request: Request, call_next):
        if self.settings.deployment_mode == "server":
            return await call_next(request)
        host = (request.headers.get("host") or "").split(":", 1)[0].lower()
        if host not in self.settings.allowed_hosts:
            return JSONResponse({"detail": "Invalid Host"}, status_code=400)

        if request.method not in {"GET", "HEAD", "OPTIONS"}:
            origin = request.headers.get("origin")
            if origin:
                parsed = urlsplit(origin)
                normalized = f"{parsed.scheme}://{parsed.netloc}"
                if normalized not in self.settings.allowed_origins:
                    return JSONResponse({"detail": "Invalid Origin"}, status_code=403)
            if (
                request.headers.get(self.settings.local_header_name)
                != self.settings.local_header_value
            ):
                return JSONResponse(
                    {"detail": "Missing local request header"}, status_code=403
                )
        return await call_next(request)


def sanitize_html(value: str) -> str:
    return nh3.clean(
        value,
        tags={
            "p",
            "br",
            "strong",
            "em",
            "ul",
            "ol",
            "li",
            "a",
            "blockquote",
            "code",
            "pre",
            "h1",
            "h2",
            "h3",
            "h4",
            "table",
            "thead",
            "tbody",
            "tr",
            "th",
            "td",
        },
        attributes={"a": {"href", "title"}, "*": {"class"}},
        url_schemes={"https", "http", "mailto"},
        link_rel="noopener noreferrer",
    )


def safe_component(value: str, max_length: int = 120) -> str:
    value = unicodedata.normalize("NFC", value).strip()
    if "/" in value or "\\" in value or value in {".", ".."}:
        raise ValueError("Path separators and traversal are not allowed")
    value = _SAFE_COMPONENT.sub("_", value).strip(" .")
    if not value or value in {".", ".."}:
        raise ValueError("Unsafe empty path component")
    return value[:max_length]


def safe_join(root: Path, *components: str) -> Path:
    root = root.expanduser().resolve()
    candidate = root.joinpath(*(safe_component(item) for item in components))
    candidate_parent = candidate.parent.resolve()
    if os.path.commonpath((root, candidate_parent)) != str(root):
        raise ValueError("Path escapes root")
    current = root
    for component in candidate.relative_to(root).parts:
        current = current / component
        if current.exists() and current.is_symlink():
            raise ValueError("Symbolic links are not allowed")
    return candidate


def require_local_request(request: Request) -> None:
    client = request.client
    if client and client.host not in {"127.0.0.1", "::1", "testclient"}:
        raise HTTPException(status_code=403, detail="Local access only")
