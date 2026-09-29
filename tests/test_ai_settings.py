"""A signed-in user's own AI key: store it, report that it exists, never return it.

The endpoint that stores the key existed with no caller and no test, and nothing
let the page ask whether a key was already saved. The property that matters is
one-directional: a key goes in and must never come back out — not in the PUT
response, not in the status read, not to another user.
"""

from fastapi.testclient import TestClient

from canvas_helper.main import create_app

from test_auth_credentials import csrf_headers, login, server_settings

KEY = "sk-ant-api03-this-must-never-be-echoed"


def signed_in(tmp_path, email="student@example.edu"):
    app = create_app(server_settings(tmp_path))
    client = TestClient(app)
    client.__enter__()
    login(client, app, email)
    return app, client


def test_status_before_anything_is_saved(tmp_path):
    _, client = signed_in(tmp_path)
    try:
        response = client.get("/api/settings/ai")
        assert response.status_code == 200
        assert response.json() == {"configured": False}
    finally:
        client.__exit__(None, None, None)


def test_saving_a_key_reports_configured_and_never_echoes_it(tmp_path):
    _, client = signed_in(tmp_path)
    try:
        saved = client.put(
            "/api/settings/ai",
            json={"provider": "anthropic", "api_key": KEY, "model": "claude-sonnet-5-5"},
            headers=csrf_headers(client),
        )
        assert saved.status_code == 200
        assert KEY not in saved.text

        status = client.get("/api/settings/ai")
        assert status.status_code == 200
        body = status.json()
        assert body["configured"] is True
        assert body["provider"] == "anthropic"
        assert body["model"] == "claude-sonnet-5-5"
        assert KEY not in status.text
        # A hint that lets the user recognise which key is saved, without
        # carrying enough of it to be useful to anyone who reads it.
        assert body["key_hint"].endswith(KEY[-4:])
        assert KEY[:-4] not in body["key_hint"]
    finally:
        client.__exit__(None, None, None)


def test_the_key_is_encrypted_at_rest(tmp_path):
    import sqlite3

    _, client = signed_in(tmp_path)
    try:
        client.put(
            "/api/settings/ai",
            json={"provider": "openai", "api_key": KEY, "model": "gpt-x"},
            headers=csrf_headers(client),
        )
    finally:
        client.__exit__(None, None, None)

    with sqlite3.connect(tmp_path / "server.db") as connection:
        blobs = [row[0] for row in connection.execute("SELECT ciphertext FROM encrypted_credentials")]
    assert blobs, "nothing was stored"
    assert all(KEY.encode() not in blob for blob in blobs)


def test_a_key_can_be_removed(tmp_path):
    _, client = signed_in(tmp_path)
    try:
        client.put(
            "/api/settings/ai",
            json={"provider": "anthropic", "api_key": KEY, "model": "m"},
            headers=csrf_headers(client),
        )
        removed = client.delete("/api/settings/ai", headers=csrf_headers(client))
        assert removed.status_code == 204
        assert client.get("/api/settings/ai").json() == {"configured": False}
    finally:
        client.__exit__(None, None, None)


def test_writes_need_the_csrf_token_and_a_session(tmp_path):
    app, client = signed_in(tmp_path)
    try:
        body = {"provider": "anthropic", "api_key": KEY, "model": "m"}
        assert client.put("/api/settings/ai", json=body).status_code == 403
        assert client.delete("/api/settings/ai").status_code == 403
        client.cookies.clear()
        assert client.get("/api/settings/ai").status_code == 401
    finally:
        client.__exit__(None, None, None)


def test_one_users_key_is_invisible_to_another(tmp_path):
    app = create_app(server_settings(tmp_path))
    with TestClient(app) as alice, TestClient(app) as bob:
        login(alice, app, "alice@example.edu")
        alice.put(
            "/api/settings/ai",
            json={"provider": "anthropic", "api_key": KEY, "model": "m"},
            headers=csrf_headers(alice),
        )
        login(bob, app, "bob@example.edu")
        seen = bob.get("/api/settings/ai")
        assert seen.json() == {"configured": False}
        assert KEY not in seen.text


