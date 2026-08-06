"""Tests for dashboard aggregation — seeded SQLite, no LLM."""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from eval_framework import db
from eval_framework.app import app
from eval_framework.dashboard import build_dashboard_data


@pytest.fixture
def db_path(tmp_path: Path) -> Path:
    path = tmp_path / "evals.db"
    db.init_db(path)
    return path


@pytest.fixture
def seeded(db_path: Path) -> str:
    """One batch: 4 convs with mixed human/judge verdicts for alignment math."""
    batch = db.create_batch(
        name="dash-batch",
        starter_file="example.json",
        batch_id="dash-1",
        status="ready",
        db_path=db_path,
    )
    specs = [
        # both pass
        ("c1", "Safety", "pass", "pass"),
        # both fail
        ("c2", "Safety", "fail", "fail"),
        # false positive: human fail, judge pass
        ("c3", "Macros", "fail", "pass"),
        # false negative: human pass, judge fail
        ("c4", "Meal Planning", "pass", "fail"),
    ]
    for conv_id, category, human, judge in specs:
        db.insert_conversation(
            batch_id=batch.batch_id,
            starter_id=conv_id,
            category=category,
            context={},
            transcript=[{"role": "user", "content": "hi"}],
            conversation_id=conv_id,
            db_path=db_path,
        )
        db.insert_verdict(
            conversation_id=conv_id,
            rater_type="human",
            verdict=human,  # type: ignore[arg-type]
            reasoning=f"human-{conv_id}",
            db_path=db_path,
        )
        db.insert_verdict(
            conversation_id=conv_id,
            rater_type="judge",
            verdict=judge,  # type: ignore[arg-type]
            reasoning=f"judge-{conv_id}",
            judge_prompt_version="v1",
            judge_model="fake",
            db_path=db_path,
        )
    return batch.batch_id


class TestBuildDashboardData:
    def test_alignment_quadrants(self, db_path: Path, seeded: str) -> None:
        data = build_dashboard_data(db_path=db_path)
        q = data["judge_vs_human"]["quadrants"]
        assert q["both_pass"] == 1
        assert q["both_fail"] == 1
        assert q["false_positives"] == 1
        assert q["false_negatives"] == 1
        assert data["judge_vs_human"]["alignment_rate"] == 0.5
        assert len(data["judge_vs_human"]["disagreements"]) == 2

    def test_human_pass_rate(self, db_path: Path, seeded: str) -> None:
        data = build_dashboard_data(db_path=db_path)
        # human: pass, fail, fail, pass → 50%
        assert data["overview"]["human_pass_rate"]["rate"] == 0.5
        assert data["agent_vs_human"]["pass_fail"] == {"pass": 2, "fail": 2}

    def test_category_bars(self, db_path: Path, seeded: str) -> None:
        data = build_dashboard_data(db_path=db_path)
        cats = {c["category"]: c for c in data["agent_vs_human"]["by_category"]}
        assert "Safety" in cats
        assert cats["Safety"]["total"] == 2

    def test_judge_failures_list(self, db_path: Path, seeded: str) -> None:
        data = build_dashboard_data(db_path=db_path)
        fails = data["judge_vs_agent"]["failures"]
        # judge fails on c2 and c4
        assert {f["starter_id"] for f in fails} == {"c2", "c4"}
        assert all(f["reasoning"] for f in fails)

    def test_batch_filter(self, db_path: Path, seeded: str) -> None:
        other = db.create_batch(
            name="other",
            starter_file="x.json",
            batch_id="other-1",
            status="ready",
            db_path=db_path,
        )
        db.insert_conversation(
            batch_id=other.batch_id,
            starter_id="lonely",
            category="Safety",
            context={},
            transcript=[],
            conversation_id="c9",
            db_path=db_path,
        )
        db.insert_verdict(
            conversation_id="c9",
            rater_type="human",
            verdict="pass",
            db_path=db_path,
        )
        scoped = build_dashboard_data(batch_id=seeded, db_path=db_path)
        assert scoped["overview"]["total_conversations"] == 4
        all_data = build_dashboard_data(db_path=db_path)
        assert all_data["overview"]["total_conversations"] == 5


class TestDashboardHttp:
    def test_dashboard_page_and_api(self, db_path: Path, seeded: str) -> None:
        app.state.db_path = db_path
        app.state.agent_factory = lambda: None  # unused
        app.state.judge_factory = lambda: None
        app.state.starters_dir = Path(".")

        with TestClient(app) as client:
            page = client.get("/dashboard?view=judge_vs_human")
            assert page.status_code == 200
            assert "Judge vs Human" in page.text
            assert "chart.js" in page.text.lower()

            api = client.get("/api/dashboard-data")
            assert api.status_code == 200
            body = api.json()
            assert body["judge_vs_human"]["alignment_rate"] == 0.5

            filtered = client.get(f"/api/dashboard-data?batch={seeded}")
            assert filtered.status_code == 200
            assert filtered.json()["overview"]["total_conversations"] == 4
