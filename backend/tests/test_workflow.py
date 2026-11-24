import base64
import json
from concurrent.futures import ThreadPoolExecutor
from email.message import EmailMessage
from pathlib import Path
from threading import Event

import pytest
from conftest import demo
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app import services
from app.agent import band, mock, priority
from app.common import digest
from app.config import APP
from app.contracts import Analysis
from app.db import accounts, engine
from app.main import app
from app.providers import Demo, ProviderError, parse_message, recipient, reply_mime

BASE = "/api/v1"


def email(client, q="Order #1042"):
    return client.get(f"{BASE}/emails", params={"q": q}).json()["items"][0]


def analyze(client, mail):
    return client.post(f"{BASE}/emails/{mail['id']}/analyze").json()


def request_send(client, mail):
    response = client.post(f"{BASE}/emails/{mail['id']}/draft", json={"tone": "friendly"})
    assert response.status_code == 200, response.text
    return client.post(f"{BASE}/drafts/{response.json()['id']}/propose-send").json()


def decision(client, approval, action="approve"):
    return client.post(
        f"{BASE}/approvals/{approval['id']}/{action}", json={"payload_hash": approval["payload_hash"]}
    )


def test_priority_validation_and_hash():
    assert priority(8, -1, True, True) == 60
    assert priority(4, 4, True, True) == 100
    assert [band(value) for value in (0, 29, 30, 59, 60, 79, 80, 100)] == [
        "low",
        "low",
        "normal",
        "normal",
        "high",
        "high",
        "critical",
        "critical",
    ]
    result = mock({"subject": "Invoice", "body": "Please settle the invoice by Friday."})
    assert result.tasks[0].due_at is None
    assert result.tasks[0].date_text == "Friday"
    assert digest({"a": 1, "b": 2}) == digest({"b": 2, "a": 1})
    with pytest.raises(ValidationError):
        Analysis.model_validate({**result.model_dump(), "urgency": 5, "unreviewed_action": "send"})


def test_mime_and_single_recipient():
    content = EmailMessage()
    content["From"] = "Sender <sender@example.invalid>"
    content["Reply-To"] = "help@example.invalid"
    content["Message-ID"] = "<id@example.invalid>"
    content.set_content(
        '<p>Hi <strong>there</strong></p><img src="https://example.invalid/pixel"><script>run()</script>',
        subtype="html",
    )
    content.add_attachment(
        b"attachment data", maintype="application", subtype="octet-stream", filename="file.txt"
    )
    parsed = parse_message(
        {
            "id": "1",
            "threadId": "2",
            "internalDate": "0",
            "raw": base64.urlsafe_b64encode(content.as_bytes()).decode(),
        }
    )
    assert parsed["body"] == "Hi there"
    assert recipient(parsed["headers"]["Reply-To"]) == "help@example.invalid"
    encoded = reply_mime(
        {
            "from": "demo@example.invalid",
            "to": "help@example.invalid",
            "subject": "Re: Ticket",
            "body": "Hi",
            "headers": parsed["headers"],
        }
    )
    assert b"In-Reply-To: <id@example.invalid>" in base64.urlsafe_b64decode(encoded)
    with pytest.raises(ProviderError):
        recipient("first@example.invalid, second@example.invalid")


def test_demo_pagination_and_owner_isolation(client):
    assert client.get(f"{BASE}/emails").status_code == 401
    demo(client)
    first = client.get(f"{BASE}/emails?limit=5").json()
    second = client.get(f"{BASE}/emails?limit=5&page=2").json()
    assert first["total"] == 24
    assert not {item["id"] for item in first["items"]}.intersection(
        item["id"] for item in second["items"]
    )
    with TestClient(app, base_url=APP, headers={"Origin": APP}) as other:
        demo(other)
        ident = first["items"][0]["id"]
        assert other.get(f"{BASE}/emails/{ident}").status_code == 404
        assert other.post(f"{BASE}/emails/{ident}/analyze").status_code == 404


