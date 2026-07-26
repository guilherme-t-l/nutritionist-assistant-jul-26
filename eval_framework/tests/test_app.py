"""HTTP-level tests for the eval framework UI/API — FakeAgent, no real LLM."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from eval_framework.adapters.base import Conversation, ConversationStarter
from eval_framework.app import _format_context, app


class TestFormatContext:
    def test_shows_full_profile_fields(self) -> None:
        lines = _format_context(
            {
                "goal": "maintain",
                "allergies": ["wheat", "gluten"],
                "disliked_ingredients": ["meat", "chicken"],
                "calorie_target": 2000,
                "protein_g_target": 100,
                "carbs_g_target": 250,
                "fat_g_target": 65,
                "cuisine_preferences": ["Brazilian"],
                "flavor_profiles": ["savory"],
                "meals_per_day": 4,
            }
        )
        assert lines == [
            "goal: maintain",
            "allergies: wheat, gluten",
            "dislikes: meat, chicken",
            "targets: 2000 kcal · P100g · C250g · F65g",
            "cuisine: Brazilian",
            "flavors: savory",
            "meals: 4/day",
        ]

    def test_empty_lists_render_as_none(self) -> None:
        lines = _format_context(
            {
                "goal": "lose_weight",
                "allergies": [],
                "disliked_ingredients": [],
                "calorie_target": 1500,
                "cuisine_preferences": [],
                "flavor_profiles": [],
            }
        )
        assert "allergies: none" in lines
        assert "dislikes: none" in lines
        assert "cuisine: none" in lines
        assert "flavors: none" in lines


class FakeAgent:
    def run_conversation(self, starter: ConversationStarter) -> Conversation:
        return Conversation(
            transcript=[
                {"role": "user", "content": starter.turns[0]},
                {
                    "role": "assistant",
                    "content": json.dumps(
                        {"meals": [], "notes": f"plan-for-{starter.id}"}
                    ),
                },
            ],
            agent_meta={"fake": True},
        )


class FakeJudge:
    model_name = "fake-judge"

    def __init__(self) -> None:
        self.prompts: list[str] = []

    def complete(self, prompt: str) -> str:
        self.prompts.append(prompt)
        return '{"verdict": "fail", "reasoning": "fake allergen"}'


@pytest.fixture
def client(tmp_path: Path) -> TestClient:
    starters = tmp_path / "starters"
    starters.mkdir()
    (starters / "tiny.json").write_text(
        json.dumps(
            {
                "name": "tiny-ui-batch",
                "description": "two starters for UI tests",
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
                        "turns": ["Create my meal plan"],
                    },
                ],
            }
        ),
        encoding="utf-8",
    )
    db_path = tmp_path / "evals.db"
    app.state.db_path = db_path
    app.state.starters_dir = starters
    app.state.agent_factory = FakeAgent
    app.state.judge_factory = FakeJudge

    with TestClient(app) as test_client:
        yield test_client


class TestBatchesScreen:
    def test_home_renders(self, client: TestClient) -> None:
        res = client.get("/")
        assert res.status_code == 200
        assert "Eval Framework" in res.text
        assert "New batch" in res.text
        assert "tiny.json" in res.text

    def test_create_batch_and_review_flow(self, client: TestClient) -> None:
        # TestClient runs BackgroundTasks inline before returning.
        res = client.post(
            "/batches",
            json={"starter_file": "tiny.json", "mode": "manual"},
        )
        assert res.status_code == 200
        body = res.json()
        batch_id = body["batch_id"]
        assert body["status"] == "generating"
        assert body["expected_total"] == 2

        status = client.get(f"/api/batches/{batch_id}")
        assert status.status_code == 200
        assert status.json()["status"] == "ready"
        assert status.json()["total"] == 2

        home = client.get("/")
        assert "tiny-ui-batch" in home.text
        assert "Review" in home.text

        review = client.get(f"/batches/{batch_id}/review")
        assert review.status_code == 200
        assert "Conversation 1 of 2" in review.text
        assert "s1" in review.text
        assert "Pass" in review.text
        # User-fidelity review: chat + plan panels, not a wall of JSON.
        assert 'id="review-stage"' in review.text
        assert 'id="chat-thread"' in review.text
        assert 'id="plan-card"' in review.text
        assert "/static/review.js" in review.text
        assert "review-data" in review.text

        static = client.get("/static/review.js")
        assert static.status_code == 200
        assert "renderPlan" in static.text

    def test_unknown_starter_file(self, client: TestClient) -> None:
        res = client.post(
            "/batches",
            json={"starter_file": "missing.json", "mode": "manual"},
        )
        assert res.status_code == 400


class TestVerdictApi:
    def test_rate_and_rerate(self, client: TestClient) -> None:
        batch_id = client.post(
            "/batches",
            json={"starter_file": "tiny.json", "mode": "manual"},
        ).json()["batch_id"]

        from eval_framework import db

        convs = db.list_conversations(batch_id, db_path=app.state.db_path)
        conv_id = convs[0].conversation_id

        res = client.post(
            f"/conversations/{conv_id}/verdict",
            json={"rater_type": "human", "verdict": "pass", "note": "looks fine"},
        )
        assert res.status_code == 200
        assert res.json()["verdict"] == "pass"

        res = client.post(
            f"/conversations/{conv_id}/verdict",
            json={"rater_type": "human", "verdict": "fail", "note": "allergen"},
        )
        assert res.status_code == 200

        latest = db.latest_verdicts_for_batch(
            batch_id, rater_type="human", db_path=app.state.db_path
        )
        assert latest[conv_id].verdict == "fail"
        assert latest[conv_id].reasoning == "allergen"

        # Review screen shows the current human verdict.
        review = client.get(f"/batches/{batch_id}/review?i=0")
        assert "FAIL" in review.text
        assert "allergen" in review.text

    def test_keyboard_friendly_review_has_shortcuts(self, client: TestClient) -> None:
        batch_id = client.post(
            "/batches",
            json={"starter_file": "tiny.json", "mode": "manual"},
        ).json()["batch_id"]
        review = client.get(f"/batches/{batch_id}/review")
        assert "auto-advances" in review.text
        js = client.get("/static/review.js").text
        assert 'e.key === "p"' in js or "e.key === 'p'" in js


class TestJudgeWorkflows:
    def test_judge_mode_auto_rates(self, client: TestClient) -> None:
        """Workflow 3: mode=judge generates then auto-rates."""
        batch_id = client.post(
            "/batches",
            json={"starter_file": "tiny.json", "mode": "judge"},
        ).json()["batch_id"]
        status = client.get(f"/api/batches/{batch_id}").json()
        assert status["status"] == "ready"
        assert status["total"] == 2
        assert status["judge_rated"] == 2

    def test_alignment_check_endpoint(self, client: TestClient) -> None:
        """Workflow 2: human-rate first, then POST /batches/{id}/judge."""
        from eval_framework import db

        seen_prompts: list[str] = []

        class TrackingJudge:
            model_name = "fake-judge"

            def complete(self, prompt: str) -> str:
                seen_prompts.append(prompt)
                return '{"verdict": "fail", "reasoning": "fake allergen"}'

        app.state.judge_factory = TrackingJudge

        batch_id = client.post(
            "/batches",
            json={"starter_file": "tiny.json", "mode": "manual"},
        ).json()["batch_id"]
        convs = db.list_conversations(batch_id, db_path=app.state.db_path)
        for conv in convs:
            client.post(
                f"/conversations/{conv.conversation_id}/verdict",
                json={
                    "rater_type": "human",
                    "verdict": "fail",
                    "note": "SECRET_HUMAN_ONLY",
                },
            )

        summary = client.get(f"/api/batches/{batch_id}").json()
        assert summary["can_align"] is True

        home = client.get("/")
        assert "Run alignment check" in home.text

        res = client.post(f"/batches/{batch_id}/judge")
        assert res.status_code == 200
        assert res.json()["status"] == "judging"

        status = client.get(f"/api/batches/{batch_id}").json()
        assert status["judge_rated"] == 2
        assert status["can_align"] is False

        assert len(seen_prompts) == 2
        for prompt in seen_prompts:
            assert "SECRET_HUMAN_ONLY" not in prompt

        judge_verdicts = db.latest_verdicts_for_batch(
            batch_id, rater_type="judge", db_path=app.state.db_path
        )
        for v in judge_verdicts.values():
            assert v.judge_prompt_version == "v1"
            assert v.judge_model == "fake-judge"
