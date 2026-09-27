"""HTTP API for the receptionist agent. The website (separate repo) calls /api/chat.

Run locally:  .venv/bin/uvicorn server:app --reload --port 8000

The agent logic lives in chatbot.py; this file is only the web "interface" to it.
Stage 4 will add a Twilio endpoint here that reuses the same run_turn().
"""

import os
import time
from collections import defaultdict, deque
from datetime import date

import anthropic
from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from chatbot import run_turn
from prompts import SYSTEM_PROMPT

# --- Abuse / cost limits (the endpoint is public and every message costs API credits) ---
RATE_LIMIT = 20            # max messages per client IP...
RATE_WINDOW_SECONDS = 600  # ...per 10 minutes
MAX_TURNS_PER_SESSION = 30 # caps how long (and expensive) one conversation can get
MAX_SESSIONS = 1000        # caps memory; oldest session is dropped beyond this

# Which websites may call this API from a browser. Comma-separated in the env var on Render.
ALLOWED_ORIGINS = os.getenv(
    "ALLOWED_ORIGINS", "http://localhost:5500,http://127.0.0.1:5500"
).split(",")

app = FastAPI()
app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_methods=["GET", "POST"],
    allow_headers=["Content-Type"],
)

# Conversation history per browser session, kept in memory (lost on restart/sleep).
# Fine for learning; a real deployment would use Redis or a database.
SESSIONS: dict[str, list] = {}

# Timestamps of recent requests per IP, for the sliding-window rate limit
_request_log: dict[str, deque] = defaultdict(deque)


def check_rate_limit(ip: str):
    """Reject the request if this IP sent RATE_LIMIT messages within the last window."""
    now = time.monotonic()
    log = _request_log[ip]
    while log and now - log[0] > RATE_WINDOW_SECONDS:
        log.popleft()  # forget requests older than the window
    if len(log) >= RATE_LIMIT:
        raise HTTPException(status_code=429, detail="Too many messages, please try again in a few minutes.")
    log.append(now)


def get_session(session_id: str) -> list:
    """Return this session's history, creating it (and evicting the oldest if full) as needed."""
    if session_id not in SESSIONS and len(SESSIONS) >= MAX_SESSIONS:
        SESSIONS.pop(next(iter(SESSIONS)))  # dicts keep insertion order -> oldest first
    return SESSIONS.setdefault(session_id, [])


class ChatRequest(BaseModel):
    """Incoming chat message. Length limits stop oversized/abusive inputs."""
    session_id: str = Field(min_length=1, max_length=64)
    message: str = Field(min_length=1, max_length=1000)


class ChatResponse(BaseModel):
    reply: str


@app.get("/health")
def health():
    """Cheap endpoint the website pings on chat open, to wake the server from free-tier sleep."""
    return {"status": "ok"}


@app.post("/api/chat", response_model=ChatResponse)
def chat(req: ChatRequest, request: Request):
    """Append the user's message to their session history, run one agent turn, return the reply."""
    check_rate_limit(request.client.host)

    messages = get_session(req.session_id)
    user_turns = sum(1 for m in messages if m["role"] == "user" and isinstance(m["content"], str))
    if user_turns >= MAX_TURNS_PER_SESSION:
        raise HTTPException(status_code=429, detail="This chat is too long. Please call the clinic to continue.")

    messages.append({"role": "user", "content": req.message})
    system = SYSTEM_PROMPT.format(today=date.today().isoformat())
    try:
        reply = run_turn(messages, system)
    except anthropic.APIError:
        # Drop the unanswered message so history stays valid (user/assistant must alternate)
        messages.pop()
        raise HTTPException(status_code=502, detail="The assistant is unavailable, please try again.")
    return ChatResponse(reply=reply)