def test_a_short_or_unknown_provider_is_rejected(tmp_path):
    _, client = signed_in(tmp_path)
    try:
        headers = csrf_headers(client)
        assert client.put(
            "/api/settings/ai",
            json={"provider": "anthropic", "api_key": "short", "model": "m"},
            headers=headers,
        ).status_code == 422
        assert client.put(
            "/api/settings/ai",
            json={"provider": "someone-else", "api_key": KEY, "model": "m"},
            headers=headers,
        ).status_code == 422
    finally:
        client.__exit__(None, None, None)


def test_switching_provider_does_not_leave_the_old_key_behind(tmp_path):
    """The old key would sit in the vault where the user can neither see nor remove it."""
    import sqlite3

    _, client = signed_in(tmp_path)
    try:
        headers = csrf_headers(client)
        client.put(
            "/api/settings/ai",
            json={"provider": "anthropic", "api_key": KEY, "model": "m1"},
            headers=headers,
        )
        client.put(
            "/api/settings/ai",
            json={"provider": "openai", "api_key": "sk-proj-a-different-key-1234", "model": "m2"},
            headers=headers,
        )
        assert client.get("/api/settings/ai").json()["provider"] == "openai"
    finally:
        client.__exit__(None, None, None)

    with sqlite3.connect(tmp_path / "server.db") as connection:
        names = {row[0] for row in connection.execute("SELECT name FROM encrypted_credentials")}
    assert "anthropic_api_key" not in names
    assert "anthropic_model" not in names


def test_deepseek_can_be_saved_and_replaces_another_providers_key(tmp_path):
    import sqlite3

    _, client = signed_in(tmp_path)
    try:
        headers = csrf_headers(client)
        client.put(
            "/api/settings/ai",
            json={"provider": "openai", "api_key": KEY, "model": "m1"},
            headers=headers,
        )
        saved = client.put(
            "/api/settings/ai",
            json={"provider": "deepseek", "api_key": "sk-deepseek-key-5678", "model": "m2"},
            headers=headers,
        )
        assert saved.status_code == 200
        body = client.get("/api/settings/ai").json()
        assert body["provider"] == "deepseek"
        assert body["key_hint"] == "…5678"
        assert client.put(
            "/api/settings/ai",
            json={"provider": "nonsense", "api_key": KEY, "model": "m"},
            headers=headers,
        ).status_code == 422
        assert client.delete("/api/settings/ai", headers=headers).status_code == 204
    finally:
        client.__exit__(None, None, None)

    with sqlite3.connect(tmp_path / "server.db") as connection:
        names = {row[0] for row in connection.execute("SELECT name FROM encrypted_credentials")}
    assert not {n for n in names if n.startswith(("openai", "deepseek", "ai_provider"))}


def test_deepseek_talks_to_its_own_endpoint_through_the_openai_client(monkeypatch):
    import asyncio
    import sys
    from types import ModuleType, SimpleNamespace

    from canvas_helper.ai import AIService

    seen = {}

    class FakeClient:
        def __init__(self, **kwargs):
            seen.update(kwargs)
            self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._create))

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        async def _create(self, **kwargs):
            seen["model"] = kwargs["model"]
            return SimpleNamespace(
                model=kwargs["model"],
                choices=[SimpleNamespace(message=SimpleNamespace(content="hi"))],
                usage=SimpleNamespace(prompt_tokens=1, completion_tokens=2),
            )

    # CI does not install the optional 'ai' extra, so stand in for the package
    # instead of patching it: the test is about which endpoint gets used.
    fake_openai = ModuleType("openai")
    fake_openai.AsyncOpenAI = FakeClient
    monkeypatch.setitem(sys.modules, "openai", fake_openai)
    service = AIService("sk-deepseek-key-5678", "some-model", "deepseek")
    assert service.available
    result = asyncio.run(service.chat([{"role": "user", "content": "hello"}]))
    assert result["text"] == "hi"
    assert seen["base_url"] == "https://api.deepseek.com"
    assert seen["api_key"] == "sk-deepseek-key-5678"

    # OpenAI itself must keep using its default endpoint.
    seen.clear()
    asyncio.run(AIService("sk-proj-abcdefgh", "m", "openai").chat([{"role": "user", "content": "hello"}]))
    assert seen["base_url"] is None
