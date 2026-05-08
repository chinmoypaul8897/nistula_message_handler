"""Pytest configuration shared by the integration test suite.

- Registers the `live` marker so RUN_LIVE_TESTS=1 -m live works without
  PytestUnknownMarkWarning.
- `client` fixture: a FastAPI TestClient for POST /webhook/message.
- `cases` fixture: parsed tests/fixtures.json. Each case is self-contained:
  inbound payload, mock_claude_output, and expected assertions.
"""
import json
from pathlib import Path

import pytest


def pytest_configure(config: pytest.Config) -> None:
    config.addinivalue_line(
        "markers",
        "live: opt-in test against the real Anthropic API; "
        "run with RUN_LIVE_TESTS=1 pytest -m live",
    )


@pytest.fixture
def client():
    """FastAPI TestClient. Imported lazily so test collection doesn't
    fail if src dependencies have transient issues."""
    from fastapi.testclient import TestClient

    from src.main import app

    return TestClient(app)


@pytest.fixture
def cases() -> dict:
    """Load tests/fixtures.json. Keyed by case slug."""
    path = Path(__file__).parent / "fixtures.json"
    return json.loads(path.read_text(encoding="utf-8"))
