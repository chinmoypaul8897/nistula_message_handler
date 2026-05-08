from uuid import UUID

import pytest
from pydantic import ValidationError

from src.models import (
    ClaudeReplyOutput,
    InboundWebhook,
    UnifiedMessage,
)
from src.property_context import format_for_prompt


# Brief's example payload (PLAN S9 case 1).
BRIEF_EXAMPLE = {
    "source": "whatsapp",
    "guest_name": "Rahul Sharma",
    "message": "Is the villa available from April 20 to 24? What is the rate for 2 adults?",
    "timestamp": "2026-05-05T10:30:00Z",
    "booking_ref": "NIS-2024-0891",
    "property_id": "villa-b1",
}


def test_inbound_webhook_accepts_brief_example():
    inbound = InboundWebhook.model_validate(BRIEF_EXAMPLE)
    assert inbound.source == "whatsapp"
    assert inbound.guest_name == "Rahul Sharma"
    assert inbound.timestamp.tzinfo is not None
    assert inbound.booking_ref == "NIS-2024-0891"


def test_inbound_webhook_rejects_invalid_source():
    payload = {**BRIEF_EXAMPLE, "source": "email"}
    with pytest.raises(ValidationError):
        InboundWebhook.model_validate(payload)


def test_inbound_webhook_rejects_malformed_timestamp():
    payload = {**BRIEF_EXAMPLE, "timestamp": "not-a-date"}
    with pytest.raises(ValidationError):
        InboundWebhook.model_validate(payload)


def test_inbound_webhook_rejects_naive_timestamp():
    # Naive datetime would silently drift by 5h30m when is_after_hours converts to IST.
    payload = {**BRIEF_EXAMPLE, "timestamp": "2026-05-05T10:30:00"}
    with pytest.raises(ValidationError):
        InboundWebhook.model_validate(payload)


def test_unified_message_from_inbound_generates_uuid_and_renames_message():
    inbound = InboundWebhook.model_validate(BRIEF_EXAMPLE)
    unified = UnifiedMessage.from_inbound(inbound)
    assert isinstance(unified.message_id, UUID)
    assert unified.message_text == BRIEF_EXAMPLE["message"]
    assert unified.query_type is None  # assigned later by the Claude call


def test_unified_message_uuids_are_unique_across_calls():
    # Guards against the classic default= vs default_factory= mistake.
    inbound = InboundWebhook.model_validate(BRIEF_EXAMPLE)
    a = UnifiedMessage.from_inbound(inbound)
    b = UnifiedMessage.from_inbound(inbound)
    assert a.message_id != b.message_id


def test_claude_reply_output_validates_required_fields():
    valid = {
        "query_type": "pre_sales_availability",
        "drafted_reply": "Hi Rahul, the villa is available those nights.",
        "classification_confidence": 0.92,
        "context_sufficient": True,
        "missing_information": [],
        "reasoning": "Date range matches the locked availability window.",
    }
    out = ClaudeReplyOutput.model_validate(valid)
    assert out.query_type == "pre_sales_availability"
    assert out.classification_confidence == 0.92

    # Missing 'reasoning' must fail.
    incomplete = {k: v for k, v in valid.items() if k != "reasoning"}
    with pytest.raises(ValidationError):
        ClaudeReplyOutput.model_validate(incomplete)


def test_format_for_prompt_includes_wifi_and_rate_and_checkin():
    block = format_for_prompt()
    assert "Nistula@2024" in block          # wifi password
    assert "18000" in block                  # base rate INR
    assert "14:00" in block                  # check-in time
