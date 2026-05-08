"""Anthropic SDK wrapper that drafts replies via forced tool-use.

Per PLAN.md S3.1 we make exactly one Claude call per inbound message and
force the draft_guest_reply tool. Structured output is read from the tool_use
block's input dict and validated against ClaudeReplyOutput. SDK exceptions
are mapped to typed errors so the endpoint layer (C4) can map cleanly to
HTTP 503/429/502.
"""
import logging
import os
from typing import Optional

import anthropic
from dotenv import load_dotenv
from pydantic import ValidationError

from src.models import ClaudeReplyOutput, UnifiedMessage
from src.prompts import TOOL_DEFINITION, build_system_prompt, format_user_message
from src.property_context import VILLA_B1


load_dotenv()

logger = logging.getLogger(__name__)

MODEL = "claude-sonnet-4-20250514"  # PLAN S7.1 -- locked, do not substitute.
MAX_TOKENS = 1024  # PLAN S7.4.

_TOOL_NAME = "draft_guest_reply"
_TOOL_CHOICE = {"type": "tool", "name": _TOOL_NAME}


class ClaudeTimeoutError(Exception):
    """Anthropic API timed out. Endpoint maps to HTTP 503."""


class ClaudeRateLimitError(Exception):
    """Anthropic API rate-limited the request. Endpoint maps to HTTP 429."""


class ClaudeServiceError(Exception):
    """Anthropic API failed, returned malformed tool input, or returned a
    non-tool-use content block. Endpoint maps to HTTP 502."""


_client: Optional[anthropic.Anthropic] = None


def _get_client() -> anthropic.Anthropic:
    """Return a cached Anthropic client. Tests patch this seam."""
    global _client
    if _client is None:
        _client = anthropic.Anthropic()
    return _client


def draft_reply(unified: UnifiedMessage) -> ClaudeReplyOutput:
    """Call Claude with forced tool-use and return the validated structured output.

    Raises:
        ClaudeTimeoutError: Anthropic API timed out.
        ClaudeRateLimitError: Anthropic API rate-limited the request.
        ClaudeServiceError: Any other API failure, or the response did not
            contain a valid draft_guest_reply tool_use block.
    """
    system_prompt = build_system_prompt(VILLA_B1)
    user_message = format_user_message(unified)

    logger.info(
        "Claude call start message_id=%s source=%s message_len=%d",
        unified.message_id,
        unified.source,
        len(unified.message_text),
    )
    logger.debug("system_prompt=%s", system_prompt)
    logger.debug("user_message=%s", user_message)

    client = _get_client()
    try:
        response = client.messages.create(
            model=MODEL,
            max_tokens=MAX_TOKENS,
            system=system_prompt,
            tools=[TOOL_DEFINITION],
            tool_choice=_TOOL_CHOICE,
            messages=[{"role": "user", "content": user_message}],
        )
    except anthropic.APITimeoutError as exc:
        logger.warning("Claude call timed out message_id=%s", unified.message_id)
        raise ClaudeTimeoutError("Anthropic API timeout") from exc
    except anthropic.RateLimitError as exc:
        logger.warning("Claude call rate-limited message_id=%s", unified.message_id)
        raise ClaudeRateLimitError("Anthropic API rate limit") from exc
    except anthropic.APIError as exc:
        logger.error(
            "Claude call API error message_id=%s error=%s",
            unified.message_id,
            type(exc).__name__,
        )
        raise ClaudeServiceError(f"Anthropic API error: {type(exc).__name__}") from exc

    # PLAN S7.5: shouldn't happen with tool_choice forcing, but guard.
    if not response.content:
        raise ClaudeServiceError("Claude returned empty content")
    block = response.content[0]
    block_type = getattr(block, "type", None)
    block_name = getattr(block, "name", None)
    if block_type != "tool_use" or block_name != _TOOL_NAME:
        logger.error(
            "Claude returned non-tool-use block message_id=%s block_type=%s",
            unified.message_id,
            block_type,
        )
        raise ClaudeServiceError(
            f"Expected tool_use block named {_TOOL_NAME!r}, got {block_type!r}"
        )

    try:
        output = ClaudeReplyOutput.model_validate(block.input)
    except ValidationError as exc:
        logger.error(
            "Claude tool input failed validation message_id=%s",
            unified.message_id,
        )
        raise ClaudeServiceError("Claude tool input failed schema validation") from exc

    logger.info(
        "Claude call ok message_id=%s query_type=%s context_sufficient=%s",
        unified.message_id,
        output.query_type,
        output.context_sufficient,
    )
    return output
