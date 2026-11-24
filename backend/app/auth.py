import base64
import hashlib
import hmac
import json
import secrets
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Annotated
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.responses import RedirectResponse

from . import config
from .common import (
    COOKIE,
    account_for,
    fail,
    find,
    identity,
    lock_account,
    origin,
    profile,
    record,
    session,
    upsert,
)
from .contracts import DeleteData
from .db import accounts, approvals, engine, now, preferences, sessions, uid, users
from .providers import (
    MODIFY,
    READ,
    SEND,
    Gmail,
    ProviderError,
    decrypt,
    encrypt,
    provider,
    token_request,
)
from .services import recover_executing, store_email

router = APIRouter()
User = Annotated[dict, Depends(identity)]
DEMO_FIXTURE = Path(__file__).with_name("fixtures") / "demo.json"
OAUTH_COOKIE = "inboxpilot_oauth"
OAUTH_PATH = "/api/v1/auth/google/callback"


@router.post("/auth/demo", status_code=201)
def demo(request: Request, response: Response):
    origin(request)
    if not config.DEMO:
        fail(404, "demo_disabled", "Demo workspaces are disabled.")
    try:
        auth = identity(request)
    except HTTPException as exc:
        if exc.status_code != 401 and request.cookies.get(COOKIE):
            raise
    else:
        with engine.connect() as conn:
            if account_for(conn, auth["user_id"])["provider"] == "demo":
                return profile(conn, auth)
    user, account = uid(), uid()
    address = "demo@inboxpilot.example"
    with engine.begin() as conn:
        conn.execute(users.insert().values(id=user, email=address))
        conn.execute(
            accounts.insert().values(
                id=account, user_id=user, address=address, provider="demo", status="connected"
            )
        )
        conn.execute(preferences.insert().values(user_id=user))
        base = datetime.now(UTC).replace(second=0, microsecond=0)
        for index, item in enumerate(json.loads(DEMO_FIXTURE.read_text())):
            store_email(
                conn,
                user,
                account,
                {
                    "provider_id": item["id"],
                    "thread_id": item["thread_id"],
                    "sender": item["sender"],
                    "subject": item["subject"],
                    "body": item["body"],
                    "received_at": (base - timedelta(hours=index * 3)).isoformat(),
                    "unread": index < 9,
                    "labels": ["INBOX"],
                    "headers": {"Message-ID": f"<{item['id']}@example.invalid>"},
                },
            )
        record(conn, user, "Demo workspace created", account, "succeeded")
        session(conn, response, user)
    return {"mode": "demo"}


@router.get("/me")
def me(auth: User):
    with engine.connect() as conn:
        return profile(conn, auth) | {"gmail_available": config.GOOGLE_ENABLED}


@router.get("/auth/options")
def options():
    return {"gmail_available": config.GOOGLE_ENABLED, "demo_available": config.DEMO}


@router.post("/auth/logout")
def logout(response: Response, auth: User):
    with engine.begin() as conn:
        conn.execute(sessions.update().where(sessions.c.id == auth["id"]).values(revoked_at=now()))
    response.delete_cookie(COOKIE)
    return {"status": "signed_out"}


def sign(value):
    return hmac.new(config.SESSION_SECRET.encode(), value.encode(), hashlib.sha256).hexdigest()


@router.get("/auth/google/start")
def google_start(request: Request, permission: str = "read"):
    if not config.GOOGLE_ENABLED:
        fail(503, "gmail_unconfigured", "Gmail is not configured on this server.")
    if permission not in {"read", "send", "modify"}:
        fail(422, "invalid_permission", "Choose a supported Gmail permission.")
    user = None
    if permission != "read":
        auth = identity(request)
        with engine.connect() as conn:
            account = account_for(conn, auth["user_id"])
            if account["provider"] != "gmail" or account["status"] != "connected":
                fail(403, "gmail_required", "Connect Gmail first.")
        user = auth["user_id"]
    verifier, nonce = secrets.token_urlsafe(48), secrets.token_urlsafe(30)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip("=")
    scope = {"read": READ, "send": SEND, "modify": MODIFY}[permission]
    packed = base64.urlsafe_b64encode(
        json.dumps([nonce, verifier, scope, user, int(time.time())]).encode()
    ).decode()
    query = urlencode(
        {
            "client_id": config.GOOGLE_ID,
            "redirect_uri": config.REDIRECT,
            "response_type": "code",
            "scope": f"{READ} {scope}" if scope != READ else READ,
            "state": nonce,
            "code_challenge": challenge,
            "code_challenge_method": "S256",
            "access_type": "offline",
            "include_granted_scopes": "true",
            "prompt": "consent",
        }
    )
    response = RedirectResponse("https://accounts.google.com/o/oauth2/v2/auth?" + query, status_code=303)
    response.set_cookie(
        OAUTH_COOKIE,
        f"{packed}.{sign(packed)}",
        max_age=600,
        httponly=True,
        secure=config.PRODUCTION,
        samesite="lax",
        path=OAUTH_PATH,
    )
    return response


