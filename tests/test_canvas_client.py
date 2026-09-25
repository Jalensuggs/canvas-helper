import httpx
import pytest

from canvas_helper.canvas.auth import CanvasBearerAuth
from canvas_helper.canvas.client import CanvasClient


def apply_auth(auth: httpx.Auth, url: str) -> httpx.Request:
    request = httpx.Request("GET", url, headers={"Authorization": "Bearer stale"})
    return next(auth.auth_flow(request))


def test_bearer_auth_is_https_same_origin_only():
    auth = CanvasBearerAuth("https://canvas.example.edu", "secret-token")

    assert (
        apply_auth(auth, "https://canvas.example.edu/api/v1/courses").headers[
            "Authorization"
        ]
        == "Bearer secret-token"
    )
    assert "Authorization" not in apply_auth(
        auth, "https://files.example.edu/download"
    ).headers
    assert "Authorization" not in apply_auth(
        auth, "http://canvas.example.edu/api/v1/courses"
    ).headers
    assert "Authorization" not in apply_auth(
        auth, "https://canvas.example.edu:444/api/v1/courses"
    ).headers


@pytest.mark.asyncio
async def test_pagination_follows_next_even_when_page_is_empty():
    calls = []

    async def handler(request: httpx.Request) -> httpx.Response:
        calls.append(str(request.url))
        if len(calls) == 1:
            return httpx.Response(
                200,
                json=[],
                headers={
                    "Link": '<https://canvas.example.edu/api/v1/items?page=2>; rel="next"'
                },
            )
        return httpx.Response(200, json=[{"id": 7}])

    client = CanvasClient(
        "https://canvas.example.edu",
        "token-value",
        transport=httpx.MockTransport(handler),
    )
    try:
        items = [item async for item in client.paginate("/api/v1/items")]
    finally:
        await client.close()

    assert items == [{"id": 7}]
    assert len(calls) == 2
    assert "per_page=100" in calls[0]


@pytest.mark.asyncio
async def test_cross_host_redirect_does_not_forward_authorization():
    seen = []

    async def handler(request: httpx.Request) -> httpx.Response:
        seen.append((request.url.host, request.headers.get("Authorization")))
        if request.url.host == "canvas.example.edu":
            return httpx.Response(
                302, headers={"Location": "https://files.example.net/download/1"}
            )
        return httpx.Response(200, content=b"file")

    client = CanvasClient(
        "https://canvas.example.edu",
        "token-value",
        transport=httpx.MockTransport(handler),
    )
    try:
        await client.request("GET", "/api/v1/files/1")
    finally:
        await client.close()

    assert seen == [
        ("canvas.example.edu", "Bearer token-value"),
        ("files.example.net", None),
    ]
