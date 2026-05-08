# PROGRESS.md

Chunk-by-chunk execution log for the Nistula Message Handler. The build plan is in [PLAN.md](PLAN.md). Update this file at the end of every chunk while context is still loaded — never batch updates across chunks.

## Chunk Status Summary

| Chunk | Status         | Completed             | Notes                                                  |
|-------|----------------|-----------------------|--------------------------------------------------------|
| C0    | Done           | 2026-05-08 14:42 UTC  | Repo seeded, venv verified, FastAPI healthz live       |
| C1    | Done           | 2026-05-08 14:53 UTC  | Pydantic models + Villa B1 context, 8/8 tests passing  |
| C2    | Done           | 2026-05-08 15:34 UTC  | Claude tool-use wired, 13/13 tests + live call green   |
| C3    | Not started    | -                     | -                                                      |
| C4    | Not started    | -                     | -                                                      |
| C5    | Not started    | -                     | -                                                      |
| C6    | Not started    | -                     | -                                                      |
| C7    | Not started    | -                     | -                                                      |
| C8    | Not started    | -                     | -                                                      |
| C9    | Not started    | -                     | -                                                      |

## Chunk-by-chunk log

### C0 - Project Initialization & GitHub Setup
**Completed:** 2026-05-08 14:42 UTC
**Files created (this chunk):** requirements.txt, .env.example, src/__init__.py, src/main.py, README.md (skeleton), PROGRESS.md
**Files created earlier (seed commit f74e0c8):** PLAN.md, CLAUDE.md, .gitignore
**What was built:** FastAPI app skeleton with `GET /healthz` returning `{"status": "ok"}`. No `/webhook/message` stub (per PLAN anti-pattern). Repo on GitHub at https://github.com/chinmoypaul8897/nistula_message_handler tracking origin/main.
**Decisions made or changed:**
- Locked Python interpreter at 3.12.2 on the dev machine (satisfies the >=3.11 pin in PLAN S4). Verified via `python --version` before pinning.
- Renamed the repo references in PLAN.md from `nistula-technical-assessment` to `nistula_message_handler` to match the actual GitHub remote (7 occurrences updated). PLAN.md text now matches reality.
- README skeleton uses the real repo name in the `git clone` example and includes both POSIX and PowerShell venv-activation lines because the dev machine is Windows but reviewers likely are not.
- `/healthz` returns a plain `dict`, not a Pydantic model. Pydantic models are owned by C1.
**Deviations from plan:**
- PLAN S15 C0 step 3 says to write `.gitignore`. It was already written in the seed commit (f74e0c8) and matches the C0 requirement exactly, so it was left untouched.
- PLAN S15 C0 step 7 says to initialize git, create the GitHub repo, and push the initial commit. Already done in the seed commit; this chunk only added a second commit on top.
- PLAN S15 C0 step 1 ("Create the project directory structure exactly as specified in Section 5") was narrowed to only the files explicitly listed in C0's "Files to create" block. Empty stubs for `src/models.py`, `src/claude_client.py`, etc. were not created — each later chunk owns its own file (consistent with the C0 "do not stub /webhook/message" anti-pattern).
**Issues encountered:** None. pip install completed cleanly; uvicorn bound on first try; `/healthz`, `/docs`, and `/openapi.json` all returned HTTP 200.
**Verification (executed locally):**
- `.venv\Scripts\python.exe --version` -> Python 3.12.2.
- `pip install -r requirements.txt` -> Successfully installed fastapi 0.136.1, uvicorn 0.46.0, pydantic 2.13.4, anthropic 0.100.0, python-dotenv 1.2.2, httpx 0.28.1, pytest 9.0.3, pytest-asyncio 1.3.0 (+ transitive deps).
- `uvicorn src.main:app --host 127.0.0.1 --port 8000` booted clean.
- `curl http://127.0.0.1:8000/healthz` -> `{"status":"ok"}`, HTTP 200.
- `curl http://127.0.0.1:8000/docs` -> HTTP 200 (Swagger UI rendered).
- `curl http://127.0.0.1:8000/openapi.json` -> HTTP 200.
- `.env` not present in working tree; `.gitignore` excludes it.
**Commit:** `chore: initialize project skeleton with FastAPI healthz endpoint and gitignore`