@router.get("/auth/google/callback")
def google_callback(request: Request, state: str = "", code: str = ""):
    response = RedirectResponse(config.APP + "/?gmail=connected", status_code=303)
    response.delete_cookie(OAUTH_COOKIE, path=OAUTH_PATH)
    if not config.GOOGLE_ENABLED:
        fail(503, "gmail_unconfigured", "Gmail is not configured on this server.")
    try:
        packed, signature = request.cookies.get(OAUTH_COOKIE, "").split(".")
        if not hmac.compare_digest(sign(packed), signature):
            raise ValueError
        expected, verifier, scope, linked_user, issued = json.loads(base64.urlsafe_b64decode(packed))
        if (
            not code
            or not secrets.compare_digest(state, expected)
            or not 0 <= time.time() - issued <= 600
        ):
            raise ValueError
        if linked_user and identity(request)["user_id"] != linked_user:
            raise ValueError
        token = token_request(
            {
                "grant_type": "authorization_code",
                "code": code,
                "code_verifier": verifier,
                "redirect_uri": config.REDIRECT,
            }
        )
        granted = token.get("scope", "").split()
        if scope not in granted or not {READ, MODIFY}.intersection(granted):
            raise ProviderError("missing_scope")
        temporary = Gmail({"id": "", "token": encrypt(token), "status": "connected", "scopes": granted})
        address = temporary.request("GET", "/profile")["emailAddress"].lower()
        with engine.begin() as conn:
            subject = f"gmail:{address}"
            insert_user = upsert(users).values(email=address, provider_subject=subject)
            conn.execute(insert_user.on_conflict_do_nothing(index_elements=["provider_subject"]))
            user = find(conn, users, users.c.provider_subject == subject)["id"]
            if linked_user and user != linked_user:
                raise ProviderError("account_mismatch")
            account = account_for(conn, user)
            if account and account["token"] and not token.get("refresh_token"):
                previous = decrypt(account["token"]).get("refresh_token")
                if previous:
                    token["refresh_token"] = previous
            account_data = {
                "address": address,
                "scopes": granted,
                "token": encrypt(token),
                "status": "connected",
            }
            if account:
                conn.execute(
                    accounts.update().where(accounts.c.id == account["id"]).values(**account_data)
                )
            else:
                conn.execute(accounts.insert().values(user_id=user, provider="gmail", **account_data))
                conn.execute(preferences.insert().values(user_id=user))
            record(conn, user, "Gmail connected", user, "succeeded")
            session(conn, response, user)
    except (HTTPException, ProviderError, ValueError, KeyError):
        response.headers["location"] = config.APP + "/?gmail=failed"
    return response


def revoke_and_clear(user):
    with engine.begin() as conn:
        account = lock_account(conn, user)
        recover_executing(conn, user)
        if find(conn, approvals, approvals.c.user_id == user, approvals.c.status == "executing"):
            fail(409, "execution_in_progress", "Wait for the approved action to finish.")
        conn.execute(
            accounts.update()
            .where(accounts.c.id == account["id"])
            .values(status="disconnected", token=None, scopes=[])
        )
    revoked = True
    if account["status"] == "connected":
        try:
            revoked = provider(account).disconnect()
        except (ProviderError, ValueError):
            revoked = False
    return revoked


@router.post("/accounts/disconnect")
def disconnect(auth: User):
    user = auth["user_id"]
    revoked = revoke_and_clear(user)
    with engine.begin() as conn:
        record(conn, user, "Account disconnected", user, "succeeded" if revoked else "revocation_failed")
    return {"status": "disconnected", "revoked": revoked}


@router.delete("/me/data")
def remove_data(body: DeleteData, response: Response, auth: User):
    user = auth["user_id"]
    revoked = revoke_and_clear(user)
    with engine.begin() as conn:
        conn.execute(users.delete().where(users.c.id == user))
    response.delete_cookie(COOKIE)
    return {"status": "deleted", "revoked": revoked}
