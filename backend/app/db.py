from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from sqlalchemy import (
    JSON,
    Boolean,
    Column,
    ForeignKey,
    Index,
    Integer,
    MetaData,
    String,
    Table,
    Text,
    UniqueConstraint,
    create_engine,
    event,
)

from . import config

URL = config.DATABASE_URL
if URL.startswith("sqlite:///") and not URL.endswith(":memory:"):
    Path(URL.removeprefix("sqlite:///")).parent.mkdir(parents=True, exist_ok=True)
engine = create_engine(URL, pool_pre_ping=True)
if engine.dialect.name == "sqlite":

    @event.listens_for(engine, "connect")
    def sqlite_pragmas(connection, _):
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("PRAGMA busy_timeout=5000")


metadata = MetaData()


def now():
    return datetime.now(UTC).isoformat()


def uid():
    return str(uuid4())


def key():
    return Column("id", String(64), primary_key=True, default=uid)


def owner():
    return Column("user_id", ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)


def email_key():
    return Column("email_id", ForeignKey("emails.id", ondelete="CASCADE"), nullable=False, index=True)


def field(name, kind=Text, default=None):
    return Column(name, kind, nullable=False, default=default)


users = Table(
    "users",
    metadata,
    key(),
    Column("provider_subject", Text, unique=True),
    field("email"),
    field("created_at", default=now),
)
sessions = Table(
    "sessions", metadata, key(), owner(), field("expires_at"), field("csrf"), Column("revoked_at", Text)
)
accounts = Table(
    "email_accounts",
    metadata,
    key(),
    owner(),
    field("provider"),
    field("address"),
    field("scopes", JSON, list),
    Column("token", Text),
    field("status"),
    UniqueConstraint("user_id"),
)
emails = Table(
    "emails",
    metadata,
    key(),
    owner(),
    Column("account_id", ForeignKey("email_accounts.id", ondelete="CASCADE"), nullable=False),
    field("provider_id"),
    field("thread_id"),
    field("sender"),
    field("subject"),
    field("body"),
    field("received_at"),
    field("unread", Boolean, True),
    field("labels", JSON, list),
    field("headers", JSON, dict),
    field("content_hash"),
    Column("priority_override", Integer),
    UniqueConstraint("account_id", "provider_id"),
)
Index("ix_emails_account_received", emails.c.account_id, emails.c.received_at)
runs = Table(
    "agent_runs",
    metadata,
    key(),
    owner(),
    email_key(),
    field("status"),
    field("model"),
    field("prompt_version"),
    field("started_at", default=now),
    Column("completed_at", Text),
    Column("error_code", Text),
    Column("usage", JSON),
)
analyses = Table(
    "analyses",
    metadata,
    key(),
    email_key(),
    Column("run_id", ForeignKey("agent_runs.id", ondelete="CASCADE"), nullable=False),
    field("content_hash"),
    field("created_at", default=now),
    field("result", JSON),
)
Index("ix_analyses_email_run", analyses.c.email_id, analyses.c.run_id)
tasks = Table(
    "tasks",
    metadata,
    key(),
    owner(),
    email_key(),
    field("title"),
    Column("due_at", Text),
    Column("original_date_text", Text),
    Column("follow_up_at", Text),
    field("status", default="open"),
    field("dedupe_key"),
    UniqueConstraint("user_id", "dedupe_key"),
)
drafts = Table(
    "drafts",
    metadata,
    key(),
    owner(),
    email_key(),
    field("subject"),
    field("body"),
    field("version", Integer, 1),
    field("status", default="editing"),
    field("created_at", default=now),
    field("updated_at", default=now),
)
approvals = Table(
    "approvals",
    metadata,
    key(),
    owner(),
    email_key(),
    Column("draft_id", ForeignKey("drafts.id", ondelete="CASCADE")),
    field("action_type"),
    field("payload", JSON),
    field("payload_hash"),
    field("status", default="pending"),
    field("idempotency_key"),
    field("created_at", default=now),
    Column("decided_at", Text),
    Column("executed_at", Text),
    Column("provider_id", Text),
    Column("error_code", Text),
    UniqueConstraint("user_id", "idempotency_key"),
)
audit = Table(
    "audit_events",
    metadata,
    key(),
    owner(),
    field("action"),
    field("resource_id"),
    field("outcome"),
    field("metadata", JSON, dict),
    field("created_at", default=now),
)
preferences = Table(
    "preferences",
    metadata,
    owner(),
    field("timezone", default="UTC"),
    field("tone", default="professional"),
    field("vip_senders", JSON, list),
    field("follow_up_days", Integer, 3),
    field("sync_enabled", Boolean, False),
    field("ai_consent", Boolean, False),
    UniqueConstraint("user_id"),
)
limits = Table(
    "rate_limits",
    metadata,
    owner(),
    field("operation"),
    field("window", Integer),
    field("count", Integer),
    UniqueConstraint("user_id", "operation", "window"),
)
Index("ix_tasks_owner_status", tasks.c.user_id, tasks.c.status)
Index("ix_approvals_owner_status", approvals.c.user_id, approvals.c.status)
