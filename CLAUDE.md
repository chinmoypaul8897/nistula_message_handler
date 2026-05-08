# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project state

This repository implements the **Nistula technical assessment**: a backend that ingests guest messages from multiple channels via webhook, normalizes them, drafts replies via Anthropic Claude with structured tool-use, and assigns a confidence-scored action (`auto_send` / `agent_review` / `escalate`).

[PLAN.md](PLAN.md) is the source of truth for design, scope, and execution. **Read it at the start of every session.** Sections 1–14 are global references; Section 15 is the chunk-by-chunk execution plan. Context resets between chunks — do not assume memory of prior chunk execution; rely on `PROGRESS.md` for state.

Three deliverables, all reviewed equally with the README:
- Part 1 — webhook + Claude integration + confidence scoring under [src/](src/)
- Part 2 — PostgreSQL unified-messaging schema in [schema.sql](schema.sql)
- Part 3 — 3am hot-water scenario response in [thinking.md](thinking.md) (≤400 words, hard limit)

## Workflow contract

**Build plan lives in [PLAN.md](PLAN.md). Always work one chunk at a time. Never start the next chunk until the user says so.**

Each chunk is self-contained. Context resets between chunks — rely on `PROGRESS.md` for state, not memory. At the start of every chunk: read PLAN §1–14 plus the active chunk in §15, read `PROGRESS.md`, then enter plan mode (deliverable, files touched, approach, deviations, questions) and wait for user approval before any code is written. At the end of every chunk: run verification, append the chunk's entry to `PROGRESS.md` while context is still loaded, commit with the chunk's prescribed conventional-commits message, and push.

## Per-chunk standard operating procedure

At the start of every chunk, in plan mode:

1. Read [PLAN.md](PLAN.md) Sections 1–14, then the active chunk subsection in Section 15.
2. Read [PROGRESS.md](PROGRESS.md) to see project state.
3. Confirm understanding of the chunk's deliverable and verification criteria.
4. List files to be touched and the approach.
5. Flag any decisions you would make differently, or anything ambiguous.
6. Ask clarifying questions, then wait for user approval before executing.
7. After execution: run verification, update PROGRESS.md (append, do not batch across chunks), commit, push.

## Locked architectural decisions (do not re-litigate)

These are settled in [PLAN.md §3](PLAN.md). If you disagree, flag it during planning — do not silently deviate.

- **One structured Claude call per message** using tool-use (`tool_choice` forces `draft_guest_reply`). Not three sequential calls. Extract structured output from `response.content[0].input`, never parse JSON from `.text`.
- **Python computes the final `confidence_score`**, not Claude. Scoring lives in `src/confidence.py` as a 4-factor weighted base score plus hard overrides (see [PLAN.md §6](PLAN.md)).
- **Hard overrides take precedence over the weighted score**, applied in defined order: complaint → score 0.0 escalate; after-hours (22:00–08:00 IST) special_request/complaint → escalate; pricing with non-empty `missing_information` → cap 0.7, agent_review.
- **Identity resolution** in the schema uses separate `guests` (canonical) and `guest_identifiers` (channel-specific handles, many-to-one). `guest_name` is **never** the canonical key — this is the answer to the brief's "hardest design decision" prompt.
- **Prompt injection** is defended at the system-prompt layer; test case 5 explicitly exercises it. Document this in the README's Security section.
- **Property context lives in code** at `src/property_context.py` (not a DB read) for Part 1.
- **Model is `claude-sonnet-4-20250514`** (specified by brief — do not substitute).

## Tech stack (locked)

Python 3.11+, FastAPI, Pydantic v2, official `anthropic` SDK, pytest + httpx. Pinned versions are in [PLAN.md §4](PLAN.md). Pydantic v2 syntax (`model_config`, not nested `class Config`).

## Common commands

