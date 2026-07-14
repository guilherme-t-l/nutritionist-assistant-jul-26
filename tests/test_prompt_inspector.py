"""Unit tests for the Prompt Inspector dump helper and env flag gate."""

from __future__ import annotations

import os
from unittest.mock import MagicMock, patch

from agent.llm import GeminiLLM, Message
from agent.prompt_inspector import dump_llm_input
from agent.schemas import MealPlan


def test_dump_first_call_separates_input_from_empty_history(capsys) -> None:
    system = "You are a Brazilian nutritionist.\nProfile: goal=lose_weight"
    messages = [
        Message(role="user", content="Generate my meal plan based on my goals."),
    ]
    reply = '{"meals": [], "notes": "Here is your plan."}'

    dump_llm_input(system, messages, reply)
    out = capsys.readouterr().out

    assert "1. INPUT" in out
    assert "1a. SYSTEM" in out
    assert "Brazilian nutritionist" in out
    assert "1b. USER MESSAGE" in out
    assert "Generate my meal plan based on my goals." in out
    assert "2. CONVERSATION HISTORY" in out
    assert "(empty — first call)" in out
    assert "3. ANSWER" in out
    assert "Here is your plan." in out


def test_dump_chat_call_splits_history_from_this_turn(capsys) -> None:
    system = "persona + profile\n\nCurrent meal plan:\n{\"meals\":[]}"
    messages = [
        Message(role="user", content="Generate my meal plan based on my goals."),
        Message(role="model", content="Swapped lunch for a lighter option."),
        Message(role="user", content="Make dinner vegetarian."),
    ]
    reply = '{"meals": [], "notes": "Dinner is now vegetarian."}'

    dump_llm_input(system, messages, reply)
    out = capsys.readouterr().out

    assert "1a. SYSTEM" in out
    assert "Current meal plan:" in out
    assert "1b. USER MESSAGE" in out
    assert "Make dinner vegetarian." in out
    assert "2. CONVERSATION HISTORY" in out
    assert "[0] user:" in out
    assert "[1] assistant:" in out
    assert "Swapped lunch for a lighter option." in out
    history_section = out.split("2. CONVERSATION HISTORY", 1)[1].split(
        "3. ANSWER", 1
    )[0]
    assert "Swapped lunch for a lighter option." in history_section
    assert "Make dinner vegetarian." not in history_section
    assert "3. ANSWER" in out
    assert "Dinner is now vegetarian." in out


def test_prompt_inspector_flag_gates_dump(monkeypatch, capsys) -> None:
    """With PROMPT_INSPECTOR off, GeminiLLM.chat stays quiet; with it on, dumps."""
    llm = GeminiLLM.__new__(GeminiLLM)
    llm._model = "test-model"
    llm._client = MagicMock()
    llm._client.models.generate_content.return_value = MagicMock(
        text='{"meals": [], "notes": "ok"}'
    )

    messages = [Message(role="user", content="hello")]
    system = "persona + profile"

    monkeypatch.delenv("PROMPT_INSPECTOR", raising=False)
    with patch("agent.llm.types"):
        llm.chat(messages, system=system, response_schema=MealPlan)
    assert "PROMPT INSPECTOR" not in capsys.readouterr().out

    monkeypatch.setenv("PROMPT_INSPECTOR", "1")
    with patch("agent.llm.types"):
        llm.chat(messages, system=system, response_schema=MealPlan)
    out = capsys.readouterr().out
    assert "PROMPT INSPECTOR" in out
    assert "1a. SYSTEM" in out
    assert "persona + profile" in out
    assert "1b. USER MESSAGE" in out
    assert "hello" in out
    assert "(empty — first call)" in out
    assert "3. ANSWER" in out
    assert "ok" in out.split("3. ANSWER", 1)[1]

    monkeypatch.delenv("PROMPT_INSPECTOR", raising=False)
    assert os.environ.get("PROMPT_INSPECTOR") != "1"
