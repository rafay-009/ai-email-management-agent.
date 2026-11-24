import re
from datetime import UTC, datetime, timedelta

from sqlalchemy import select

from . import agent, config
from .common import account_for, digest, fail, find, lock_account, owned, rate, record, upsert
from .db import analyses, approvals, drafts, emails, engine, now, preferences, runs, tasks, uid
from .providers import MODIFY, SEND, ProviderError, provider, recipient


def store_email(conn, user, account, data):
    content_hash = digest([data["subject"], data["body"], data["sender"], data["headers"]])
    stmt = upsert(emails).values(user_id=user, account_id=account, content_hash=content_hash, **data)
    updated = {key: stmt.excluded[key] for key in (*data, "content_hash")}
    conn.execute(stmt.on_conflict_do_update(index_elements=["account_id", "provider_id"], set_=updated))


def thread(conn, message):
    return list(
        reversed(
            [
                dict(row)
                for row in conn.execute(
                    select(emails)
                    .where(
                        emails.c.account_id == message["account_id"],
                        emails.c.thread_id == message["thread_id"],
                    )
                    .order_by(emails.c.received_at.desc(), emails.c.id)
                    .limit(config.THREAD_LIMIT)
                ).mappings()
            ]
        )
    )


def latest(conn, email_id):
    row = find(conn, analyses, analyses.c.email_id == email_id, order=analyses.c.created_at.desc())
    return row["result"] if row else None


def public_draft(row):
    if not row:
        return None
    return {k: row[k] for k in ("id", "email_id", "subject", "body", "version", "status", "updated_at")}


def excerpt(row: dict, limit: int) -> agent.MessageContext:
    return {
        "subject": agent.normalize(row["subject"])[:300],
        "sender": agent.normalize(row["sender"])[:300],
        "body": agent.normalize(row["body"])[:limit],
    }


def get_thread_context(conn, user: str, email_id: str) -> list[agent.MessageContext]:
    message = owned(conn, emails, email_id, user)
    return [excerpt(item, 1600) for item in thread(conn, message) if item["id"] != email_id]


def get_related_tasks(conn, user: str, email_id: str) -> list[str]:
    titles = conn.execute(
        select(tasks.c.title).where(tasks.c.user_id == user, tasks.c.email_id == email_id).limit(8)
    ).scalars()
    return [agent.normalize(title)[:240] for title in titles]


def state(conn, user, email, tone=None):
    account = account_for(conn, user)
    prefs = find(conn, preferences, preferences.c.user_id == user)
    model = "mock"
    if account["provider"] == "gmail" and config.LLM == "openai":
        if not prefs["ai_consent"]:
            fail(403, "ai_consent_required", "Enable AI processing consent in Settings.")
        model = config.MODEL
    if tone:
        prefs["tone"] = tone

    return {
        "message": excerpt(email, config.MAX_BODY),
        "context": get_thread_context(conn, user, email["id"]),
        "preferences": prefs,
        "related_tasks": get_related_tasks(conn, user, email["id"]),
        "model": model,
    }


def analysis_key(message, context):
    # Extracted tasks are a consequence of analysis and should not invalidate its cache.
    inputs = {key: context[key] for key in ("context", "preferences", "model")}
    return digest([message["content_hash"], inputs])


