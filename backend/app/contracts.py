from datetime import date
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints

Category = Literal[
    "customer_support",
    "sales",
    "work",
    "finance",
    "meeting",
    "personal",
    "newsletter",
    "spam_suspected",
    "other",
]
Tone = Literal["professional", "friendly", "concise"]
Action = Literal["draft_reply", "create_task", "archive", "label", "no_action"]
Score = Annotated[int, Field(ge=0, le=4, strict=True)]
Confidence = Annotated[float, Field(ge=0, le=1)]
Subject = Annotated[
    str, StringConstraints(strip_whitespace=True, min_length=1, max_length=250, pattern=r"^[^\r\n]+$")
]
Body = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=8000)]
Short = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=240)]


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ExtractedTask(Strict):
    title: Short
    due_at: date | None
    date_text: Short | None
    confidence: Confidence


class Analysis(Strict):
    category: Category
    intent: Short
    summary: Annotated[str, StringConstraints(min_length=1, max_length=600)]
    importance: Score
    urgency: Score
    reply_required: bool
    sentiment: Literal["positive", "neutral", "negative", "uncertain"]
    confidence: Confidence
    evidence: list[Short] = Field(max_length=5)
    tasks: list[ExtractedTask] = Field(max_length=8)
    follow_up_suggested: bool
    recommended_actions: list[Action] = Field(max_length=5)


class Draft(Strict):
    subject: Subject
    body: Body


class DraftEdit(Draft):
    version: int = Field(ge=1)


class DraftRequest(Strict):
    tone: Tone | None = None


class Batch(Strict):
    ids: list[str] = Field(min_length=1, max_length=10)


class Decision(Strict):
    payload_hash: str = Field(pattern=r"^[a-f0-9]{64}$")


class Proposal(Strict):
    action: Literal["archive", "label"]


class TaskEdit(Strict):
    status: Literal["open", "completed"] | None = None
    due_at: date | None = None


class Preferences(Strict):
    timezone: str | None = Field(default=None, max_length=100)
    tone: Tone | None = None
    vip_senders: list[Annotated[str, StringConstraints(max_length=254)]] | None = Field(
        default=None, max_length=30
    )
    follow_up_days: int | None = Field(default=None, ge=1, le=30)
    ai_consent: bool | None = None


class EmailEdit(Strict):
    unread: bool | None = None
    priority_override: int | None = Field(default=None, ge=0, le=100)


class DeleteData(Strict):
    confirm: Literal["DELETE"]
