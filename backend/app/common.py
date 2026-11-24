import hashlib
import json
import secrets
import time
from datetime import UTC, datetime, timedelta

from fastapi import HTTPException, Request
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

from . import config
from .db import accounts, audit, engine, limits, now, sessions, users

COOKIE = "inboxpilot_session"


def digest(value):
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
    ).hexdigest()


def fail(status, code, message):
    raise HTTPException(status, {"code": code, "message": message})


def find(conn, table, *where, order=None):
    query = select(table).where(*where)
    if order is not None:
        query = query.order_by(order)
    row = conn.execute(query.limit(1)).mappings().first()
    return dict(row) if row else None


def owned(conn, table, ident, user):
    row = find(conn, table, table.c.id == ident, table.c.user_id == user)
    if not row:
        fail(404, "resource_missing", "This item could not be found.")
    return row


def record(conn, user, action, resource, outcome, **metadata):
    conn.execute(
        audit.insert().values(
            user_id=user, action=action, resource_id=resource, outcome=outcome, metadata=metadata
        )
    )


def session(conn, response, user):
    token, csrf = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
    expires = (datetime.now(UTC) + timedelta(days=7)).isoformat()
    conn.execute(sessions.insert().values(id=digest(token), user_id=user, csrf=csrf, expires_at=expires))
    response.set_cookie(
        COOKIE, token, max_age=604800, httponly=True, samesite="lax", secure=config.PRODUCTION
    )


def origin(request):
    if request.headers.get("origin") not in {*config.ORIGINS, config.APP, config.API}:
        fail(403, "origin_rejected", "The request origin is not permitted.")


def identity(request: Request):
    if request.method not in {"GET", "HEAD", "OPTIONS"}:
        origin(request)
    token = request.cookies.get(COOKIE, "")
    with engine.connect() as conn:
        row = find(
            conn,
            sessions,
            sessions.c.id == digest(token),
            sessions.c.revoked_at.is_(None),
            sessions.c.expires_at > now(),
        )
    if not row:
        fail(401, "sign_in_required", "Start a demo workspace or connect Gmail.")
    if request.method not in {"GET", "HEAD", "OPTIONS"} and not secrets.compare_digest(
        row["csrf"], request.headers.get("x-csrf-token", "")
    ):
        fail(403, "csrf_rejected", "Reload the workspace and try again.")
    return row


def profile(conn, auth):
    user = find(conn, users, users.c.id == auth["user_id"])
    account = account_for(conn, user["id"])
    return {
        "id": user["id"],
        "email": user["email"],
        "mode": account["provider"],
        "status": account["status"],
        "scopes": account["scopes"],
        "csrf": auth["csrf"],
        "llm_provider": config.LLM if account["provider"] == "gmail" else "mock",
    }


def account_for(conn, user):
    return find(conn, accounts, accounts.c.user_id == user)


def lock_account(conn, user):
    # Serialize execution claims and disconnections on both supported databases.
    conn.execute(accounts.update().where(accounts.c.user_id == user).values(status=accounts.c.status))
    return account_for(conn, user)


def upsert(table):
    return (sqlite_insert if engine.dialect.name == "sqlite" else pg_insert)(table)


def rate(user, operation, maximum, period=60):
    window = int(time.time()) // period
    with engine.begin() as conn:
        conn.execute(
            limits.delete().where(
                limits.c.user_id == user, limits.c.operation == operation, limits.c.window < window
            )
        )
        statement = upsert(limits).values(user_id=user, operation=operation, window=window, count=1)
        result = conn.execute(
            statement.on_conflict_do_update(
                index_elements=["user_id", "operation", "window"],
                set_={"count": limits.c.count + 1},
                where=limits.c.count < maximum,
            )
        )
        if not result.rowcount:
            fail(429, "rate_limit", "Too many requests. Try again shortly.")