def analyze(user, email_id):
    rate(user, "analysis", 18)
    with engine.begin() as conn:
        message = owned(conn, emails, email_id, user)
        context = state(conn, user, message)
        cached = conn.execute(
            select(runs.c.id)
            .join(analyses, analyses.c.run_id == runs.c.id)
            .where(
                runs.c.user_id == user,
                runs.c.email_id == email_id,
                runs.c.model == context["model"],
                runs.c.prompt_version == agent.VERSION,
                runs.c.status == "succeeded",
                analyses.c.content_hash == analysis_key(message, context),
            )
            .order_by(runs.c.started_at.desc())
            .limit(1)
        ).scalar()
        if cached:
            return {"run_id": cached, "status": "succeeded", "cached": True}
        run_id = uid()
        conn.execute(
            runs.insert().values(
                id=run_id,
                user_id=user,
                email_id=email_id,
                status="running",
                model=context["model"],
                prompt_version=agent.VERSION,
            )
        )
    try:
        result = agent.workflow().invoke(context, config={"recursion_limit": 5})
        classification = result["analysis"]
        try:
            sender = recipient(message["sender"])
        except ProviderError:
            sender = ""
        vip = sender.lower() in context["preferences"]["vip_senders"]
        score = agent.priority(
            classification.importance, classification.urgency, classification.reply_required, vip
        )
        follow_up = (
            (datetime.now(UTC) + timedelta(days=context["preferences"]["follow_up_days"])).isoformat()
            if classification.follow_up_suggested
            else None
        )
        data = {
            **classification.model_dump(mode="json"),
            "priority_score": score,
            "priority_band": agent.band(score),
            "vip": vip,
            "follow_up_at": follow_up,
        }
        with engine.begin() as conn:
            conn.execute(
                analyses.insert().values(
                    email_id=email_id,
                    run_id=run_id,
                    content_hash=analysis_key(message, context),
                    result=data,
                )
            )
            for task in classification.tasks:
                key = digest(
                    [
                        message["thread_id"],
                        " ".join(task.title.lower().split()),
                        task.due_at.isoformat() if task.due_at else task.date_text,
                    ]
                )
                conn.execute(
                    upsert(tasks)
                    .values(
                        user_id=user,
                        email_id=email_id,
                        title=task.title,
                        due_at=task.due_at.isoformat() if task.due_at else None,
                        original_date_text=task.date_text,
                        follow_up_at=follow_up,
                        dedupe_key=key,
                    )
                    .on_conflict_do_nothing(index_elements=["user_id", "dedupe_key"])
                )
            if follow_up and not classification.tasks:
                key = digest([message["thread_id"], "follow_up"])
                conn.execute(
                    upsert(tasks)
                    .values(
                        user_id=user,
                        email_id=email_id,
                        title="Follow up on this conversation",
                        follow_up_at=follow_up,
                        dedupe_key=key,
                    )
                    .on_conflict_do_nothing(index_elements=["user_id", "dedupe_key"])
                )
            if result.get("draft") and not find(
                conn, drafts, drafts.c.user_id == user, drafts.c.email_id == email_id
            ):
                conn.execute(
                    drafts.insert().values(
                        user_id=user, email_id=email_id, **result["draft"].model_dump()
                    )
                )
            conn.execute(
                runs.update()
                .where(runs.c.id == run_id)
                .values(status="succeeded", completed_at=now(), usage=result.get("usage"))
            )
            record(conn, user, "Email analyzed", email_id, "succeeded", run_id=run_id)
    except Exception:  # noqa: BLE001
        with engine.begin() as conn:
            conn.execute(
                runs.update()
                .where(runs.c.id == run_id)
                .values(status="failed", completed_at=now(), error_code="analysis_failed")
            )
            record(
                conn, user, "Email analyzed", email_id, "failed", run_id=run_id, code="analysis_failed"
            )
        return {"run_id": run_id, "status": "failed", "error_code": "analysis_failed"}
    return {"run_id": run_id, "status": "succeeded", "cached": False}


def draft_reply(user, email_id, tone):
    rate(user, "draft", 10)
    with engine.connect() as conn:
        message = owned(conn, emails, email_id, user)
        context = state(conn, user, message, tone)
    try:
        content = agent.write_draft(context)["draft"].model_dump()
    except Exception:  # noqa: BLE001
        fail(502, "draft_failed", "The draft could not be generated. Try again.")
    with engine.begin() as conn:
        draft_id = uid()
        conn.execute(drafts.insert().values(id=draft_id, user_id=user, email_id=email_id, **content))
        record(conn, user, "Draft generated", draft_id, "succeeded")
        return public_draft(owned(conn, drafts, draft_id, user))


