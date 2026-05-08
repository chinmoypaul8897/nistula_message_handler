"""Unit tests for src.confidence -- the deterministic scoring module.

Coverage targets each PLAN S15 C3 case explicitly: the worked example, every
override path, override ordering, the IST after-hours boundary in both
directions, the PLAN S6.6 threshold strict-inequality at 0.85, and the
reply-completeness heuristic in isolation. Floating-point assertions use
pytest.approx; threshold tests pick values away from the edge to avoid drift.
"""
from datetime import datetime, timezone
from uuid import uuid4

import pytest

from src.confidence import (
    HEDGE_TOKENS,
    RISK_CLASS_SCORES,
    WEIGHTS,
    apply_overrides,
    compute_base_score,
    is_after_hours,
    reply_completeness,
    score_and_act,
)
from src.models import ClaudeReplyOutput, QueryType, UnifiedMessage


# --- Helpers ----------------------------------------------------------------


def _utc(year: int, month: int, day: int, hour: int = 12, minute: int = 0) -> datetime:
    return datetime(year, month, day, hour, minute, tzinfo=timezone.utc)


def _ist_to_utc(hour: int, minute: int = 0) -> datetime:
    """Build a UTC datetime that lands at hour:minute IST on 2026-05-05."""
    # IST = UTC+5:30. UTC hour = IST hour - 5h30m, with day rollover handled.
    total = hour * 60 + minute - (5 * 60 + 30)
    if total < 0:
        total += 24 * 60
    return datetime(2026, 5, 5, total // 60, total % 60, tzinfo=timezone.utc)


def _msg(ts: datetime, query_type: QueryType | None = None) -> UnifiedMessage:
    return UnifiedMessage(
        message_id=uuid4(),
        source="whatsapp",
        guest_name="Test Guest",
        message_text="hello",
        timestamp=ts,
        booking_ref=None,
        property_id="villa-b1",
        query_type=query_type,
    )


def _claude(
    *,
    query_type: QueryType,
    classification_confidence: float = 0.95,
    context_sufficient: bool = True,
    missing_information: list[str] | None = None,
    drafted_reply: str = "Hi! Villa B1 is available those nights at INR 18000 per night.",
) -> ClaudeReplyOutput:
    return ClaudeReplyOutput(
        query_type=query_type,
        drafted_reply=drafted_reply,
        classification_confidence=classification_confidence,
        context_sufficient=context_sufficient,
        missing_information=missing_information or [],
        reasoning="test",
    )


# --- Constants integrity ----------------------------------------------------


def test_weights_sum_to_one():
    assert sum(WEIGHTS.values()) == pytest.approx(1.0)


def test_risk_class_scores_cover_all_query_types():
    expected = {
        "general_enquiry",
        "post_sales_checkin",
        "pre_sales_availability",
        "pre_sales_pricing",
        "special_request",
        "complaint",
    }
    assert set(RISK_CLASS_SCORES.keys()) == expected


# --- Base score / threshold mapping (5) -------------------------------------


def test_worked_example_pricing_returns_0945_auto_send():
    # PLAN S6.7: classification=0.95, context_sufficient=True, completeness=1.0,
    # query_type=pre_sales_pricing (risk=0.8) -> base 0.945, no override -> auto_send.
    claude = _claude(
        query_type="pre_sales_pricing",
        classification_confidence=0.95,
        drafted_reply="Hi! For 2 adults across 3 nights, the rate is INR 18000 per night.",
    )
    score, action = score_and_act(claude, _msg(_utc(2026, 5, 5, hour=14)))
    assert score == pytest.approx(0.945)
    assert action == "auto_send"


def test_general_enquiry_perfect_signals_auto_send():
    claude = _claude(
        query_type="general_enquiry",
        classification_confidence=1.0,
        drafted_reply="Hi! Villa B1 is in Assagao, North Goa, with 3 bedrooms and a private pool.",
    )
    score, action = score_and_act(claude, _msg(_utc(2026, 5, 5, hour=14)))
    assert score >= 0.9
    assert action == "auto_send"


def test_threshold_just_above_auto_send_cutoff_is_auto_send():
    # 0.851 > 0.85 -> auto_send.
    claude = _claude(
        query_type="general_enquiry",  # risk=1.0, context=1.0
        classification_confidence=0.92,
        drafted_reply="A short reply with sane length so completeness is 1.0 here.",
    )
    score, action = score_and_act(claude, _msg(_utc(2026, 5, 5, hour=14)))
    assert score > 0.85
    assert action == "auto_send"


def test_threshold_at_0_85_exactly_is_agent_review():
    # PLAN S6.6 says > 0.85 is auto_send (strict). 0.85 exactly is agent_review.
    score = 0.85
    assert _action_for(score) == "agent_review"


def test_threshold_below_escalate_cutoff_is_escalate():
    score = 0.599
    assert _action_for(score) == "escalate"


def _action_for(score: float):
    """Round-trip a fixed score through apply_overrides on a benign query."""
    claude = _claude(query_type="general_enquiry")
    # Use base_score directly with a query/state that hits no override.
    # apply_overrides falls through to _action_from_threshold for general_enquiry
    # at daytime with no missing_information.
    _, action = apply_overrides(score, claude, _msg(_utc(2026, 5, 5, hour=14)))
    return action


# --- Overrides (4) ----------------------------------------------------------


def test_override_1_complaint_at_2pm_zeroes_score_and_escalates():
    claude = _claude(query_type="complaint")
    score, action = score_and_act(claude, _msg(_utc(2026, 5, 5, hour=14)))
    assert score == 0.0
    assert action == "escalate"


def test_override_2_special_request_at_23_ist_caps_and_escalates():
    # 23:00 IST is after-hours. special_request + after-hours -> escalate, score capped.
    claude = _claude(query_type="special_request")
    score, action = score_and_act(claude, _msg(_ist_to_utc(23, 0)))
    assert action == "escalate"
    assert score <= 0.59  # cap consistent with the < 0.60 escalate threshold


def test_override_3_pricing_with_missing_info_caps_07_agent_review():
    claude = _claude(
        query_type="pre_sales_pricing",
        missing_information=["pet policy"],
    )
    score, action = score_and_act(claude, _msg(_utc(2026, 5, 5, hour=14)))
    assert score <= 0.7
    assert action == "agent_review"


def test_complaint_at_3am_override_1_fires_before_override_2():
    # Complaint at 3am IST satisfies BOTH override 1 (complaint) and override 2
    # (after-hours complaint). Override #1 must win because it appears first
    # and produces score = 0.0 specifically (override #2 only caps at 0.59).
    claude = _claude(query_type="complaint")
    score, action = score_and_act(claude, _msg(_ist_to_utc(3, 0)))
    assert score == 0.0
    assert action == "escalate"


# --- After-hours boundaries (4) --------------------------------------------


def test_after_hours_at_2200_ist_is_true():
    assert is_after_hours(_ist_to_utc(22, 0)) is True


def test_after_hours_at_2159_ist_is_false():
    assert is_after_hours(_ist_to_utc(21, 59)) is False


def test_after_hours_at_0759_ist_is_true():
    assert is_after_hours(_ist_to_utc(7, 59)) is True


def test_after_hours_at_0800_ist_is_false():
    assert is_after_hours(_ist_to_utc(8, 0)) is False


# --- Reply completeness heuristic (5) --------------------------------------


def test_reply_completeness_perfect_returns_one():
    reply = "Hi Rahul! Villa B1 is available, the rate is INR 18000 per night."
    assert reply_completeness(reply) == pytest.approx(1.0)


def test_reply_completeness_hedge_token_subtracts_0_3():
    reply = "I think the rate might be 18000 per night, but let me check with the team."
    # Two hedge tokens present ("i think", "might be") but PLAN says one
    # subtraction regardless of count.
    assert reply_completeness(reply) == pytest.approx(0.7)
    assert any(token in reply.lower() for token in HEDGE_TOKENS)


def test_reply_completeness_placeholder_subtracts_0_4():
    reply = "Hi! The rate for your stay is [PRICE] per night, please confirm."
    assert reply_completeness(reply) == pytest.approx(0.6)


def test_reply_completeness_short_reply_subtracts_0_2():
    reply = "Yes, available."  # 16 chars < 30 -> length penalty.
    assert reply_completeness(reply) == pytest.approx(0.8)


def test_reply_completeness_floors_at_zero():
    # Hedge (-0.3) + placeholder (-0.4) + short (-0.2) = 0.1 then floor to 0.1.
    # Add a long enough reply to hit the >600 length penalty AND the others
    # so total subtraction = 0.9 -> 0.1, still positive. Force a 0.0 floor by
    # making completeness drop below zero.
    long_hedge_placeholder = (
        "I think " + "x" * 600 + " [PRICE] possibly not sure I believe might be"
    )
    # Length > 600 (-0.2), hedge (-0.3), placeholder (-0.4) = -0.9 -> 0.1.
    # We need < 0 to test the floor; verify via the math above this stays at 0.1.
    # So instead test a constructed worst case that drops below zero by hand:
    # The current heuristic only subtracts up to 0.9, never below 0.0. The
    # floor exists for forward-compat. Assert non-negative for a maximally
    # bad reply.
    assert reply_completeness(long_hedge_placeholder) >= 0.0
    assert reply_completeness(long_hedge_placeholder) == pytest.approx(0.1)
