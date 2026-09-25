from urllib.parse import urlsplit

import httpx


class CanvasBearerAuth(httpx.Auth):
    requires_request_body = True

    def __init__(self, base_url: str, token: str):
        parsed = urlsplit(base_url)
        if parsed.scheme != "https" or not parsed.hostname:
            raise ValueError("Canvas authentication requires an HTTPS base URL")
        self.origin = (
            parsed.scheme.lower(),
            parsed.hostname.lower(),
            parsed.port or 443,
        )
        self.token = token

    def auth_flow(self, request: httpx.Request):
        parsed = urlsplit(str(request.url))
        request_origin = (
            parsed.scheme.lower(),
            (parsed.hostname or "").lower(),
            parsed.port or (443 if parsed.scheme.lower() == "https" else 80),
        )
        request.headers.pop("Authorization", None)
        if request_origin == self.origin and parsed.scheme.lower() == "https":
            request.headers["Authorization"] = f"Bearer {self.token}"
        yield request
