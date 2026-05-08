"""Pydantic v2 models that define the unified message schema.

The schema is the contract: every channel webhook validates against InboundWebhook,
the internal pipeline operates on UnifiedMessage, the Claude tool-use call returns
ClaudeReplyOutput, and the HTTP response is EndpointResponse. No business logic
lives here -- per PLAN.md S15 C1, models are shape and validation only.
"""
from datetime import datetime
from typing import Literal, Self
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field, field_validator


SourceChannel = Literal["whatsapp", "booking_com", "airbnb", "instagram", "direct"]

QueryType = Literal[
    "pre_sales_availability",
    "pre_sales_pricing",
    "post_sales_checkin",
    "special_request",
    "complaint",
    "general_enquiry",
]

ActionType = Literal["auto_send", "agent_review", "escalate"]


class InboundWebhook(BaseModel):
    """Raw webhook payload from any guest-facing channel.

    extra="ignore" because channels send heterogeneous payloads (WhatsApp adds
    its own message IDs, Booking.com adds reservation metadata, etc.). Forbidding
    extras would 422 on every payload that does not exactly match. The schema
    captures only the fields the pipeline operates on; the full original payload
    is preserved separately as messages.raw_payload (PLAN S10.3) for debugging.
    """

    model_config = ConfigDict(extra="ignore", str_strip_whitespace=True)

    source: SourceChannel
    guest_name: str
    message: str
    timestamp: datetime
    booking_ref: str | None = None
    property_id: str

    @field_validator("timestamp")
    @classmethod
    def _require_timezone(cls, v: datetime) -> datetime:
        # Naive datetimes are rejected: is_after_hours converts to IST in C3,
        # and a naive timestamp would silently drift by 5h30m and corrupt the
        # complaint/special_request after-hours override.
        if v.tzinfo is None:
            raise ValueError(
                "timestamp must include timezone info (use ISO-8601 with 'Z' or an offset)"
            )
        return v


class UnifiedMessage(BaseModel):
    """Internal normalized representation of a guest message.

    Generated from InboundWebhook via from_inbound. Carries a server-assigned
    message_id (UUID) so error responses can correlate even when the Claude call
    fails. query_type is None on construction and assigned after the Claude call.
    """

    model_config = ConfigDict(extra="forbid")

    message_id: UUID = Field(default_factory=uuid4)
    source: SourceChannel
    guest_name: str
    message_text: str
    timestamp: datetime
    booking_ref: str | None = None
    property_id: str
    query_type: QueryType | None = None

    @classmethod
    def from_inbound(cls, inbound: InboundWebhook) -> Self:
        return cls(
            source=inbound.source,
            guest_name=inbound.guest_name,
            message_text=inbound.message,
            timestamp=inbound.timestamp,
            booking_ref=inbound.booking_ref,
            property_id=inbound.property_id,
        )


class ClaudeReplyOutput(BaseModel):
    """Structured output returned by the Claude tool-use call.

    Mirrors the locked tool input_schema in PLAN S7.2. extra="forbid" because
    we own this contract end-to-end -- any deviation means the SDK shape changed
    or the tool definition drifted, and we want the endpoint layer to surface
    that as HTTP 502 (PLAN S7.5) rather than silently accepting it.
    """

    model_config = ConfigDict(extra="forbid")

    query_type: QueryType
    drafted_reply: str
    classification_confidence: float = Field(ge=0.0, le=1.0)
    context_sufficient: bool
    missing_information: list[str]
    reasoning: str


class EndpointResponse(BaseModel):
    """Response shape returned by POST /webhook/message."""

    model_config = ConfigDict(extra="forbid")

    message_id: UUID
    query_type: QueryType
    drafted_reply: str
    confidence_score: float = Field(ge=0.0, le=1.0)
    action: ActionType
