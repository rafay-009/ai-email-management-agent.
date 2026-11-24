import uuid
from contextlib import asynccontextmanager
from typing import Annotated
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from fastapi import APIRouter, Depends, FastAPI, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy import String, and_, case, cast, func, or_, select
from starlette.exceptions import HTTPException

from . import agent, config, services
from . import contracts as schema
from .auth import router as auth_router
from .common import account_for, fail, find, identity, owned, rate, record
from .db import analyses, approvals, audit, drafts, emails, engine, preferences, runs, tasks
from .providers import ProviderError, provider, recipient


@asynccontextmanager
async def lifespan(app):
    config.validate()
    yield


app = FastAPI(title="InboxPilot AI", version="0.1.0", lifespan=lifespan)
router = APIRouter(prefix="/api/v1")
router.include_router(auth_router)
User = Annotated[dict, Depends(identity)]


def error(request, status, code, message):
    return JSONResponse(
        status_code=status,
        content={"code": code, "message": message, "request_id": request.state.request_id},
    )


@app.middleware("http")
async def security(request: Request, call_next):
    request.state.request_id = str(uuid.uuid4())
    if request.method in {"POST", "PATCH", "DELETE", "PUT"}:
        length = request.headers.get("content-length")
        if (length and int(length) > 32768) or request.headers.get("transfer-encoding"):
            return error(request, 413, "request_too_large", "Request body exceeds the allowed size.")
    response = await call_next(request)
    response.headers["X-Request-ID"] = request.state.request_id
    response.headers["Cache-Control"] = "no-store"
    response.headers["X-Content-Type-Options"] = "nosniff"
    return response


app.add_middleware(
    CORSMiddleware,
    allow_origins=config.ORIGINS,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PATCH", "DELETE"],
    allow_headers=["Content-Type", "X-CSRF-Token"],
)


@app.exception_handler(HTTPException)
async def http_error(request, exc):
    detail = (
        exc.detail
        if isinstance(exc.detail, dict)
        else {"code": "http_error", "message": str(exc.detail)}
    )
    return error(request, exc.status_code, detail["code"], detail["message"])


@app.exception_handler(RequestValidationError)
async def validation_error(request, exc):
    fields = ", ".join(str(item["loc"][-1]) for item in exc.errors()[:4])
    return error(request, 422, "invalid_request", f"Check the following fields: {fields}.")


@app.exception_handler(ProviderError)
async def provider_error(request, exc):
    status = {
        "missing_scope": 403,
        "disconnected": 403,
        "provider_throttled": 429,
        "provider_unavailable": 503,
        "sync_deadline": 503,
    }.get(exc.code, 502)
    return error(request, status, exc.code, "The email provider could not complete the request.")


@app.exception_handler(Exception)
async def unexpected_error(request, exc):
    return error(request, 500, "unexpected_error", "An unexpected error occurred.")


@app.get("/health")
@router.get("/health")
def health():
    return {"status": "ok"}


def inbox_query(user):
    ranked = (
        select(
            analyses.c.email_id,
            analyses.c.result,
            func.row_number()
            .over(
                partition_by=analyses.c.email_id,
                order_by=(analyses.c.created_at.desc(), analyses.c.id.desc()),
            )
            .label("rank"),
        )
        .join(emails, emails.c.id == analyses.c.email_id)
        .where(emails.c.user_id == user)
        .subquery()
    )
    return select(emails, ranked.c.result.label("analysis")).outerjoin(
        ranked, and_(ranked.c.email_id == emails.c.id, ranked.c.rank == 1)
    ).where(emails.c.user_id == user), ranked


def public_email(row, detail=False):
    fields = (
        "id",
        "sender",
        "subject",
        "received_at",
        "unread",
        "labels",
        "priority_override",
        "analysis",
    )
    output = {key: row.get(key) for key in fields}
    score = row.get("priority_override")
    if score is None and row.get("analysis"):
        score = row["analysis"]["priority_score"]
    output.update(
        score=score, priority=agent.band(score) if score is not None else None, preview=row["body"][:145]
    )
    if detail:
        try:
            address = recipient(row["headers"].get("Reply-To") or row["sender"])
        except ProviderError:
            address = None
        output.update(body=row["body"], reply_to=address)
    return output