def save_draft(user, draft_id, edit):
    with engine.begin() as conn:
        draft = owned(conn, drafts, draft_id, user)
        claimed = conn.execute(
            drafts.update()
            .where(
                drafts.c.id == draft_id, drafts.c.version == edit.version, drafts.c.status == "editing"
            )
            .values(**edit.model_dump(exclude={"version"}), version=edit.version + 1, updated_at=now())
        )
        if not claimed.rowcount:
            fail(409, "stale_draft", "This draft changed or is being sent. Reload it.")
        conn.execute(
            approvals.update()
            .where(approvals.c.draft_id == draft_id, approvals.c.status == "pending")
            .values(status="superseded", decided_at=now())
        )
        record(conn, user, "Draft edited", draft["id"], "succeeded")
        return public_draft(owned(conn, drafts, draft_id, user))


def approval_view(row):
    payload = {k: v for k, v in row["payload"].items() if k != "headers"}
    return {**row, "payload": payload}


def propose(user, email_id=None, draft_id=None, action="send"):
    with engine.begin() as conn:
        account = account_for(conn, user)
        if draft_id:
            draft = owned(conn, drafts, draft_id, user)
            email_id = draft["email_id"]
        message = owned(conn, emails, email_id, user)
        payload = {
            "account_id": account["id"],
            "email_id": email_id,
            "provider_id": message["provider_id"],
            "thread_id": message["thread_id"],
            "from": account["address"],
            "subject": message["subject"],
            "content_hash": message["content_hash"],
        }
        if draft_id:
            if draft["status"] != "editing":
                fail(409, "draft_unavailable", "This draft is unavailable for sending.")
            if re.search(
                r"\[[^\]\n]+\]|\{\{[^}]+\}\}|<[^>\n]*(?:insert|placeholder)[^>\n]*>",
                draft["body"] + "\n" + draft["subject"],
                re.IGNORECASE,
            ):
                fail(
                    422, "unresolved_placeholder", "Resolve all placeholders before requesting approval."
                )
            try:
                to = recipient(message["headers"].get("Reply-To") or message["sender"])
            except ProviderError:
                fail(422, "invalid_recipient", "The original message has no single valid reply address.")
            if account["provider"] == "gmail":
                original = re.sub(r"^(?:re:\s*)+", "", message["subject"], flags=re.IGNORECASE)
                edited = re.sub(r"^(?:re:\s*)+", "", draft["subject"], flags=re.IGNORECASE)
                if original.casefold() != edited.casefold():
                    fail(
                        422,
                        "thread_subject_mismatch",
                        "Keep the original subject for a threaded Gmail reply.",
                    )
            payload.update(
                to=to,
                subject=draft["subject"],
                body=draft["body"],
                version=draft["version"],
                headers=message["headers"],
            )
        else:
            payload["changes"] = (
                {"removeLabelIds": ["INBOX"]} if action == "archive" else {"addLabelIds": ["STARRED"]}
            )
        signature = digest(payload)
        idem = digest([action, draft_id, signature])
        proposal_id = uid()
        stmt = upsert(approvals).values(
            id=proposal_id,
            user_id=user,
            email_id=email_id,
            draft_id=draft_id,
            action_type=action,
            payload=payload,
            payload_hash=signature,
            idempotency_key=idem,
        )
        inserted = conn.execute(
            stmt.on_conflict_do_nothing(index_elements=["user_id", "idempotency_key"])
        )
        if inserted.rowcount:
            record(conn, user, f"{action.title()} proposed", proposal_id, "pending")
        row = find(conn, approvals, approvals.c.user_id == user, approvals.c.idempotency_key == idem)
        return approval_view(row)


def recover_executing(conn, user):
    cutoff = (datetime.now(UTC) - timedelta(minutes=2)).isoformat()
    stale = conn.execute(
        approvals.update()
        .where(
            approvals.c.user_id == user,
            approvals.c.status == "executing",
            approvals.c.decided_at < cutoff,
        )
        .values(status="unknown", error_code="execution_interrupted", executed_at=now())
        .returning(approvals.c.id, approvals.c.draft_id)
    ).all()
    for ident, draft_id in stale:
        if draft_id:
            conn.execute(
                drafts.update()
                .where(drafts.c.id == draft_id, drafts.c.status == "executing")
                .values(status="unknown")
            )
        record(conn, user, "Execution interrupted", ident, "unknown", code="execution_interrupted")


