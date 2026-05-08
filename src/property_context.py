"""Mock property context for Villa B1, sourced from the assessment brief.

Per PLAN.md S3.6 this lives in code, not a database. The DB schema in Part 2
(schema.sql) handles persistence separately. format_for_prompt() renders the
dict as a clean human-readable block injected into the Claude system prompt
at request time (PLAN S7.3).
"""
from typing import Any


VILLA_B1: dict[str, Any] = {
    "property_id": "villa-b1",
    "name": "Villa B1",
    "location": "Assagao, North Goa",
    "bedrooms": 3,
    "max_guests": 6,
    "private_pool": True,
    "check_in_time": "14:00",
    "check_out_time": "11:00",
    "base_rate_inr": 18000,
    "base_rate_includes_guests": 4,
    "extra_guest_inr_per_night": 2000,
    "wifi_password": "Nistula@2024",
    "caretaker_hours": "08:00-22:00",
    "chef_on_call": True,
    "chef_requires_prebooking": True,
    "availability_april_20_24": "available",
    "cancellation_policy": "Free cancellation up to 7 days before check-in",
}


def _yes_no(b: bool) -> str:
    return "Yes" if b else "No"


def format_for_prompt() -> str:
    """Render VILLA_B1 as a human-readable block for the system prompt.

    Flat 'Label: value' lines, booleans rendered as Yes/No, monetary fields
    combined with their qualifiers so the LLM sees the rate card as one
    coherent unit rather than three disconnected integers.
    """
    p = VILLA_B1
    if p["chef_on_call"] and p["chef_requires_prebooking"]:
        chef_line = "Yes (advance booking required)"
    else:
        chef_line = _yes_no(p["chef_on_call"])

    lines = [
        f"Property ID: {p['property_id']}",
        f"Name: {p['name']}",
        f"Location: {p['location']}",
        f"Bedrooms: {p['bedrooms']}",
        f"Maximum guests: {p['max_guests']}",
        f"Private pool: {_yes_no(p['private_pool'])}",
        f"Check-in time: {p['check_in_time']}",
        f"Check-out time: {p['check_out_time']}",
        (
            f"Base rate: INR {p['base_rate_inr']} per night "
            f"(includes up to {p['base_rate_includes_guests']} guests)"
        ),
        (
            f"Extra guest charge: INR {p['extra_guest_inr_per_night']} per night "
            f"per additional guest beyond {p['base_rate_includes_guests']}"
        ),
        f"WiFi password: {p['wifi_password']}",
        f"Caretaker hours: {p['caretaker_hours']} IST",
        f"Chef on call: {chef_line}",
        f"Availability (April 20-24): {p['availability_april_20_24']}",
        f"Cancellation policy: {p['cancellation_policy']}",
    ]
    return "\n".join(lines)
