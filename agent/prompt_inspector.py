"""Dev-only dump of one LLM call (input + history + answer).

Gated by `PROMPT_INSPECTOR=1` at the call site (`GeminiLLM.chat`). This module
only formats and prints — no env checks, no I/O beyond stdout.

Layout (matches how to read a call):
  1. INPUT — what this call is asking for right now
     1a. SYSTEM — persona, profile, current plan (not from the user)
     1b. USER MESSAGE — this turn's user text
  2. CONVERSATION HISTORY — prior turns the model also receives
  3. ANSWER — what the agent returned
"""

from __future__ import annotations

import json

from agent.llm import Message

_BANNER = "=" * 40


def dump_llm_input(system: str, messages: list[Message], reply: str) -> None:
    """Pretty-print this call's input, history, and agent answer to stdout."""
    # Routes always append the new user turn last, so history is everything
    # before it. Fall back to "all history / empty user" if the shape is odd.
    if messages and messages[-1].role == "user":
        history = messages[:-1]
        user_message = messages[-1].content
    else:
        history = list(messages)
        user_message = "(none)"

    lines = [
        _BANNER,
        "PROMPT INSPECTOR",
        _BANNER,
        "",
        "1. INPUT  (fed to the model this call)",
        "   1a. SYSTEM  (persona / profile / current plan — not from the user)",
        "-" * 40,
        system,
        "",
        "   1b. USER MESSAGE  (this turn)",
        "-" * 40,
        user_message,
        "",
        "2. CONVERSATION HISTORY  (prior turns the agent can see)",
        "-" * 40,
    ]
    if not history:
        lines.append("(empty — first call)")
        lines.append("")
    else:
        for i, m in enumerate(history):
            label = "user" if m.role == "user" else "assistant"
            lines.append(f"[{i}] {label}:")
            lines.append(m.content)
            lines.append("")

    lines.extend(
        [
            "3. ANSWER  (from the agent)",
            "-" * 40,
            _format_reply(reply),
            "",
            _BANNER,
        ]
    )
    print("\n".join(lines))


def _format_reply(reply: str) -> str:
    """Pretty-print JSON answers; fall back to the raw string."""
    try:
        return json.dumps(json.loads(reply), indent=2, ensure_ascii=False)
    except (json.JSONDecodeError, TypeError):
        return reply