def decide(user, ident, reviewed_hash, approved):
    with engine.begin() as conn:
        account = lock_account(conn, user) if approved else None
        item = owned(conn, approvals, ident, user)
        payload = item["payload"]
        if reviewed_hash != item["payload_hash"] or digest(payload) != reviewed_hash:
            fail(409, "approval_changed", "Review the latest approval payload.")
        if item["status"] != "pending":
            fail(409, "approval_closed", "This approval is no longer pending.")
        if approved:
            target = owned(conn, emails, item["email_id"], user)
            if (
                account["id"] != payload["account_id"]
                or target["content_hash"] != payload["content_hash"]
            ):
                fail(409, "target_changed", "The target changed. Create a new proposal.")
            if account["status"] != "connected":
                fail(403, "disconnected", "Reconnect the email account first.")
            scopes = {SEND, MODIFY} if item["action_type"] == "send" else {MODIFY}
            if account["provider"] == "gmail" and not scopes.intersection(account["scopes"]):
                fail(403, "missing_scope", "Grant the required permission in Settings.")
            if item["draft_id"]:
                locked = conn.execute(
                    drafts.update()
                    .where(
                        drafts.c.id == item["draft_id"],
                        drafts.c.version == payload["version"],
                        drafts.c.subject == payload["subject"],
                        drafts.c.body == payload["body"],
                        drafts.c.status == "editing",
                    )
                    .values(status="executing")
                )
                if not locked.rowcount:
                    fail(409, "stale_draft", "The draft has changed. Review a new proposal.")
        status = "executing" if approved else "rejected"
        changed = conn.execute(
            approvals.update()
            .where(
                approvals.c.id == ident,
                approvals.c.status == "pending",
                approvals.c.payload_hash == reviewed_hash,
            )
            .values(status=status, decided_at=now())
        )
        if not changed.rowcount:
            fail(409, "approval_closed", "This approval is no longer pending.")
        record(
            conn,
            user,
            f"{item['action_type'].title()} {'approved' if approved else 'rejected'}",
            ident,
            status,
        )
        if not approved:
            return approval_view(owned(conn, approvals, ident, user))
    provider_id, error = None, None
    try:
        adapter = provider(account)
        provider_id = (
            adapter.send_reply(payload)
            if item["action_type"] == "send"
            else adapter.modify_labels(payload)
        )
        status = "succeeded"
    except ProviderError as exc:
        status, error = ("unknown" if exc.unknown else "failed"), exc.code
    except Exception:  # noqa: BLE001
        status, error = "unknown", "execution_interrupted"
    with engine.begin() as conn:
        updated = conn.execute(
            approvals.update()
            .where(approvals.c.id == ident, approvals.c.status == "executing")
            .values(status=status, executed_at=now(), provider_id=provider_id, error_code=error)
        )
        if updated.rowcount:
            if item["draft_id"]:
                conn.execute(
                    drafts.update()
                    .where(drafts.c.id == item["draft_id"])
                    .values(
                        status="sent"
                        if status == "succeeded"
                        else "unknown"
                        if status == "unknown"
                        else "editing"
                    )
                )
            if status == "succeeded" and item["action_type"] != "send":
                message = owned(conn, emails, item["email_id"], user)
                labels = sorted(
                    (set(message["labels"]) | set(payload["changes"].get("addLabelIds", [])))
                    - set(payload["changes"].get("removeLabelIds", []))
                )
                conn.execute(emails.update().where(emails.c.id == message["id"]).values(labels=labels))
            record(
                conn,
                user,
                f"{item['action_type'].title()} executed",
                ident,
                status,
                provider_id=provider_id,
                code=error,
            )
        return approval_view(owned(conn, approvals, ident, user))
