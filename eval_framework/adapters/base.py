"""Structural interfaces (Protocols) for the eval framework.

A Protocol is a duck-typed interface: any class with matching methods
satisfies it — no inheritance required. That keeps the framework
decoupled from any one LLM SDK or host app.

See "Protocols & duck typing" in the PM mini-book if covered there.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable


@dataclass(frozen=True)
class ConversationStarter:
    """One scripted scenario from a starter JSON file.

    `context` is opaque to the framework — the adapter decides what it
    means. For nutri-assistant: either a UserProfile body for POST /plan,
    or include `resume_as` (e.g. "demo5") to login + resume a saved plan
    and run `turns` as chat edits.
    """

    id: str
    category: str
    context: dict[str, Any]
    turns: list[str]


@dataclass
class Conversation:
    """Full transcript the target agent produced for one starter.

    `transcript` is a list of `{role, content}` turns (user + assistant).
    `agent_meta` carries latency, model hints, errors — free-form so
    adapters can attach whatever is useful for later debugging.
    """

    transcript: list[dict[str, str]]
    agent_meta: dict[str, Any] = field(default_factory=dict)


@runtime_checkable
class TargetAgent(Protocol):
    """The one seam between the framework and the system under test."""

    def run_conversation(self, starter: ConversationStarter) -> Conversation:
        """Given a starter, return the full conversation transcript."""
        ...


@runtime_checkable
class JudgeLLM(Protocol):
    """Swap-in LLM used by the LLM-as-a-judge (Step 4)."""

    def complete(self, prompt: str) -> str:
        """Return the model's raw text completion for `prompt`."""
        ...


__all__ = [
    "ConversationStarter",
    "Conversation",
    "TargetAgent",
    "JudgeLLM",
]
