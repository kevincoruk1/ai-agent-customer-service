"""Tools the receptionist agent can call, backed by fake in-memory data.

Each tool has two parts:
  1. A JSON schema (in TOOLS) that tells Claude what the tool does and what inputs it takes.
  2. A Python function that actually runs when Claude asks for it.
Later stages swap the fake data for a real calendar/database without touching the agent loop.
"""

import json

# Fake calendar: date -> open time slots. Stands in for a real booking system.
AVAILABILITY = {
    "2026-09-28": ["09:00", "10:30", "14:00"],
    "2026-09-29": ["11:00", "15:30"],
    "2026-09-30": [],
}

# Confirmed bookings are appended here so you can inspect them after a run.
BOOKINGS = []


def check_availability(date: str) -> dict:
    """Return the open slots for a date, or an empty list if the day is full/unknown."""
    return {"date": date, "open_slots": AVAILABILITY.get(date, [])}


def book_appointment(name: str, date: str, time: str) -> dict:
    """Book a slot if it's still open; remove it from availability so it can't be double-booked."""
    slots = AVAILABILITY.get(date, [])
    if time not in slots:
        # Raising lets the agent loop return this to Claude as an error result
        raise ValueError(f"{time} on {date} is not available.")
    slots.remove(time)
    booking = {"id": len(BOOKINGS) + 1, "name": name, "date": date, "time": time}
    BOOKINGS.append(booking)
    return {"status": "confirmed", **booking}


# Tool definitions sent to Claude. strict=True guarantees inputs match the schema exactly.
TOOLS = [
    {
        "name": "check_availability",
        "description": "Look up open appointment slots for a given date. "
        "Call this before offering or booking any time.",
        "strict": True,
        "input_schema": {
            "type": "object",
            "properties": {
                "date": {"type": "string", "description": "Date in YYYY-MM-DD format."}
            },
            "required": ["date"],
            "additionalProperties": False,
        },
    },
    {
        "name": "book_appointment",
        "description": "Book an appointment. Only call after the caller has confirmed "
        "their name, date, and time.",
        "strict": True,
        "input_schema": {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "Caller's full name."},
                "date": {"type": "string", "description": "Date in YYYY-MM-DD format."},
                "time": {"type": "string", "description": "Time in HH:MM (24h) format."},
            },
            "required": ["name", "date", "time"],
            "additionalProperties": False,
        },
    },
]

# Maps tool name -> Python function, so the agent loop can dispatch by name
_HANDLERS = {
    "check_availability": check_availability,
    "book_appointment": book_appointment,
}


def execute_tool(name: str, tool_input: dict) -> tuple[str, bool]:
    """Run a tool and return (result_json, is_error). Errors go back to Claude instead of crashing."""
    handler = _HANDLERS.get(name)
    if handler is None:
        return f"Unknown tool: {name}", True
    try:
        return json.dumps(handler(**tool_input)), False
    except Exception as e:
        return f"Error: {e}", True
