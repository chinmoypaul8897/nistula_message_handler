from fastapi import FastAPI

app = FastAPI(
    title="Nistula Message Handler",
    description=(
        "Webhook backend that ingests guest messages from multiple channels, "
        "drafts replies via Anthropic Claude, and assigns a confidence-scored "
        "action. See PLAN.md and README.md."
    ),
    version="0.1.0",
)


@app.get("/healthz")
def healthz() -> dict[str, str]:
    return {"status": "ok"}