@router.get("/emails")
def inbox(
    auth: User,
    q: str = Query("", max_length=200),
    category: schema.Category | None = None,
    priority: str | None = Query(None, pattern="^(critical|high|normal|low|untriaged)$"),
    sort: str = Query("newest", pattern="^(newest|oldest|priority)$"),
    unread: bool | None = None,
    archived: bool = False,
    page: int = Query(1, ge=1, le=1000),
    limit: int = Query(25, ge=1, le=100),
):
    query, ranked = inbox_query(auth["user_id"])
    score = func.coalesce(emails.c.priority_override, ranked.c.result["priority_score"].as_integer())
    if q:
        query = query.where(
            or_(
                *(
                    column.icontains(q, autoescape=True)
                    for column in (emails.c.sender, emails.c.subject, emails.c.body)
                )
            )
        )
    if category:
        query = query.where(ranked.c.result["category"].as_string() == category)
    if priority:
        ranges = {"critical": (80, 100), "high": (60, 79), "normal": (30, 59), "low": (0, 29)}
        query = query.where(
            score.is_(None) if priority == "untriaged" else score.between(*ranges[priority])
        )
    if unread is not None:
        query = query.where(emails.c.unread == unread)
    query = query.where(
        cast(emails.c.labels, String).like('%"INBOX"%')
        if not archived
        else ~cast(emails.c.labels, String).like('%"INBOX"%')
    )
    date_order = emails.c.received_at.asc() if sort == "oldest" else emails.c.received_at.desc()
    orders = (func.coalesce(score, -1).desc(), date_order) if sort == "priority" else (date_order,)
    with engine.connect() as conn:
        total = conn.scalar(select(func.count()).select_from(query.subquery()))
        rows = conn.execute(
            query.order_by(*orders, emails.c.id).offset((page - 1) * limit).limit(limit)
        ).mappings()
        return {"items": [public_email(row) for row in rows], "page": page, "total": total}


@router.post("/emails/sync")
def sync(auth: User):
    user = auth["user_id"]
    rate(user, "sync", 2)
    with engine.connect() as conn:
        account = account_for(conn, user)
    saved, failures = {}, 0
    try:
        adapter = provider(account)
        message_ids = adapter.list_messages(50)
        for index, message_id in enumerate(message_ids):
            try:
                message = adapter.get_message(message_id)
                saved[message["provider_id"]] = message
            except ProviderError as exc:
                if exc.code == "sync_deadline":
                    failures += len(message_ids) - index
                    break
                if exc.code in {"missing_scope", "reauthorization_required"} and not saved:
                    raise
                failures += 1
            except (ValueError, KeyError):
                failures += 1
    except ProviderError as exc:
        with engine.begin() as conn:
            record(conn, user, "Inbox synchronized", account["id"], "failed", code=exc.code)
        raise
    with engine.begin() as conn:
        for message in saved.values():
            services.store_email(conn, user, account["id"], message)
        record(
            conn,
            user,
            "Inbox synchronized",
            account["id"],
            "partial" if failures else "succeeded",
            count=len(saved),
            failures=failures,
        )
    return {"synced": len(saved), "failed": failures, "status": "partial" if failures else "succeeded"}


@router.post("/emails/analyze-batch")
def batch(body: schema.Batch, auth: User):
    if len(body.ids) > config.MAX_BATCH:
        fail(422, "batch_too_large", "Select fewer messages.")
    user = auth["user_id"]
    results = []
    for ident in dict.fromkeys(body.ids):
        try:
            results.append({"email_id": ident, **services.analyze(user, ident)})
        except HTTPException as exc:
            results.append({"email_id": ident, "status": "failed", "error_code": exc.detail["code"]})
    return {"items": results}


