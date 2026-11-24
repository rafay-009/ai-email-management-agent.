import base64
import json
import re
import time
from datetime import UTC, datetime
from email import policy
from email.message import EmailMessage
from email.parser import BytesParser
from email.utils import getaddresses
from html.parser import HTMLParser
from urllib.parse import quote

import httpx
from cryptography.fernet import Fernet

from . import config
from .db import accounts, engine, uid

GMAIL = "https://gmail.googleapis.com/gmail/v1/users/me"
TOKEN = "https://oauth2.googleapis.com/token"
REVOKE = "https://oauth2.googleapis.com/revoke"
SCOPE = "https://www.googleapis.com/auth/gmail."
READ, SEND, MODIFY = (SCOPE + suffix for suffix in ("readonly", "send", "modify"))


class ProviderError(Exception):
    def __init__(self, code, unknown=False):
        self.code = code
        self.unknown = unknown


def encrypt(data):
    return Fernet(config.TOKEN_KEY.encode()).encrypt(json.dumps(data).encode()).decode()


def decrypt(data):
    return json.loads(Fernet(config.TOKEN_KEY.encode()).decrypt(data.encode()))


def token_request(data):
    try:
        response = httpx.post(
            TOKEN,
            data={**data, "client_id": config.GOOGLE_ID, "client_secret": config.GOOGLE_SECRET},
            timeout=8,
        )
        response.raise_for_status()
        token = response.json()
        return {**token, "expires_at": time.time() + token.get("expires_in", 3600)}
    except (httpx.HTTPError, ValueError) as exc:
        raise ProviderError("oauth_token_error") from exc


class PlainHTML(HTMLParser):
    hidden = frozenset({"script", "style", "template", "head"})
    blocks = frozenset({"br", "p", "div", "li", "tr", "h1", "h2", "blockquote"})

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts = []
        self.ignored = []

    def handle_starttag(self, tag, attrs):
        if tag in self.hidden:
            self.ignored.append(tag)
        if tag in self.blocks and not self.ignored:
            self.parts.append("\n")

    def handle_endtag(self, tag):
        if self.ignored and self.ignored[-1] == tag:
            self.ignored.pop()
        elif tag in self.blocks and not self.ignored:
            self.parts.append("\n")

    def handle_data(self, data):
        if not self.ignored:
            self.parts.append(data)


def body_parts(part: EmailMessage, depth: int = 0):
    if depth > 12 or part.get_filename() or part.get_content_disposition() == "attachment":
        return
    if part.get_content_maintype() == "multipart":
        for child in part.iter_parts():
            yield from body_parts(child, depth + 1)
    elif part.get_content_maintype() == "text":
        yield part


def plain_text(message: EmailMessage):
    plain, html = [], []
    for part in body_parts(message):
        kind = part.get_content_type()
        if kind not in {"text/plain", "text/html"}:
            continue
        raw = (part.get_payload(decode=True) or b"")[: config.MAX_BODY * 8]
        try:
            text = raw.decode(part.get_content_charset() or "utf-8", errors="replace")
        except LookupError:
            text = raw.decode("utf-8", errors="replace")
        if kind == "text/plain":
            plain.append(text)
        else:
            parser = PlainHTML()
            parser.feed(text)
            html.append("".join(parser.parts))
    return "\n".join(plain or html).strip()[: config.MAX_BODY]


def parse_message(data):
    raw = data["raw"]
    message = BytesParser(policy=policy.default).parsebytes(
        base64.urlsafe_b64decode(raw + "=" * (-len(raw) % 4))
    )
    return {
        "provider_id": data["id"],
        "thread_id": data["threadId"],
        "sender": str(message.get("From", ""))[:400],
        "subject": (str(message.get("Subject", "")) or "(No subject)")[:240],
        "body": plain_text(message),
        "received_at": datetime.fromtimestamp(int(data["internalDate"]) / 1000, UTC).isoformat(),
        "unread": "UNREAD" in data.get("labelIds", []),
        "labels": data.get("labelIds", []),
        "headers": {
            name: str(message.get(name, ""))[:1600]
            for name in ("Reply-To", "Message-ID", "References", "To")
        },
    }


def recipient(value):
    addresses = getaddresses([value])
    if len(addresses) != 1 or not re.fullmatch(
        r"[^\s@<>\r\n,;]+@[^\s@<>\r\n,;]+\.[^\s@<>\r\n,;]+", addresses[0][1]
    ):
        raise ProviderError("invalid_recipient")
    return addresses[0][1]


def reply_mime(payload):
    message = EmailMessage(policy=policy.SMTP)
    message["From"] = recipient(payload["from"])
    message["To"] = recipient(payload["to"])
    message["Subject"] = payload["subject"]
    message_id = payload["headers"].get("Message-ID", "")
    if re.fullmatch(r"<[^<>\s]+>", message_id):
        message["In-Reply-To"] = message_id
        previous = re.findall(r"<[^<>\s]+>", payload["headers"].get("References", ""))
        message["References"] = " ".join([*previous[-12:], message_id])
    message.set_content(payload["body"])
    return base64.urlsafe_b64encode(message.as_bytes()).decode()