### C1 - Pydantic Models & Property Context
**Completed:** 2026-05-08 14:53 UTC
**Files created:** src/models.py, src/property_context.py, tests/__init__.py, tests/test_models.py
**What was built:** The unified schema as code. Two Literal type aliases (`QueryType`, `ActionType`) and a `SourceChannel` alias. Four Pydantic v2 BaseModels: `InboundWebhook` (untrusted boundary), `UnifiedMessage` (internal normalized form, with `from_inbound` classmethod), `ClaudeReplyOutput` (mirrors PLAN S7.2 tool input_schema), `EndpointResponse` (HTTP response shape). Property-context module holds the locked Villa B1 dict and a `format_for_prompt()` helper that renders flat human-readable lines for the system prompt. No business logic, no LLM imports.
**Decisions made or changed:**
- `InboundWebhook` uses `extra="ignore"`. Channels send heterogeneous payloads (WhatsApp adds message IDs, Booking.com adds reservation metadata) and forbidding extras would 422 every payload that does not exactly match. The full original payload will be preserved as `messages.raw_payload` JSONB per PLAN S10.3.
- `ClaudeReplyOutput` and `UnifiedMessage` and `EndpointResponse` use `extra="forbid"`. We own those shapes end-to-end -- surprises mean the SDK changed or our code drifted, and the endpoint layer should surface that as HTTP 502 (PLAN S7.5) rather than swallow it silently.
- Naive timestamps are rejected at the boundary via a `@field_validator("timestamp")` on `InboundWebhook`. PLAN says "datetime, UTC" but does not specify what to do with naive input. `is_after_hours` (C3) converts to IST -- a naive timestamp would silently drift by 5h30m and flip the after-hours override result. Failing fast at validation is cleaner than silent coercion.
- `format_for_prompt()` uses flat `Label: value` lines, booleans rendered Yes/No, and the rate card combined into two coherent lines so the LLM sees pricing as a unit rather than three disconnected integers. Wifi password, base rate, and check-in time are explicit so the asserted test for those substrings is mechanical.
- 8 tests instead of the 6 PLAN S15 C1 lists explicitly. Added: `rejects_naive_timestamp` (guards the IST drift footgun) and `unified_message_uuids_are_unique_across_calls` (catches the classic `default=` vs `default_factory=` mistake). Both are cheap and document real invariants.
**Deviations from plan:**
- None on the locked spec. PLAN S8 VILLA_B1 is reproduced byte-for-byte; PLAN S7.2 ClaudeReplyOutput shape matches the tool input_schema 1:1; the six required test cases are all present.
- PLAN listed `QueryType` and `ActionType` as third and fifth "Pydantic models". They are implemented as `typing.Literal` type aliases (not `BaseModel`s), which is what PLAN's spec literal `Literal[...]` actually is. Pydantic handles `Literal` natively.
**Issues encountered:** None.
**Verification (executed locally):**
- `pytest tests/test_models.py -v` -> 8 passed in 0.24s.
- `python -c "from src.models import InboundWebhook, UnifiedMessage, ClaudeReplyOutput, EndpointResponse, QueryType, ActionType"` -> imports OK.
- `python -c "from src.property_context import format_for_prompt; print(format_for_prompt())"` -> 15-line block printed; wifi password `Nistula@2024`, base rate `INR 18000`, and check-in `14:00` all visible.
**Commit:** `feat: add Pydantic models for unified schema and Villa B1 property context`