```bash
# Install
python -m venv .venv && source .venv/bin/activate    # PowerShell: .venv\Scripts\Activate.ps1
pip install -r requirements.txt
cp .env.example .env                                  # then add ANTHROPIC_API_KEY

# Run
uvicorn src.main:app --reload
# Then: http://localhost:8000/docs and http://localhost:8000/healthz

# Test
pytest tests/                                         # default: mocked Claude, no API calls
pytest tests/test_confidence.py::test_complaint_at_3am_overrides_to_escalate -v   # single test
RUN_LIVE_TESTS=1 pytest -m live tests/test_endpoint.py    # opt-in live API smoke test

# Schema validation (Part 2)
psql -f schema.sql <db>                               # against a fresh PG database
```

## Tools to run after edits

After any change, run the relevant subset before reporting the chunk done:

| When you change… | Run |
|---|---|
| `src/models.py` or `src/property_context.py` | `pytest tests/test_models.py -v` |
| `src/confidence.py` | `pytest tests/test_confidence.py -v` |
| `src/claude_client.py` or `src/prompts.py` | `pytest tests/test_claude_client.py -v` |
| `src/main.py` or any endpoint glue | `pytest tests/test_endpoint.py -v` and manually `curl localhost:8000/healthz` |
| Any `src/` file | `pytest tests/` (full mocked suite — must stay green) |
| `schema.sql` | `psql -f schema.sql` against a fresh PG database to confirm it parses |
| `thinking.md` | `wc -w thinking.md` (must be ≤ 400) |
| `README.md` setup section | Walk through the documented setup on a clean clone |
| Pre-push (every push) | Grep staged diff for `sk-ant-` and `ANTHROPIC`; confirm `.env` is not staged; relevant test suite green |

Before submission only: `RUN_LIVE_TESTS=1 pytest -m live tests/test_endpoint.py` once against the real Anthropic API.

`ruff` is optional/time-permitting per [PLAN.md §4](PLAN.md) — do not gate commits on it.

## Project-wide anti-patterns (from [PLAN.md §17](PLAN.md))

- Hardcoding the API key anywhere, even temporarily.
- Sequential Claude calls (classify → reply → score). One structured call only.
- Treating `guest_name` as canonical guest identity in the schema.
- Soft confidence rules with no hard overrides for high-stakes branches.
- Logging full message text at INFO (PII). Use DEBUG; INFO logs `message_id`, source, and length only.
- Returning HTTP 200 with an error body — HTTP codes are the contract. Map Claude errors: timeout → 503, rate limit → 429, service/malformed tool input → 502.
- Generating `message_id` *after* the Claude call (it must exist before, so error responses can include it).
- Stuffing business logic into Pydantic models — they are shape and validation only; no LLM imports.
- Importing `claude_client` from `confidence.py` — confidence is a pure function of inputs.
- Stubbing `/webhook/message` before C4 — leaving it undefined keeps early commits honest.
- Adding scope mid-chunk — write follow-ups into `PROGRESS.md` and keep moving.
- `VARCHAR(N)` for free text in the schema — use `TEXT`. All timestamps are `TIMESTAMPTZ`.

## Reference data and constants

- Villa B1 property dict and after-hours window (caretaker on duty 08:00–22:00 IST) are locked in [PLAN.md §8](PLAN.md). The wifi password and rate card live there exactly as the brief gave them — do not alter.
- The five canonical test fixtures (baseline availability, extra-guest pricing math, 3am complaint, ambiguous "Hi?", prompt injection) are specified in [PLAN.md §9](PLAN.md). Each tests something specific; do not rewrite them.
- Confidence scoring constants (weights, risk class lookup, hedge tokens, action thresholds at 0.85 and 0.60) are in [PLAN.md §6](PLAN.md).

## Commit conventions

Conventional Commits prefixes: `chore:`, `feat:`, `fix:`, `test:`, `docs:`, `refactor:`. Each chunk in [PLAN.md §15](PLAN.md) specifies its commit message. Pre-commit checklist: no `sk-ant-` or `ANTHROPIC` in staged files; `.env` not staged; tests pass.
