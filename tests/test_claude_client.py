"""Mocked unit tests for src.claude_client.draft_reply.

The seam is src.claude_client._get_client. Each test patches it to return a
Mock whose .messages.create returns or raises whatever the test needs. No
real Anthropic API calls. The live integration check lives separately
(scripts/c2_live_check.py and PROGRESS.md C2 entry).
"""
from types import SimpleNamespace
from unittest.mock import MagicMock

import anthropic
import httpx
import pytest

from src.claude_client import (
    ClaudeRateLimitError,
    ClaudeServiceError,
    ClaudeTimeoutError,
    MAX_TOKENS,
    MODEL,
    draft_reply,
)
from src.models import InboundWebhook, UnifiedMessage


BRIEF_EXAMPLE = {
    "source": "whatsapp",
    "guest_name": "Rahul Sharma",
    "message": "Is the villa available from April 20 to 24? What is the rate for 2 adults?",
    "timestamp": "2026-05-05T10:30:00Z",
    "booking_ref": "NIS-2024-0891",
    "property_id": "villa-b1",
}

VALID_TOOL_INPUT = {
    "query_type": "pre_sales_availability",
    "drafted_reply": "Hi Rahul, the villa is available those nights.",
    "classification_confidence": 0.92,
    "context_sufficient": True,
    "missing_information": [],
    "reasoning": "Date range matches the locked availability window.",
}


def _unified() -> UnifiedMessage:
    return UnifiedMessage.from_inbound(InboundWebhook.model_validate(BRIEF_EXAMPLE))


def _tool_use_response(tool_input: dict, name: str = "draft_guest_reply"):
    """Build a fake Anthropic response whose first content block is a tool_use."""
    block = SimpleNamespace(type="tool_use", name=name, input=tool_input)
    return SimpleNamespace(content=[block])


def _make_mock_client(create_return=None, create_side_effect=None) -> MagicMock:
    client = MagicMock()
    if create_side_effect is not None:
        client.messages.create.side_effect = create_side_effect
    else:
        client.messages.create.return_value = create_return
    return client


def test_happy_path_calls_with_correct_args(monkeypatch):
    mock_client = _make_mock_client(_tool_use_response(VALID_TOOL_INPUT))
    monkeypatch.setattr("src.claude_client._get_client", lambda: mock_client)

    out = draft_reply(_unified())

    assert out.query_type == "pre_sales_availability"
    assert out.classification_confidence == 0.92

    mock_client.messages.create.assert_called_once()
    kwargs = mock_client.messages.create.call_args.kwargs
    assert kwargs["model"] == MODEL
    assert kwargs["max_tokens"] == MAX_TOKENS
    assert kwargs["tool_choice"] == {"type": "tool", "name": "draft_guest_reply"}
    assert len(kwargs["tools"]) == 1
    assert kwargs["tools"][0]["name"] == "draft_guest_reply"
    assert kwargs["messages"][0]["role"] == "user"
    assert "<inbound_message>" in kwargs["messages"][0]["content"]
    assert "PROPERTY CONTEXT:" in kwargs["system"]


def test_malformed_tool_input_raises_claude_service_error(monkeypatch):
    # Missing 'reasoning' -> ClaudeReplyOutput.model_validate fails -> ClaudeServiceError.
    bad_input = {k: v for k, v in VALID_TOOL_INPUT.items() if k != "reasoning"}
    mock_client = _make_mock_client(_tool_use_response(bad_input))
    monkeypatch.setattr("src.claude_client._get_client", lambda: mock_client)

    with pytest.raises(ClaudeServiceError):
        draft_reply(_unified())


def test_api_timeout_maps_to_claude_timeout_error(monkeypatch):
    timeout = anthropic.APITimeoutError(request=httpx.Request("POST", "https://x"))
    mock_client = _make_mock_client(create_side_effect=timeout)
    monkeypatch.setattr("src.claude_client._get_client", lambda: mock_client)

    with pytest.raises(ClaudeTimeoutError):
        draft_reply(_unified())


def test_rate_limit_maps_to_claude_rate_limit_error(monkeypatch):
    response = httpx.Response(429, request=httpx.Request("POST", "https://x"))
    rate = anthropic.RateLimitError(
        message="rate limited", response=response, body=None
    )
    mock_client = _make_mock_client(create_side_effect=rate)
    monkeypatch.setattr("src.claude_client._get_client", lambda: mock_client)

    with pytest.raises(ClaudeRateLimitError):
        draft_reply(_unified())


def test_non_tool_use_content_raises_claude_service_error(monkeypatch):
    # PLAN S7.5: shouldn't happen with tool_choice forcing, but guard.
    text_block = SimpleNamespace(type="text", text="hello")
    fake_response = SimpleNamespace(content=[text_block])
    mock_client = _make_mock_client(fake_response)
    monkeypatch.setattr("src.claude_client._get_client", lambda: mock_client)

    with pytest.raises(ClaudeServiceError):
        draft_reply(_unified())