@router.get("/emails/{ident}")
def detail(ident: str, auth: User, draft_id: str | None = None):
    user = auth["user_id"]
    with engine.connect() as conn:
        message = owned(conn, emails, ident, user)
        analysis = services.latest(conn, ident)
        draft = find(
            conn,
            drafts,
            drafts.c.email_id == ident,
            drafts.c.user_id == user,
            order=drafts.c.created_at.desc(),
        )
        if draft_id:
            draft = owned(conn, drafts, draft_id, user)
            if draft["email_id"] != ident:
                fail(404, "resource_missing", "This draft does not belong to the message.")
        history = conn.execute(
            select(
                runs.c.id,
                runs.c.status,
                runs.c.started_at,
                runs.c.completed_at,
                runs.c.model,
                runs.c.error_code,
                runs.c.usage,
            )
            .where(runs.c.email_id == ident, runs.c.user_id == user)
            .order_by(runs.c.started_at.desc())
            .limit(20)
        ).mappings()
        return {
            **public_email({**message, "analysis": analysis}, True),
            "draft": services.public_draft(draft),
            "runs": [dict(row) for row in history],
        }


@router.patch("/emails/{ident}")
def edit_message(ident: str, body: schema.EmailEdit, auth: User):
    with engine.begin() as conn:
        message = owned(conn, emails, ident, auth["user_id"])
        values = body.model_dump(exclude_unset=True)
        if values.get("unread") is None:
            values.pop("unread", None)
        if values:
            conn.execute(emails.update().where(emails.c.id == ident).values(**values))
        return public_email({**message, **values, "analysis": services.latest(conn, ident)})


@router.get("/emails/{ident}/thread")
def thread(ident: str, auth: User):
    with engine.connect() as conn:
        message = owned(conn, emails, ident, auth["user_id"])
        return {
            "items": [
                {k: row[k] for k in ("id", "sender", "subject", "body", "received_at")}
                for row in services.thread(conn, message)
            ]
        }


@router.post("/emails/{ident}/analyze")
def analyze(ident: str, auth: User):
    return services.analyze(auth["user_id"], ident)


@router.post("/emails/{ident}/draft")
def generate(ident: str, body: schema.DraftRequest, auth: User):
    return services.draft_reply(auth["user_id"], ident, body.tone)


@router.post("/emails/{ident}/propose")
def propose(ident: str, body: schema.Proposal, auth: User):
    return services.propose(auth["user_id"], email_id=ident, action=body.action)


@router.patch("/drafts/{ident}")
def save(ident: str, body: schema.DraftEdit, auth: User):
    return services.save_draft(auth["user_id"], ident, body)


@router.post("/drafts/{ident}/propose-send")
def propose_send(ident: str, auth: User):
    return services.propose(auth["user_id"], draft_id=ident)


@router.get("/approvals")
def list_approvals(auth: User, limit: int = Query(50, ge=1, le=100), offset: int = Query(0, ge=0)):
    with engine.begin() as conn:
        user = auth["user_id"]
        services.recover_executing(conn, user)
        priority = case((approvals.c.status == "pending", 0), else_=1)
        rows = conn.execute(
            select(approvals)
            .where(approvals.c.user_id == user)
            .order_by(priority, approvals.c.created_at.desc())
            .offset(offset)
            .limit(limit)
        ).mappings()
        return {"items": [services.approval_view(dict(row)) for row in rows]}


@router.post("/approvals/{ident}/approve")
def approve(ident: str, body: schema.Decision, auth: User):
    return services.decide(auth["user_id"], ident, body.payload_hash, True)


@router.post("/approvals/{ident}/reject")
def reject(ident: str, body: schema.Decision, auth: User):
    return services.decide(auth["user_id"], ident, body.payload_hash, False)


@router.get("/tasks")
def list_tasks(auth: User, limit: int = Query(50, ge=1, le=100), offset: int = Query(0, ge=0)):
    with engine.connect() as conn:
        rows = conn.execute(
            select(tasks, emails.c.subject)
            .join(emails, emails.c.id == tasks.c.email_id)
            .where(tasks.c.user_id == auth["user_id"])
            .order_by(tasks.c.status.desc(), tasks.c.due_at.asc().nulls_last(), tasks.c.id)
            .offset(offset)
            .limit(limit)
        ).mappings()
        return {"items": [dict(row) for row in rows]}


