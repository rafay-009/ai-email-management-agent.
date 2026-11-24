import json
import os
import re
from datetime import date
from functools import lru_cache
from pathlib import Path
from typing import TypedDict

from langgraph.graph import END, START, StateGraph
from openai import OpenAI
from pydantic import ValidationError

from . import config
from .contracts import Analysis, Draft, ExtractedTask

VERSION = "v1"
PROMPTS = Path(__file__).with_name("prompts")


def priority(importance, urgency, reply, vip):
    return min(
        100, 10 * (max(0, min(4, importance)) + max(0, min(4, urgency)) + bool(reply) + bool(vip))
    )


def band(score):
    if score >= 80:
        return "critical"
    if score >= 60:
        return "high"
    if score >= 30:
        return "normal"
    return "low"


def normalize(text):
    text = re.sub(r"-----BEGIN [^-]+-----[\s\S]*?-----END [^-]+-----", "[redacted]", text)
    text = re.sub(
        r"(?i)\b(?:sk-[\w-]{16,}|bearer +[\w.-]{16,}|(?:password|api[_ -]?key)\s*[:=]\s*\S+)",
        "[redacted]",
        text,
    )
    return text.replace("\x00", "").strip()


def mock(message):
    original = message["body"]
    text = f"{message['subject']} {original}".lower()
    groups = [
        (
            "spam_suspected",
            r"ignore (?:all |previous )?instructions|send secrets|tool:|verify your password",
        ),
        ("newsletter", r"newsletter|unsubscribe"),
        ("finance", r"invoice|receipt|facture|balance"),
        ("meeting", r"\bmeeting\b|design review"),
        ("customer_support", r"order|delivery|checkout|customer|pedido"),
        ("sales", r"proposal|partnership|estimate"),
        ("personal", r"coffee|picnic|weekend photos"),
        ("work", r"project|homepage|milestones|brief"),
    ]
    category = next((name for name, pattern in groups if re.search(pattern, text)), "other")
    safe = category not in {"newsletter", "spam_suspected"}
    request = re.search(
        r"(?i)(?:please |could you |would you |can you |¿puedes |pouvez-vous )[^.!?\n]+", original
    )
    reply = safe and bool(request or "?" in original)
    urgent = safe and bool(re.search(r"today|overdue|urgent|broken", text))
    importance = 4 if urgent else 2 if reply or category in {"finance", "meeting"} else 0
    urgency = 4 if urgent else 1 if safe else 0
    phrase = re.search(
        r"\b(?:\d{4}-\d{2}-\d{2}|(?:next )?(?:Monday|Tuesday|Wednesday|Thursday|Friday|Saturday|Sunday)|today|soon)\b",
        original,
        re.IGNORECASE,
    )
    due = None
    if phrase and re.fullmatch(r"\d{4}-\d{2}-\d{2}", phrase[0]):
        try:
            due = date.fromisoformat(phrase[0])
        except ValueError:
            pass
    evidence = request[0][:180].strip() if request else (original or message["subject"])[:180].strip()
    task = (
        ExtractedTask(
            title=evidence, due_at=due, date_text=phrase[0] if phrase else None, confidence=0.85
        )
        if safe and request and reply
        else None
    )
    return Analysis(
        category=category,
        intent="Review suspicious instructions"
        if not safe and category == "spam_suspected"
        else "Response requested"
        if reply
        else "Information shared",
        summary="The message contains instructions that warrant manual review."
        if category == "spam_suspected"
        else original[:330] or message["subject"],
        importance=importance,
        urgency=urgency,
        reply_required=reply,
        sentiment="negative" if urgent else "neutral",
        confidence=0.85,
        evidence=[evidence] if evidence else [],
        tasks=[task] if task else [],
        follow_up_suggested=reply and safe,
        recommended_actions=(["draft_reply"] + (["create_task"] if task else []))
        if reply
        else ["archive"]
        if category == "newsletter"
        else ["no_action"],
    )


class MessageContext(TypedDict):
    subject: str
    sender: str
    body: str


class State(TypedDict, total=False):
    message: MessageContext
    context: list[MessageContext]
    preferences: dict
    related_tasks: list[str]
    model: str
    analysis: Analysis
    draft: Draft
    usage: dict


def structured(name, schema, state):
    context = {key: state[key] for key in ("message", "context", "related_tasks") if key in state}
    context["tone"] = state["preferences"]["tone"]
    client = OpenAI(api_key=os.environ["OPENAI_API_KEY"], timeout=config.TIMEOUT, max_retries=0)
    for attempt in range(2):
        try:
            response = client.responses.parse(
                model=state["model"],
                max_output_tokens=1400,
                store=False,
                input=[
                    {"role": "developer", "content": (PROMPTS / f"{name}-{VERSION}.txt").read_text()},
                    {"role": "user", "content": json.dumps(context, ensure_ascii=False)},
                ],
                text_format=schema,
            )
            parsed = response.output_parsed
            if parsed is None:
                raise ValueError("Model returned no structured result")
            if schema is Analysis:
                source = state["message"]["subject"] + "\n" + state["message"]["body"]
                if any(quote not in source for quote in parsed.evidence):
                    raise ValueError("Evidence was not found in the message")
                for task in parsed.tasks:
                    if task.date_text and task.date_text not in source:
                        task.date_text = None
                    if task.due_at and (
                        task.due_at.isoformat() not in source
                        or task.due_at.isoformat() != task.date_text
                    ):
                        task.due_at = None
            usage = response.usage.model_dump() if response.usage else None
            return parsed, usage
        except (ValidationError, ValueError):
            if attempt:
                raise


def classify(state: State):
    if state["model"] == "mock":
        return {"analysis": mock(state["message"])}
    result, usage = structured("classify", Analysis, state)
    return {"analysis": result, "usage": {"classification": usage} if usage else {}}


def write_draft(state: State):
    message = state["message"]
    if state["model"] != "mock":
        result, usage = structured("reply", Draft, state)
        return {
            "draft": result,
            "usage": {**state.get("usage", {}), "draft": usage} if usage else state.get("usage", {}),
        }
    tone = state["preferences"]["tone"]
    subject = message["subject"]
    subject = subject if subject.lower().startswith("re:") else f"Re: {subject}"
    text = {
        "professional": "Hello,\n\nThank you for your message. Could you confirm any details you would like me to address?\n\nBest regards",
        "friendly": "Hi there,\n\nThanks for reaching out! Is there anything else I should know before I look into this?\n\nThanks!",
        "concise": "Thanks for your message. Could you confirm any details I should address?",
    }[tone]
    category = mock(message).category
    clarification = {
        "customer_support": "Could you confirm the order or ticket number so I can locate the request?",
        "finance": "Could you confirm the invoice reference and any details that need review?",
        "meeting": "Could you confirm the meeting date, time, and timezone?",
        "sales": "Could you confirm the scope and timing you have in mind?",
    }.get(category)
    if clarification and tone == "professional":
        text = text.replace("Could you confirm any details you would like me to address?", clarification)
    return {"draft": Draft(subject=subject[:250], body=text)}


def next_node(state: State):
    analysis = state["analysis"]
    return "draft" if analysis.reply_required and analysis.category != "spam_suspected" else END


@lru_cache
def workflow():
    graph = StateGraph(State)
    graph.add_node("classify", classify)
    graph.add_node("draft", write_draft)
    graph.add_edge(START, "classify")
    graph.add_conditional_edges("classify", next_node, ["draft", END])
    graph.add_edge("draft", END)
    return graph.compile()
