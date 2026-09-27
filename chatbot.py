"""Stage 1: text-based receptionist chatbot with tool use.

Run:  python chatbot.py   (type 'quit' to exit)

Uses a hand-written agent loop (instead of the SDK's tool runner) on purpose,
so you can see exactly how tool calls flow between your code and Claude.
"""

import time
from datetime import date

import anthropic
from dotenv import load_dotenv

from prompts import SYSTEM_PROMPT
from tools import BOOKINGS, TOOLS, execute_tool

load_dotenv()  # reads ANTHROPIC_API_KEY from .env

MODEL = "claude-opus-5"
client = anthropic.Anthropic()


def ask_claude(messages: list, system: str):
    """Send the conversation to Claude once and return the response."""
    return client.beta.messages.create(
        model=MODEL,
        max_tokens=16000,
        system=system,
        tools=TOOLS,
        messages=messages,
        # Low effort = faster, shorter replies; right for a latency-sensitive phone agent
        output_config={"effort": "low"},
        # If a safety classifier declines, the API retries on a recommended fallback model
        betas=["server-side-fallback-2026-07-01"],
        fallbacks="default",
    )


def run_turn(messages: list, system: str) -> str:
    """Handle one caller turn: loop until Claude stops calling tools, then return its spoken reply."""
    while True:
        start = time.perf_counter()
        response = ask_claude(messages, system)
        print(f"  [llm {time.perf_counter() - start:.2f}s, stop={response.stop_reason}]")

        # Keep the full content (text + tool_use blocks) in history, not just the text
        messages.append({"role": "assistant", "content": response.content})

        if response.stop_reason == "refusal":
            return "Sorry, I can't help with that. A staff member will call you back."

        if response.stop_reason != "tool_use":
            # end_turn (or max_tokens): return whatever text Claude said
            return "".join(b.text for b in response.content if b.type == "text")

        # Run every tool Claude asked for, and send all results back in ONE user message
        tool_results = []
        for block in response.content:
            if block.type != "tool_use":
                continue
            result, is_error = execute_tool(block.name, block.input)
            print(f"  [tool {block.name}({block.input}) -> {result}]")
            tool_results.append({
                "type": "tool_result",
                "tool_use_id": block.id,  # must match the tool_use block's id
                "content": result,
                "is_error": is_error,
            })
        messages.append({"role": "user", "content": tool_results})


def main():
    """Terminal chat loop: read caller input, run a turn, print the reply."""
    system = SYSTEM_PROMPT.format(today=date.today().isoformat())
    messages = []
    print("Receptionist ready. Type 'quit' to exit.\n")
    print("Agent: Thanks for calling Bright Smile Dental, how can I help?")

    while True:
        user_input = input("You: ").strip()
        if user_input.lower() in {"quit", "exit"}:
            break
        if not user_input:
            continue
        messages.append({"role": "user", "content": user_input})
        try:
            reply = run_turn(messages, system)
        except anthropic.APIConnectionError:
            reply = "(network error - check your connection)"
        except anthropic.APIStatusError as e:
            reply = f"(API error {e.status_code}: {e.message})"
        print(f"Agent: {reply}\n")

    print(f"\nBookings made this session: {BOOKINGS}")


if __name__ == "__main__":
    main()