### C2 - Claude Client & Tool-Use Call
**Completed:** 2026-05-08 15:34 UTC
**Files created:** src/prompts.py, src/claude_client.py, tests/test_claude_client.py
**What was built:** A working Anthropic SDK wrapper that calls Claude with forced tool-use and returns a validated `ClaudeReplyOutput`. `src/prompts.py` holds the locked TOOL_DEFINITION (PLAN S7.2), SYSTEM_PROMPT_TEMPLATE (PLAN S7.3), `build_system_prompt(property_context)` rendering the template with the formatted Villa B1 block, and `format_user_message(unified)` wrapping the inbound payload in XML-like tags so guest message text cannot be confused with structural instructions. `src/claude_client.py` defines three typed exceptions (`ClaudeTimeoutError`, `ClaudeRateLimitError`, `ClaudeServiceError`), constants (`MODEL = "claude-sonnet-4-20250514"`, `MAX_TOKENS = 1024`), a lazy `_get_client()` singleton, and `draft_reply(unified)` which forces `tool_choice` and validates `response.content[0].input` against `ClaudeReplyOutput`.
**Decisions made or changed:**
- **Live integration test executed.** Per PLAN S15 C2 verification, ran one real API call against the brief's example payload and confirmed a sensible `ClaudeReplyOutput` returned. Sanitized output below. ANTHROPIC_API_KEY was loaded from a gitignored `.env` and never logged.
- **No XML escaping in `format_user_message`.** The system-prompt rule (PLAN S3.5) is the locked primary defense against prompt injection and test case 5 in PLAN S9 exercises it. A short module docstring notes the residual tag-spoofing risk so future readers see the trade-off.
- **5 mocked tests** (PLAN required 2): happy_path_calls_with_correct_args, malformed_tool_input -> ClaudeServiceError, APITimeoutError -> ClaudeTimeoutError, RateLimitError -> ClaudeRateLimitError, non_tool_use_content -> ClaudeServiceError. The error-mapping tests pin the contract C4 will rely on.
- **Lazy `_get_client()` singleton.** Module-level `_client = None`, instantiated on first call. Clean test mocking (each test patches `_get_client`), no eager-fail-at-import when `.env` is missing, single SDK instance per process for connection reuse.
- **Defensive guard on response shape.** Even with `tool_choice` forcing, `draft_reply` checks `block.type == "tool_use"` and `block.name == "draft_guest_reply"` before reading `.input`. Anything else raises `ClaudeServiceError`. PLAN S7.5 calls this out as defense-in-depth and a mocked test exercises it.
- **Logging discipline.** INFO logs the call_id, source, message length, query_type, and context_sufficient. DEBUG logs the rendered system prompt and user message. The API key is never read into a log record. The full message text is never logged at INFO (PII per PLAN anti-patterns).
**Deviations from plan:** None.
**Issues encountered:**
- The first live-test print failed with `UnicodeEncodeError` on `₹` (the `INR` rupee symbol) because Windows default stdout is cp1252. The Claude call itself fully succeeded -- the crash was purely a print-time encoding artifact. Re-ran with `sys.stdout.reconfigure(encoding="utf-8")` and captured the full output. Worth noting because the eventual /webhook/message JSON response will need `ensure_ascii=False` only if we round-trip through json.dumps; FastAPI handles this correctly by default. No code change needed for C2.
**Verification (executed locally):**
- `pytest tests/ -v` -> 13 passed in 2.81s (5 new + 8 from C1).
- `python -c "from src.claude_client import draft_reply, ClaudeServiceError, ClaudeTimeoutError, ClaudeRateLimitError, MODEL, MAX_TOKENS; from src.prompts import TOOL_DEFINITION, build_system_prompt, format_user_message"` -> imports OK; MODEL=claude-sonnet-4-20250514, MAX_TOKENS=1024, tool name=draft_guest_reply.
- `git check-ignore .env` -> .env confirmed gitignored before any live call.
- **Live API call** (sanitized; brief example payload from PLAN S9 case 1):
  ```
  POST https://api.anthropic.com/v1/messages -> 200 OK
  query_type: pre_sales_availability
  classification_confidence: 0.95
  context_sufficient: True
  missing_information: []
  reasoning: Guest is asking about availability and pricing for specific dates
            and guest count. Property context contains availability for April
            20, 2024, and pricing structure for base occupancy.
  drafted_reply (304 chars):
    Hi Rahul! Yes, Villa B1 is available from April 20-24. For 2 adults,
    the rate is INR 18,000 per night (includes up to 4 guests). The villa
    features 3 bedrooms, private pool, and can accommodate up to 6 guests
    total. Check-in is at 2 PM and check-out at 11 AM. Would you like me
    to help you with the booking?
  ```
  Reply uses only facts from the locked property context, addresses the guest by name, sits within the 30-600 character bound, no placeholders, no hedge tokens.
**Commit:** `feat: integrate Claude tool-use for structured guest reply drafting`