def test_draft_review_hash_version_csrf_and_execution(client, monkeypatch):
    writes = []
    monkeypatch.setattr(
        Demo, "send_reply", lambda self, payload: writes.append(payload) or "demo-result"
    )
    demo(client)
    mail = email(client)
    assert analyze(client, mail)["status"] == "succeeded"
    assert analyze(client, mail)["cached"] is True
    proposal = request_send(client, mail)
    assert not writes
    path = f"{BASE}/approvals/{proposal['id']}/approve"
    assert (
        client.post(
            path, headers={"X-CSRF-Token": "bad"}, json={"payload_hash": proposal["payload_hash"]}
        ).status_code
        == 403
    )
    assert client.post(path, json={"payload_hash": "0" * 64}).status_code == 409
    edit = client.patch(
        f"{BASE}/drafts/{proposal['draft_id']}",
        json={"version": 1, "subject": proposal["payload"]["subject"], "body": "Reviewed exact text."},
    )
    assert edit.json()["version"] == 2
    assert decision(client, proposal).status_code == 409
    updated = client.post(f"{BASE}/drafts/{proposal['draft_id']}/propose-send").json()
    assert updated["payload"]["body"] == "Reviewed exact text."
    assert decision(client, updated).json()["status"] == "succeeded"
    assert decision(client, updated).status_code == 409
    assert len(writes) == 1
    assert writes[0]["body"] == "Reviewed exact text."
    assert client.get(f"{BASE}/dashboard").json()["pending"] == 0


def test_duplicate_approval_race_and_unknown(client, monkeypatch):
    demo(client)
    item = request_send(client, email(client))
    entered, finish = Event(), Event()
    calls = []

    def timeout(self, payload):
        calls.append(payload)
        entered.set()
        assert finish.wait(10)
        raise ProviderError("provider_timeout", unknown=True)

    monkeypatch.setattr(Demo, "send_reply", timeout)
    with ThreadPoolExecutor(max_workers=2) as workers:
        first = workers.submit(decision, client, item)
        assert entered.wait(10)
        try:
            assert workers.submit(decision, client, item).result(10).status_code == 409
        finally:
            finish.set()
        assert first.result(10).json()["status"] == "unknown"
    assert len(calls) == 1
    assert decision(client, item).status_code == 409


def test_missing_scope(client, monkeypatch):
    demo(client)
    item = request_send(client, email(client))
    with engine.begin() as conn:
        conn.execute(
            accounts.update()
            .where(accounts.c.id == item["payload"]["account_id"])
            .values(provider="gmail", scopes=[])
        )
    monkeypatch.setattr(
        services, "provider", lambda account: pytest.fail("Provider called without permission")
    )
    assert decision(client, item).status_code == 403


def test_malicious_text_rejection_task_dedupe_and_disconnect(client, monkeypatch, caplog):
    monkeypatch.setattr(Demo, "send_reply", lambda *args: pytest.fail("Unexpected provider write"))
    demo(client)
    malicious = email(client, "Immediate account verification")
    assert analyze(client, malicious)["status"] == "succeeded"
    detail = client.get(f"{BASE}/emails/{malicious['id']}").json()
    assert detail["analysis"]["category"] == "spam_suspected"
    assert not detail["draft"]
    assert "attacker@example.invalid" not in caplog.text
    for mail in client.get(f"{BASE}/emails?q=Need+the+project+file").json()["items"]:
        assert analyze(client, mail)["status"] == "succeeded"
    tasks = client.get(f"{BASE}/tasks").json()["items"]
    assert len(tasks) == 1
    assert (
        client.patch(f"{BASE}/tasks/{tasks[0]['id']}", json={"status": "completed"}).json()["status"]
        == "completed"
    )
    item = request_send(client, email(client))
    assert decision(client, item, "reject").json()["status"] == "rejected"
    assert decision(client, item).status_code == 409
    assert client.post(f"{BASE}/accounts/disconnect").json()["revoked"]
    assert client.get(f"{BASE}/me").json()["status"] == "disconnected"
    assert (
        client.request("DELETE", f"{BASE}/me/data", json={"confirm": "DELETE"}).json()["status"]
        == "deleted"
    )
    assert client.get(f"{BASE}/me").status_code == 401


def test_fixture_evaluation():
    scenarios = json.loads(Path("app/fixtures/demo.json").read_text())
    results = [(scenario["expected"], mock(scenario)) for scenario in scenarios]
    category = sum(expected["category"] == got.category for expected, got in results)
    matches = sum(expected["reply_required"] == got.reply_required for expected, got in results)
    true_positives = sum(expected["reply_required"] and got.reply_required for expected, got in results)
    predicted = sum(got.reply_required for _, got in results)
    actual = sum(expected["reply_required"] for expected, _ in results)
    scored = sum(
        expected["priority_band"]
        == band(priority(got.importance, got.urgency, got.reply_required, False))
        for expected, got in results
    )
    task_matches = sum(expected["task"] == bool(got.tasks) for expected, got in results)
    print(
        f"category={category}/{len(results)}; reply matches={matches}/{len(results)}; precision={true_positives}/{predicted}; recall={true_positives}/{actual}; priority={scored}/{len(results)}; task presence={task_matches}/{len(results)}"
    )
    assert category / len(results) >= 0.8
