"""Unit tests for eval_framework.db — uses a temp SQLite file, no real LLM."""

from __future__ import annotations

from pathlib import Path

import pytest

from eval_framework import db


@pytest.fixture
def db_path(tmp_path: Path) -> Path:
    """Fresh SQLite file per test so cases don't share state."""
    path = tmp_path / "evals.db"
    db.init_db(path)
    return path


class TestBatches:
    def test_create_and_get(self, db_path: Path) -> None:
        batch = db.create_batch(
            name="smoke",
            starter_file="example.json",
            batch_id="smoke-001",
            db_path=db_path,
        )
        assert batch.status == "generating"
        loaded = db.get_batch("smoke-001", db_path=db_path)
        assert loaded is not None
        assert loaded.name == "smoke"
        assert loaded.starter_file == "example.json"

    def test_update_status(self, db_path: Path) -> None:
        db.create_batch(
            name="smoke", starter_file="example.json", batch_id="b1", db_path=db_path
        )
        db.update_batch_status("b1", "ready", db_path=db_path)
        assert db.get_batch("b1", db_path=db_path).status == "ready"

    def test_update_unknown_raises(self, db_path: Path) -> None:
        with pytest.raises(KeyError):
            db.update_batch_status("missing", "ready", db_path=db_path)

    def test_list_newest_first(self, db_path: Path) -> None:
        db.create_batch(
            name="older", starter_file="a.json", batch_id="old", db_path=db_path
        )
        db.create_batch(
            name="newer", starter_file="b.json", batch_id="new", db_path=db_path
        )
        ids = [b.batch_id for b in db.list_batches(db_path=db_path)]
        assert ids[0] == "new"
        assert ids[1] == "old"


class TestConversations:
    def test_insert_and_round_trip(self, db_path: Path) -> None:
        db.create_batch(
            name="smoke", starter_file="example.json", batch_id="b1", db_path=db_path
        )
        conv = db.insert_conversation(
            batch_id="b1",
            starter_id="allergy-01",
            category="Safety",
            context={"allergies": ["peanuts"], "calorie_target": 1800},
            transcript=[
                {"role": "user", "content": "Create my meal plan"},
                {"role": "assistant", "content": '{"meals": []}'},
            ],
            agent_meta={"latency_ms": 1200, "model": "gemini-2.5-flash"},
            conversation_id="c1",
            db_path=db_path,
        )
        loaded = db.get_conversation("c1", db_path=db_path)
        assert loaded is not None
        assert loaded.starter_id == "allergy-01"
        assert loaded.context["allergies"] == ["peanuts"]
        assert loaded.transcript[0]["role"] == "user"
        assert loaded.agent_meta["latency_ms"] == 1200
        assert conv.conversation_id == "c1"

    def test_list_by_batch(self, db_path: Path) -> None:
        db.create_batch(
            name="smoke", starter_file="example.json", batch_id="b1", db_path=db_path
        )
        db.insert_conversation(
            batch_id="b1",
            starter_id="s1",
            category="Safety",
            context={},
            transcript=[],
            conversation_id="c1",
            db_path=db_path,
        )
        db.insert_conversation(
            batch_id="b1",
            starter_id="s2",
            category="Macros",
            context={},
            transcript=[],
            conversation_id="c2",
            db_path=db_path,
        )
        convs = db.list_conversations("b1", db_path=db_path)
        assert [c.conversation_id for c in convs] == ["c1", "c2"]

    def test_survives_reopen(self, db_path: Path) -> None:
        """Acceptance: conversations persist and survive restart."""
        db.create_batch(
            name="smoke", starter_file="example.json", batch_id="b1", db_path=db_path
        )
        db.insert_conversation(
            batch_id="b1",
            starter_id="s1",
            category="Safety",
            context={"goal": "lose_weight"},
            transcript=[{"role": "user", "content": "hi"}],
            conversation_id="c1",
            db_path=db_path,
        )
        # "Restart" = open a fresh connection via a new call; same file on disk.
        loaded = db.get_conversation("c1", db_path=db_path)
        assert loaded is not None
        assert loaded.context["goal"] == "lose_weight"


class TestVerdicts:
    def _seed_conversation(self, db_path: Path) -> None:
        db.create_batch(
            name="smoke", starter_file="example.json", batch_id="b1", db_path=db_path
        )
        db.insert_conversation(
            batch_id="b1",
            starter_id="s1",
            category="Safety",
            context={},
            transcript=[],
            conversation_id="c1",
            db_path=db_path,
        )

    def test_human_and_judge_on_same_conversation(self, db_path: Path) -> None:
        """Key property: one conversation can hold both rater types."""
        self._seed_conversation(db_path)
        db.insert_verdict(
            conversation_id="c1",
            rater_type="human",
            verdict="fail",
            reasoning="peanut oil in pad thai",
            db_path=db_path,
        )
        db.insert_verdict(
            conversation_id="c1",
            rater_type="judge",
            verdict="fail",
            reasoning="allergen present",
            judge_prompt_version="v1",
            judge_model="gemini-2.5-flash",
            db_path=db_path,
        )
        all_v = db.list_verdicts_for_conversation("c1", db_path=db_path)
        assert len(all_v) == 2
        types = {v.rater_type for v in all_v}
        assert types == {"human", "judge"}

    def test_latest_wins_on_rerate(self, db_path: Path) -> None:
        self._seed_conversation(db_path)
        db.insert_verdict(
            conversation_id="c1", rater_type="human", verdict="pass", db_path=db_path
        )
        db.insert_verdict(
            conversation_id="c1",
            rater_type="human",
            verdict="fail",
            reasoning="changed my mind",
            db_path=db_path,
        )
        latest = db.latest_verdicts_for_batch(
            "b1", rater_type="human", db_path=db_path
        )
        assert latest["c1"].verdict == "fail"
        assert latest["c1"].reasoning == "changed my mind"

    def test_judge_provenance_fields(self, db_path: Path) -> None:
        self._seed_conversation(db_path)
        v = db.insert_verdict(
            conversation_id="c1",
            rater_type="judge",
            verdict="pass",
            reasoning="looks clean",
            judge_prompt_version="v1",
            judge_model="gemini-2.5-flash",
            db_path=db_path,
        )
        assert v.judge_prompt_version == "v1"
        assert v.judge_model == "gemini-2.5-flash"

    def test_invalid_verdict_raises(self, db_path: Path) -> None:
        self._seed_conversation(db_path)
        with pytest.raises(ValueError, match="pass"):
            db.insert_verdict(
                conversation_id="c1",
                rater_type="human",
                verdict="maybe",  # type: ignore[arg-type]
                db_path=db_path,
            )
