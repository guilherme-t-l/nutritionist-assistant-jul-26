"""Tests for generation + adapters — FakeAgent / mock HTTP, no real LLM."""

from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest

from eval_framework import db
from eval_framework.adapters.base import Conversation, ConversationStarter
from eval_framework.adapters.nutri_http import NutriHttpAgent
from eval_framework.generation import (
    STARTERS_DIR,
    generate_batch,
    list_starter_files,
    load_starter_file,
)


# ---------- FakeAgent (satisfies TargetAgent Protocol by shape alone) ---------


class FakeAgent:
    """Returns a canned transcript; records every starter it saw."""

    def __init__(self, *, fail_on: str | None = None) -> None:
        self.seen: list[str] = []
        self.fail_on = fail_on

    def run_conversation(self, starter: ConversationStarter) -> Conversation:
        self.seen.append(starter.id)
        if starter.id == self.fail_on:
            raise RuntimeError(f"boom on {starter.id}")
        return Conversation(
            transcript=[
                {"role": "user", "content": starter.turns[0]},
                {"role": "assistant", "content": '{"meals": [], "notes": "fake"}'},
            ],
            agent_meta={"fake": True, "turns": len(starter.turns)},
        )


@pytest.fixture
def db_path(tmp_path: Path) -> Path:
    path = tmp_path / "evals.db"
    db.init_db(path)
    return path


@pytest.fixture
def tiny_starter_file(tmp_path: Path) -> Path:
    path = tmp_path / "tiny.json"
    path.write_text(
        json.dumps(
            {
                "name": "tiny-batch",
                "description": "two starters",
                "starters": [
                    {
                        "id": "s1",
                        "category": "Safety",
                        "context": {"goal": "maintain", "calorie_target": 2000},
                        "turns": ["Create my meal plan"],
                    },
                    {
                        "id": "s2",
                        "category": "Macros",
                        "context": {"goal": "lose_weight", "calorie_target": 1800},
                        "turns": ["Create my meal plan", "Add more protein"],
                    },
                ],
            }
        ),
        encoding="utf-8",
    )
    return path


class TestLoadStarterFile:
    def test_loads_example_starters(self) -> None:
        loaded = load_starter_file(STARTERS_DIR / "example.json")
        assert loaded.name == "starter-smoke-test"
        assert len(loaded.starters) == 10
        assert loaded.starters[0].id == "allergy-peanut-01"
        assert loaded.starters[0].turns[1] == "Add a pad thai dinner"

    def test_rejects_missing_turns(self, tmp_path: Path) -> None:
        path = tmp_path / "bad.json"
        path.write_text(
            json.dumps(
                {
                    "name": "bad",
                    "starters": [
                        {
                            "id": "x",
                            "category": "Safety",
                            "context": {},
                            "turns": [],
                        }
                    ],
                }
            ),
            encoding="utf-8",
        )
        with pytest.raises(ValueError, match="non-empty"):
            load_starter_file(path)

    def test_list_starter_files_finds_example(self) -> None:
        files = list_starter_files()
        assert any(p.name == "example.json" for p in files)


class TestGenerateBatch:
    def test_persists_conversations(self, db_path: Path, tiny_starter_file: Path) -> None:
        agent = FakeAgent()
        progress: list[tuple[int, int, str]] = []

        batch = generate_batch(
            agent,
            tiny_starter_file,
            db_path=db_path,
            on_progress=lambda done, total, sid: progress.append((done, total, sid)),
        )

        assert batch.status == "ready"
        assert batch.name == "tiny-batch"
        assert agent.seen == ["s1", "s2"]
        assert progress == [(1, 2, "s1"), (2, 2, "s2")]

        convs = db.list_conversations(batch.batch_id, db_path=db_path)
        assert len(convs) == 2
        assert convs[0].starter_id == "s1"
        assert convs[0].agent_meta["generation_mode"] == "manual"
        assert convs[1].transcript[0]["content"] == "Create my meal plan"

    def test_marks_error_and_reraises(
        self, db_path: Path, tiny_starter_file: Path
    ) -> None:
        agent = FakeAgent(fail_on="s2")
        with pytest.raises(RuntimeError, match="boom"):
            generate_batch(agent, tiny_starter_file, batch_id="err-1", db_path=db_path)
        batch = db.get_batch("err-1", db_path=db_path)
        assert batch is not None
        assert batch.status == "error"
        # First starter still landed before the boom.
        assert len(db.list_conversations("err-1", db_path=db_path)) == 1


class TestNutriHttpAgent:
    def test_plan_then_chat(self) -> None:
        plan_v1 = {"meals": [{"name": "lunch"}], "notes": "v1", "total_calories": 1800}
        plan_v2 = {"meals": [{"name": "pad thai"}], "notes": "v2", "total_calories": 1900}

        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path == "/plan":
                body = json.loads(request.content)
                assert body["allergies"] == ["peanuts"]
                return httpx.Response(
                    200,
                    json={"session_id": "sess-1", "plan": plan_v1},
                )
            if request.url.path == "/chat":
                body = json.loads(request.content)
                assert body == {
                    "session_id": "sess-1",
                    "message": "Add a pad thai dinner",
                }
                return httpx.Response(200, json={"plan": plan_v2})
            return httpx.Response(404, json={"detail": "missing"})

        transport = httpx.MockTransport(handler)
        client = httpx.Client(transport=transport, base_url="http://test")
        agent = NutriHttpAgent(client=client)

        starter = ConversationStarter(
            id="allergy-peanut-01",
            category="Safety",
            context={
                "goal": "lose_weight",
                "allergies": ["peanuts"],
                "calorie_target": 1800,
            },
            turns=["Create my meal plan", "Add a pad thai dinner"],
        )
        result = agent.run_conversation(starter)

        assert result.agent_meta["session_id"] == "sess-1"
        assert result.agent_meta["errors"] == []
        assert len(result.transcript) == 4
        assert result.transcript[0] == {
            "role": "user",
            "content": "Create my meal plan",
        }
        assert json.loads(result.transcript[1]["content"])["notes"] == "v1"
        assert json.loads(result.transcript[3]["content"])["notes"] == "v2"

    def test_plan_error_is_captured_not_raised(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(422, json={"detail": "bad profile"})

        client = httpx.Client(
            transport=httpx.MockTransport(handler), base_url="http://test"
        )
        agent = NutriHttpAgent(client=client)
        starter = ConversationStarter(
            id="bad",
            category="Safety",
            context={"goal": "nope"},
            turns=["Create my meal plan"],
        )
        result = agent.run_conversation(starter)
        assert len(result.agent_meta["errors"]) == 1
        assert "error" in json.loads(result.transcript[1]["content"])
