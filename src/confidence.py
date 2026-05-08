"""Confidence scoring + action mapping for the Nistula message handler.

A pure function of inputs: every (score, action) pair is fully reproducible
from the ClaudeReplyOutput and UnifiedMessage that produced it. No LLM call,
no I/O, no global state. PLAN.md S3.2 puts the numeric score in deterministic
Python (not in Claude) so the logic is auditable, tunable in one place, and
reviewable as a code artifact.

Three layers, evaluated in order:
  1. compute_base_score   -- 4-factor weighted sum (PLAN S6.2).
  2. apply_overrides      -- 3 hard rules in defined order (PLAN S6.3).
  3. _action_from_threshold -- bucket the score when no override fired
                               (PLAN S6.6).

score_and_act is the orchestrator C4 imports.
"""
import re
from datetime import datetime, timedelta, timezone

from src.models import ActionType, ClaudeReplyOutput, QueryType, UnifiedMessage


RISK_CLASS_SCORES: dict[QueryType, float] = {
    "general_enquiry": 1.0,
    "post_sales_checkin": 1.0,
    "pre_sales_availability": 0.9,
    "pre_sales_pricing": 0.8,
    "special_request": 0.6,
    "complaint": 0.0,
}

WEIGHTS: dict[str, float] = {
    "classification": 0.30,
    "context": 0.30,
    "completeness": 0.20,
    "risk": 0.20,
}

# Hedge tokens stored lowercased; matched case-insensitively against the reply
# (PLAN S6.4 says "contains hedge tokens" -- substring semantics).
HEDGE_TOKENS: list[str] = [
    "i think",
    "not sure",
    "might be",
    "possibly",
    "i believe",
]

# Unfilled placeholder patterns like [PRICE], [DATE], [GUEST_NAME]. Per PLAN S6.4.
_PLACEHOLDER_RE = re.compile(r"\[[A-Z_]+\]")

# IST = UTC+5:30, fixed offset (India does not observe DST). Matches the
# caretaker hours from PLAN S8 (08:00-22:00 IST). Using a fixed-offset
# timezone instead of ZoneInfo("Asia/Kolkata") keeps the module dependency-
# free and avoids Windows requiring the tzdata package as an indirect runtime
# dependency.
_IST = timezone(timedelta(hours=5, minutes=30), name="IST")

# Reply length sanity bounds (PLAN S6.4) and after-hours window (PLAN S6.3 #2).
_REPLY_MIN_CHARS = 30
_REPLY_MAX_CHARS = 600
_AFTER_HOURS_START = 22  # IST hour (inclusive)
_AFTER_HOURS_END = 8  # IST hour (exclusive)

# Score caps for overrides #2 and #3 (PLAN S6.3; #2 cap chosen to keep the
# returned (score, action) pair consistent with the S6.6 threshold table).
_OVERRIDE_2_CAP = 0.59
_OVERRIDE_3_CAP = 0.7

# Action thresholds (PLAN S6.6). > 0.85 is strict; 0.85 exactly maps to
# agent_review.
_AUTO_SEND_THRESHOLD = 0.85
_AGENT_REVIEW_THRESHOLD = 0.60


def is_after_hours(ts: datetime) -> bool:
    """Return True iff the timestamp falls in the IST after-hours window.

    After hours = 22:00 (inclusive) to 08:00 (exclusive) IST. Caretaker is on
    duty during the complementary window per PLAN S8.

    Naive datetimes are not accepted. InboundWebhook rejects them at the
    boundary (see src/models.py), so any datetime reaching this function
    should already be timezone-aware. We assert that and let an AttributeError
    surface loudly rather than silently treat a naive datetime as UTC.
    """
    assert ts.tzinfo is not None, "is_after_hours requires a timezone-aware datetime"
    hour = ts.astimezone(_IST).hour
    return hour >= _AFTER_HOURS_START or hour < _AFTER_HOURS_END


def reply_completeness(reply: str) -> float:
    """Score the drafted reply on a 0..1 scale per PLAN S6.4.

    Start at 1.0; subtract once for each category that triggers (regardless
    of how many hedge tokens or placeholders are present). Floor at 0.0.
    """
    score = 1.0
    lowered = reply.lower()
    if any(token in lowered for token in HEDGE_TOKENS):
        score -= 0.3
    if _PLACEHOLDER_RE.search(reply):
        score -= 0.4
    if len(reply) < _REPLY_MIN_CHARS or len(reply) > _REPLY_MAX_CHARS:
        score -= 0.2
    return max(0.0, score)


def compute_base_score(claude_output: ClaudeReplyOutput) -> float:
    """4-factor weighted base score per PLAN S6.2.

    No overrides applied here. context_sufficiency is quantized: 1.0 when
    True, 0.3 when False (PLAN S6.2). risk_class_score comes from the lookup
    table in PLAN S6.5.
    """
    classification = claude_output.classification_confidence
    context = 1.0 if claude_output.context_sufficient else 0.3
    completeness = reply_completeness(claude_output.drafted_reply)
    risk = RISK_CLASS_SCORES[claude_output.query_type]

    return (
        WEIGHTS["classification"] * classification
        + WEIGHTS["context"] * context
        + WEIGHTS["completeness"] * completeness
        + WEIGHTS["risk"] * risk
    )


def apply_overrides(
    base_score: float,
    claude_output: ClaudeReplyOutput,
    message: UnifiedMessage,
) -> tuple[float, ActionType]:
    """Apply the three PLAN S6.3 hard overrides in order; first match wins.

    If no override fires, fall through to the threshold mapping in
    _action_from_threshold so this function always returns a concrete
    ActionType (not Optional). Override score behavior:
      #1 complaint                 -> score = 0.0, action = escalate
      #2 after-hours complaint or  -> score = min(base, 0.59), action = escalate
         special_request              (the cap keeps the returned score < the
                                       0.60 escalate threshold so audit reads
                                       cleanly)
      #3 pricing + missing_info    -> score = min(base, 0.7), action = agent_review
    """
    qt = claude_output.query_type

    # #1: complaints always need a human, no matter how confident the model is.
    if qt == "complaint":
        return 0.0, "escalate"

    # #2: after-hours requests we cannot silently auto-handle. Caretaker is
    # off-duty (PLAN S8 caretaker_hours = 08:00-22:00 IST), so any complaint
    # or special_request waits for an on-call human.
    if qt in ("complaint", "special_request") and is_after_hours(message.timestamp):
        return min(base_score, _OVERRIDE_2_CAP), "escalate"

    # #3: never auto-send a wrong price.
    if qt == "pre_sales_pricing" and claude_output.missing_information:
        return min(base_score, _OVERRIDE_3_CAP), "agent_review"

    return base_score, _action_from_threshold(base_score)


def _action_from_threshold(score: float) -> ActionType:
    """Map a final score to an action per PLAN S6.6.

    > 0.85          -> auto_send       (strict; 0.85 exactly is agent_review)
    0.60..0.85      -> agent_review
    < 0.60          -> escalate
    """
    if score > _AUTO_SEND_THRESHOLD:
        return "auto_send"
    if score >= _AGENT_REVIEW_THRESHOLD:
        return "agent_review"
    return "escalate"


def score_and_act(
    claude_output: ClaudeReplyOutput,
    message: UnifiedMessage,
) -> tuple[float, ActionType]:
    """Public entry point: compute the base score, apply overrides, return."""
    base = compute_base_score(claude_output)
    return apply_overrides(base, claude_output, message)
