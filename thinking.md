# Part 3 — The 3am Hot-Water Scenario

## Question A — The Immediate Response

> James — I'm so sorry. I've just woken our on-call team for Villa B1; they'll be in touch with you within minutes. We know breakfast is coming and we'll do everything we can. Truly sorry for the disruption.

The trade-off: the guest needs to feel heard within seconds, but we cannot make commitments the on-call human hasn't approved. This reply buys five minutes of trust while a real person actually engages — no fix-by-breakfast promise, no refund offered, just acknowledgement and the truthful next step.

## Question B — The System Design

**t=0s** (within ~2s of the webhook hit). The AI sends the acknowledgement above. Confidence scoring routes the message to `escalate` via override #1 (complaint) and #2 (after-hours), with a 0.0 score. The pager fires to the on-call agent in a cascade — WhatsApp first, SMS fifteen seconds later, email at thirty. An incident row is created (`severity=high`, `property_id=villa-b1`, `guest_id`, `conversation_id`, `category=hot_water`). The Villa B1 caretaker is paged separately with location, guest contact, and the issue summary.

**t=+15min.** If no human has acknowledged the page, the system escalates to the property manager.

**t=+30min.** If still no acknowledgement, the system escalates to the founder and auto-sends a delay-acknowledgement to James so he isn't left silent.

**Throughout.** Every step appends to `message_audit_log` — pages, escalations, drafts, sends — so a post-incident review can reconstruct the full timeline.

## Question C — The Learning

- **Observation.** "Hot water at Villa B1" is a named issue with three occurrences in 60 days. That's a pattern, not noise.
- **System action right now.** Auto-tag any future complaint matching `{hot water, geyser, water heater, no heat}` at Villa B1 to the existing issue thread, and trigger the high-severity preventive workflow before the guest finishes typing.
- **Build to prevent the 4th:**
  - **Preventive maintenance schedule** — geyser inspection within 48 hours of any pattern detection.
  - **Pre-arrival auto-check** — caretaker confirms hot water is functional 4 hours before every Villa B1 check-in; logged in the PMS.
  - **Issue heatmap** — `complaints × property × issue_type` over time; threshold-triggered alerts when any cell crosses 2 occurrences in 30 days.
  - **Root-cause field on every resolved complaint** — within 7 days of close, a human writes the root cause. This is the data that turns reactive support into preventive ops.
