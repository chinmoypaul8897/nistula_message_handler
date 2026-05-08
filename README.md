# Nistula Message Handler

> Status: in progress (Chunk 0 / 9). Skeleton only — full README arrives in Chunk 8.

A backend for Nistula (luxury villa hospitality, Assagao, Goa) that ingests guest messages from multiple channels via webhook, normalizes them into a unified schema, drafts replies via the Anthropic Claude API with structured tool-use, and assigns a confidence-scored action (`auto_send` / `agent_review` / `escalate`).

The execution plan, locked architectural decisions, and per-chunk progress live in [PLAN.md](PLAN.md) and [PROGRESS.md](PROGRESS.md).

## Setup (placeholder)

Requires Python 3.11+.

```bash
git clone https://github.com/chinmoypaul8897/nistula_message_handler.git
cd nistula_message_handler

# Create and activate a virtualenv
python -m venv .venv
# macOS / Linux:
source .venv/bin/activate
# Windows PowerShell:
.venv\Scripts\Activate.ps1

pip install -r requirements.txt

# Configure the Anthropic API key
cp .env.example .env       # PowerShell: Copy-Item .env.example .env
# then open .env and set ANTHROPIC_API_KEY=sk-ant-...

# Run
uvicorn src.main:app --reload
# Health check:  http://localhost:8000/healthz
# OpenAPI docs:  http://localhost:8000/docs
```

The full setup walkthrough, architecture diagram, confidence-scoring spec, security notes, and worked examples are populated in Chunk 8 per [PLAN §12](PLAN.md).