@router.patch("/tasks/{ident}")
def edit_task(ident: str, body: schema.TaskEdit, auth: User):
    user = auth["user_id"]
    values = body.model_dump(mode="json", exclude_unset=True)
    if values.get("status") is None:
        values.pop("status", None)
    with engine.begin() as conn:
        task = owned(conn, tasks, ident, user)
        if values:
            conn.execute(tasks.update().where(tasks.c.id == ident).values(**values))
            record(conn, user, "Task updated", ident, "succeeded")
        return {**task, **values}


@router.get("/preferences")
def get_preferences(auth: User):
    with engine.connect() as conn:
        return find(conn, preferences, preferences.c.user_id == auth["user_id"])


@router.patch("/preferences")
def set_preferences(body: schema.Preferences, auth: User):
    user = auth["user_id"]
    values = body.model_dump(exclude_none=True)
    try:
        if "timezone" in values:
            ZoneInfo(values["timezone"])
        if "vip_senders" in values:
            values["vip_senders"] = sorted({recipient(item).lower() for item in values["vip_senders"]})
    except (ZoneInfoNotFoundError, ValueError, ProviderError):
        fail(422, "invalid_preferences", "Enter an IANA timezone and valid VIP email addresses.")
    with engine.begin() as conn:
        if values:
            conn.execute(preferences.update().where(preferences.c.user_id == user).values(**values))
            record(conn, user, "Preferences updated", user, "succeeded")
        return find(conn, preferences, preferences.c.user_id == user)


@router.get("/audit")
def get_audit(auth: User, limit: int = Query(30, ge=1, le=100), offset: int = Query(0, ge=0)):
    with engine.begin() as conn:
        user = auth["user_id"]
        services.recover_executing(conn, user)
        rows = conn.execute(
            select(audit)
            .where(audit.c.user_id == user)
            .order_by(audit.c.created_at.desc(), audit.c.id)
            .offset(offset)
            .limit(limit)
        ).mappings()
        return {"items": [dict(row) for row in rows]}


@router.get("/dashboard")
def dashboard(auth: User):
    user = auth["user_id"]
    query, ranked = inbox_query(user)
    score = func.coalesce(emails.c.priority_override, ranked.c.result["priority_score"].as_integer(), -1)
    with engine.begin() as conn:
        services.recover_executing(conn, user)
        active = query.where(cast(emails.c.labels, String).like('%"INBOX"%'))
        source = query.subquery()
        total = conn.scalar(select(func.count()).select_from(source))
        analyzed = conn.scalar(
            select(func.count()).select_from(query.where(ranked.c.email_id.is_not(None)).subquery())
        )
        urgent = conn.scalar(select(func.count()).select_from(active.where(score >= 80).subquery()))
        sent = select(drafts.c.email_id).where(drafts.c.user_id == user, drafts.c.status == "sent")
        awaiting = conn.scalar(
            select(func.count()).select_from(
                active.where(
                    ranked.c.result["reply_required"].as_boolean().is_(True), emails.c.id.not_in(sent)
                ).subquery()
            )
        )
        pending = conn.scalar(
            select(func.count())
            .select_from(approvals)
            .where(approvals.c.user_id == user, approvals.c.status == "pending")
        )
        opened = conn.scalar(
            select(func.count())
            .select_from(tasks)
            .where(tasks.c.user_id == user, tasks.c.status == "open")
        )
        messages = conn.execute(
            active.order_by(score.desc(), emails.c.received_at.desc()).limit(5)
        ).mappings()
        events = conn.execute(
            select(audit).where(audit.c.user_id == user).order_by(audit.c.created_at.desc()).limit(5)
        ).mappings()
        return {
            "total": total,
            "analyzed": analyzed,
            "urgent": urgent,
            "awaiting_reply": awaiting,
            "pending": pending,
            "open_tasks": opened,
            "priority_inbox": [public_email(row) for row in messages],
            "activity": [dict(row) for row in events],
        }


app.include_router(router)
