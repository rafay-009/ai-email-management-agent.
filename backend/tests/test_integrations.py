from types import SimpleNamespace
from urllib.parse import parse_qs, urlparse

import httpx
from cryptography.fernet import Fernet

from app import agent, config, providers
from app.auth import OAUTH_COOKIE
from app.providers import MODIFY, READ, Gmail, ProviderError


def test_oauth_state_pkce_and_trusted_profile(client, monkeypatch):
    monkeypatch.setattr(config, "GOOGLE_ENABLED", True)
    monkeypatch.setattr(config, "GOOGLE_ID", "fixture-client")
    monkeypatch.setattr(config, "GOOGLE_SECRET", "fixture-secret")
    monkeypatch.setattr(config, "SESSION_SECRET", "x" * 48)
    monkeypatch.setattr(config, "TOKEN_KEY", Fernet.generate_key().decode())
    captured = {}

    def exchange(data):
        captured.update(data)
        return {"scope": READ, "access_token": "fixture-access", "refresh_token": "fixture-refresh"}

    monkeypatch.setattr("app.auth.token_request", exchange)
    monkeypatch.setattr(
        Gmail, "request", lambda self, method, path: {"emailAddress": "fixture@example.invalid"}
    )
    route = "/api/v1/auth/google"
    start = client.get(f"{route}/start", follow_redirects=False)
    query = parse_qs(urlparse(start.headers["location"]).query)
    assert query["code_challenge_method"] == ["S256"]
    assert query["scope"] == [READ]
    assert OAUTH_COOKIE in client.cookies
    forged = client.get(
        f"{route}/callback", params={"state": "invalid", "code": "fixture-code"}, follow_redirects=False
    )
    assert forged.headers["location"].endswith("gmail=failed")
    assert not captured

    start = client.get(f"{route}/start", follow_redirects=False)
    state = parse_qs(urlparse(start.headers["location"]).query)["state"][0]
    callback = client.get(
        f"{route}/callback", params={"state": state, "code": "fixture-code"}, follow_redirects=False
    )
    assert callback.headers["location"].endswith("gmail=connected")
    assert len(captured["code_verifier"]) >= 43
    assert client.get("/api/v1/me").json()["email"] == "fixture@example.invalid"


def test_gmail_reads_retry_but_write_timeout_is_unknown(monkeypatch):
    monkeypatch.setattr(config, "TOKEN_KEY", Fernet.generate_key().decode())
    token = providers.encrypt({"access_token": "fixture-token", "expires_at": 9e9})
    adapter = Gmail({"id": "fixture", "status": "connected", "token": token, "scopes": [MODIFY]})
    calls = []

    def request(method, url, **kwargs):
        calls.append(method)
        if method == "POST":
            raise httpx.ReadTimeout("fixture timeout")
        return httpx.Response(429) if len(calls) == 1 else httpx.Response(200, json={"id": "fixture"})

    monkeypatch.setattr(httpx, "request", request)
    monkeypatch.setattr(providers.time, "sleep", lambda _: None)
    assert adapter.request("GET", "/messages/fixture") == {"id": "fixture"}
    try:
        adapter.request("POST", "/messages/send", payload={})
    except ProviderError as exc:
        assert exc.unknown
    else:
        raise AssertionError("Expected an unknown write outcome")
    assert calls == ["GET", "GET", "POST"]


def test_structured_prompt_roles_and_evidence_retry(monkeypatch):
    calls = []
    source = {
        "subject": "Order issue",
        "body": "Could you verify the order?",
        "sender": "sender@example.invalid",
    }
    result = agent.mock(source)

    def parse(**kwargs):
        calls.append(kwargs)
        parsed = result.model_copy(update={"evidence": ["invented"]}) if len(calls) == 1 else result
        return SimpleNamespace(output_parsed=parsed, usage=None)

    monkeypatch.setenv("OPENAI_API_KEY", "fixture-key")
    monkeypatch.setattr(
        agent, "OpenAI", lambda **kwargs: SimpleNamespace(responses=SimpleNamespace(parse=parse))
    )
    actual, usage = agent.structured(
        "classify",
        agent.Analysis,
        {"message": source, "preferences": {"tone": "friendly"}, "model": "fixture-model"},
    )
    assert actual == result and usage is None
    assert len(calls) == 2
    assert calls[0]["store"] is False
    developer, user = calls[0]["input"]
    assert developer["role"] == "developer" and "untrusted" in developer["content"]
    assert user["role"] == "user" and source["body"] in user["content"]