class Demo:
    def list_messages(self, limit=50):
        return []

    def get_message(self, message_id):
        raise ProviderError("demo_fixture_is_local")

    def get_thread(self, thread_id):
        return []

    def send_reply(self, payload):
        return f"simulated-{uid()}"

    def modify_labels(self, payload):
        return f"simulated-{uid()}"

    def disconnect(self):
        return True


class Gmail:
    def __init__(self, account):
        if account["status"] != "connected" or not account["token"]:
            raise ProviderError("disconnected")
        self.account = account
        self.token = decrypt(account["token"])
        self.read_deadline: float | None = None

    def refresh(self):
        refresh = self.token.get("refresh_token")
        if not refresh:
            raise ProviderError("reauthorization_required")
        self.token = {
            **self.token,
            **token_request({"grant_type": "refresh_token", "refresh_token": refresh}),
        }
        with engine.begin() as conn:
            conn.execute(
                accounts.update()
                .where(accounts.c.id == self.account["id"], accounts.c.status == "connected")
                .values(token=encrypt(self.token))
            )

    def request(self, method, path, params=None, payload=None):
        write = method == "POST"
        if self.token["expires_at"] <= time.time() + 15:
            self.refresh()
        refreshed = False
        for attempt in range(1 if write else 3):
            remaining = self.read_deadline - time.monotonic() if self.read_deadline is not None else 8
            if remaining <= 0:
                raise ProviderError("sync_deadline")
            try:
                response = httpx.request(
                    method,
                    GMAIL + path,
                    headers={"Authorization": f"Bearer {self.token['access_token']}"},
                    params=params,
                    json=payload,
                    timeout=min(8, remaining),
                )
            except httpx.RequestError as exc:
                if write or attempt == 2:
                    raise ProviderError("provider_timeout", unknown=write) from exc
                continue
            status = response.status_code
            if status == 401 and not write and not refreshed:
                self.refresh()
                refreshed = True
                continue
            if status == 429 or status >= 500:
                if write or attempt == 2:
                    raise ProviderError(
                        "provider_throttled" if status == 429 else "provider_unavailable",
                        unknown=write and status >= 500,
                    )
                time.sleep(0.2 * (attempt + 1))
                continue
            if not response.is_success:
                raise ProviderError(
                    {
                        401: "reauthorization_required",
                        403: "missing_scope",
                        404: "provider_message_missing",
                    }.get(status, "provider_rejected")
                )
            try:
                return response.json()
            except ValueError as exc:
                raise ProviderError("invalid_provider_response", unknown=write) from exc
        raise ProviderError("provider_unavailable")

    def require(self, *scopes):
        if not set(scopes).intersection(self.account["scopes"]):
            raise ProviderError("missing_scope")

    def list_messages(self, limit=50):
        self.require(READ, MODIFY)
        self.read_deadline = time.monotonic() + 40
        rows, page = [], None
        for _ in range(5):
            params = {"labelIds": "INBOX", "maxResults": min(25, limit - len(rows))}
            if page:
                params["pageToken"] = page
            data = self.request("GET", "/messages", params=params)
            rows.extend(item["id"] for item in data.get("messages", []))
            page = data.get("nextPageToken")
            if len(rows) >= limit or not page:
                break
        return rows[:limit]

    def get_message(self, message_id):
        self.require(READ, MODIFY)
        return parse_message(
            self.request("GET", f"/messages/{quote(message_id, safe='')}", params={"format": "raw"})
        )

    def get_thread(self, thread_id):
        self.require(READ, MODIFY)
        data = self.request("GET", f"/threads/{quote(thread_id, safe='')}", params={"format": "minimal"})
        return [
            self.get_message(item["id"]) for item in data.get("messages", [])[-config.THREAD_LIMIT :]
        ]

    def send_reply(self, payload):
        self.require(SEND, MODIFY)
        result = self.request(
            "POST",
            "/messages/send",
            payload={"raw": reply_mime(payload), "threadId": payload["thread_id"]},
        )
        if not result.get("id"):
            raise ProviderError("invalid_provider_response", unknown=True)
        return result["id"]

    def modify_labels(self, payload):
        self.require(MODIFY)
        result = self.request(
            "POST",
            f"/messages/{quote(payload['provider_id'], safe='')}/modify",
            payload=payload["changes"],
        )
        if not result.get("id"):
            raise ProviderError("invalid_provider_response", unknown=True)
        return result["id"]

    def disconnect(self):
        try:
            response = httpx.post(
                REVOKE,
                data={"token": self.token.get("refresh_token") or self.token["access_token"]},
                timeout=8,
            )
            return response.is_success
        except httpx.RequestError:
            return False


def provider(account):
    return Demo() if account["provider"] == "demo" else Gmail(account)
