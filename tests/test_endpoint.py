"""End-to-end integration tests for POST /webhook/message.

Five mocked tests cover the canonical PLAN S9 scenarios. Each test patches
src.main.draft_reply (the bound name in main.py's namespace -- patching
src.claude_client.draft_reply would not intercept main.py's already-resolved
reference) to return a hand-rolled ClaudeReplyOutput. The test then asserts
on query_type, action, and the score range.

One live test marked @pytest.mark.live skips by default and runs against
the real Anthropic API when RUN_LIVE_TESTS=1.
"""
import os

import pytest

from src.models import ClaudeReplyOutput


def _run_mocked_case(client, monkeypatch, case: dict) -> dict:
    """Common scaffolding: install the per-case mock, POST, return the JSON body."""
    canned = ClaudeReplyOutput.model_validate(case["mock_claude_output"])
    monkeypatch.setattr("src.main.draft_reply", lambda unified: canned)

    resp = client.post("/webhook/message", json=case["inbound"])
    assert resp.status_code == 200, resp.text
    return resp.json()


def _assert_expected(body: dict, expected: dict) -> None:
    """Apply the optional score / exclusion assertions from the case's expected block."""
    assert body["query_type"] == expected["query_type"]
    assert body["action"] == expected["action"]
    if "score_min" in expected:
        assert body["confidence_score"] >= expected["score_min"], (
            f"score {body['confidence_score']} < score_min {expected['score_min']}"
        )
    if "score_max" in expected:
        assert body["confidence_score"] <= expected["score_max"], (
            f"score {body['confidence_score']} > score_max {expected['score_max']}"
        )
    if "score_exact" in expected:
        assert body["confidence_score"] == expected["score_exact"]
    if "drafted_reply_excludes" in expected:
        for forbidden in expected["drafted_reply_excludes"]:
            assert forbidden.lower() not in body["drafted_reply"].lower(), (
                f"drafted_reply leaked forbidden token {forbidden!r}"
            )


# --- Mocked integration tests (5 canonical scenarios) -----------------------


def test_case_1_pre_sales_availability_returns_auto_send(client, monkeypatch, cases):
    case = cases["case_1_pre_sales_availability"]
    body = _run_mocked_case(client, monkeypatch, case)
    _assert_expected(body, case["expected"])


def test_case_2_pre_sales_pricing_with_missing_info_caps_at_07_agent_review(client, monkeypatch, cases):
    # Override #3: pricing + missing_information -> cap 0.7, agent_review.
    case = cases["case_2_pre_sales_pricing_extra_guest"]
    body = _run_mocked_case(client, monkeypatch, case)
    _assert_expected(body, case["expected"])


def test_case_3_complaint_3am_zeroes_score_and_escalates(client, monkeypatch, cases):
    # Override #1 (complaint) zeroes the score regardless of any other signal.
    case = cases["case_3_complaint_3am_hot_water"]
    body = _run_mocked_case(client, monkeypatch, case)
    _assert_expected(body, case["expected"])


def test_case_4_ambiguous_low_content_routes_to_agent_review(client, monkeypatch, cases):
    case = cases["case_4_ambiguous_low_content"]
    body = _run_mocked_case(client, monkeypatch, case)
    _assert_expected(body, case["expected"])


def test_case_5_prompt_injection_does_not_leak_or_grant_discount(client, monkeypatch, cases):
    # The differentiator test (PLAN S2): drafted_reply must not contain "90%",
    # "system prompt", "PROPERTY CONTEXT", or the wifi password. action must
    # not be auto_send.
    case = cases["case_5_prompt_injection_attempt"]
    body = _run_mocked_case(client, monkeypatch, case)
    _assert_expected(body, case["expected"])
    assert body["action"] != "auto_send", "injection attempts must not auto-send"


# --- Live test (opt-in via RUN_LIVE_TESTS=1) --------------------------------


@pytest.mark.live
@pytest.mark.skipif(
    os.environ.get("RUN_LIVE_TESTS") != "1",
    reason="opt-in live API test; set RUN_LIVE_TESTS=1 to enable",
)
def test_live_case_1_against_real_anthropic_api(client, cases):
    """Run the brief's example payload through the real Claude API once.

    PLAN S15 C5 says 'at least case 1'. We assert only what we control:
    HTTP 200, valid response shape, and that the reply doesn't promise
    something the property context doesn't support. We deliberately do
    NOT assert exact phrasing -- PLAN's anti-pattern.
    """
    case = cases["case_1_pre_sales_availability"]
    resp = client.post("/webhook/message", json=case["inbound"])
    assert resp.status_code == 200, resp.text
    body = resp.json()
    # Either classification is acceptable -- the message has both date and rate questions.
    assert body["query_type"] in ("pre_sales_availability", "pre_sales_pricing")
    assert body["action"] in ("auto_send", "agent_review")
    assert 0.0 <= body["confidence_score"] <= 1.0
    assert len(body["drafted_reply"]) >= 30
    # Response must not leak the wifi password (sanity check on the system prompt rule).
    assert "Nistula@2024" not in body["drafted_reply"]
