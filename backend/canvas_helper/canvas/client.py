import asyncio
import random
from collections.abc import AsyncIterator
from typing import Any
from urllib.parse import urljoin

import httpx

from .auth import CanvasBearerAuth


class CanvasError(RuntimeError):
    pass


class CanvasAuthenticationError(CanvasError):
    pass


class CanvasPermissionError(CanvasError):
    pass


class CanvasClient:
    def __init__(
        self,
        base_url: str,
        token: str,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
        max_retries: int = 3,
    ):
        self.base_url = base_url.rstrip("/") + "/"
        self.max_retries = max_retries
        self._semaphore = asyncio.Semaphore(2)
        self._client = httpx.AsyncClient(
            auth=CanvasBearerAuth(self.base_url, token),
            headers={"User-Agent": "CanvasHelper/0.1"},
            follow_redirects=True,
            max_redirects=5,
            timeout=httpx.Timeout(20.0, connect=10.0),
            transport=transport,
        )
        self.rate_limit_remaining: float | None = None

    async def close(self) -> None:
        await self._client.aclose()

    def _url(self, path_or_url: str) -> str:
        if path_or_url.startswith(("https://", "http://")):
            return path_or_url
        return urljoin(self.base_url, path_or_url.lstrip("/"))

    async def request(self, method: str, path_or_url: str, **kwargs) -> httpx.Response:
        method = method.upper()
        idempotent = method in {"GET", "HEAD"}
        for attempt in range(self.max_retries + 1):
            try:
                async with self._semaphore:
                    response = await self._client.request(
                        method, self._url(path_or_url), **kwargs
                    )
            except (httpx.ConnectError, httpx.ReadTimeout):
                if not idempotent or attempt >= self.max_retries:
                    raise
                await asyncio.sleep(0.15 * (2**attempt) + random.random() * 0.05)
                continue

            remaining = response.headers.get("X-Rate-Limit-Remaining")
            if remaining:
                try:
                    self.rate_limit_remaining = float(remaining)
                except ValueError:
                    pass
            retryable = response.status_code == 429 or (
                idempotent and response.status_code >= 500
            )
            if retryable and attempt < self.max_retries:
                retry_after = response.headers.get("Retry-After")
                delay = (
                    min(float(retry_after), 10.0)
                    if retry_after and retry_after.replace(".", "", 1).isdigit()
                    else 0.2 * (2**attempt) + random.random() * 0.1
                )
                await asyncio.sleep(delay)
                continue
            if response.status_code == 401:
                raise CanvasAuthenticationError("Canvas token is invalid or expired")
            if response.status_code == 403:
                raise CanvasPermissionError("Canvas denied access to this resource")
            response.raise_for_status()
            return response
        raise CanvasError("Request retry loop exhausted")

    async def get_json(self, path: str, **params) -> Any:
        response = await self.request("GET", path, params=params or None)
        return response.json()

    async def paginate(
        self, path: str, *, params: dict[str, Any] | None = None
    ) -> AsyncIterator[Any]:
        next_url: str | None = path
        next_params = {"per_page": 100, **(params or {})}
        while next_url:
            response = await self.request("GET", next_url, params=next_params)
            payload = response.json()
            if not isinstance(payload, list):
                raise CanvasError("Expected a list response while paginating")
            for item in payload:
                yield item
            next_url = response.links.get("next", {}).get("url")
            next_params = None
