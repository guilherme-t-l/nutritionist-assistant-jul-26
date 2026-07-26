"""Tests for the LLM-as-a-judge — FakeLLM only, no real API calls."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from eval_framework import db
from eval_framework.judge import (
    build_judge_user_payload,
    compose_judge_prompt,
    judge_batch,
    judge_conversation,
    load_judge_prompt,
    parse_judge_reply,
)


class FakeJudge:
    """Returns canned JSON; records every prompt it saw."""

    model_name = "fake-judge"

    def __init__(self, reply: str = '{"verdict": "fail", "reasoning": "allergen"}') -> None:
        self.reply = reply
        self.prompts: list[str] = []

    def complete(self, prompt: str) -> str:
        self.prompts.append(prompt)
        return self.reply


@pytest.fixture
def db_path(tmp_path: Path) -> Path:
    path = tmp_path / "evals.db"
    db.init_db(path)
    return path


@pytest.fixture
def seeded_batch(db_path: Path) -> str:
    batch = db.create_batch(
        name="align",
        starter_file="example.json",
        batch_id="align-1",
        status="ready",
        db_path=db_path,
    )
    db.insert_conversation(
        batch_id=batch.batch_id,
        starter_id="allergy-01",
        category="Safety",
        context={"allergies": ["peanuts"], "calorie_target": 1800},
        transcript=[
            {"role": "user", "content": "Create my meal plan"},
            {
                "role": "assistant",
                "content": json.dumps(
                    {"meals": [{"name": "pad thai with peanut sauce"}], "notes": "x"}
                ),
            },
        ],
        conversation_id="c1",
        db_path=db_path,
    )
    db.insert_conversation(
        batch_id=batch.batch_id,
        starter_id="macros-01",
        category="Macros",
        context={"allergies": [], "calorie_target": 2000},
        transcript=[
            {"role": "user", "content": "Create my meal plan"},
            {
                "role": "assistant",
                "content": json.dumps({"meals": [], "notes": "ok", "total_calories": 2000}),
            },
        ],
        conversation_id="c2",
        db_path=db_path,
    )
    return batch.batch_id


class TestPromptLoading:
    def test_v1_exists_and_mentions_fail_rules(self) -> None:
        text = load_judge_prompt("v1")
        assert "Allergen" in text or "allergen" in text.lower()
        assert "10%" in text
        assert "pass" in text and "fail" in text


class TestParseJudgeReply:
    def test_plain_json(self) -> None:
        reply = parse_judge_reply('{"verdict": "pass", "reasoning": "clean"}')
        assert reply.verdict == "pass"
        assert reply.reasoning == "clean"

    def test_fenced_json(self) -> None:
        raw = 'Here you go:\n```json\n{"verdict": "fail", "reasoning": "kcal"}\n```'
        reply = parse_judge_reply(raw)
        assert reply.verdict == "fail"

    def test_invalid_verdict_raises(self) -> None:
        with pytest.raises(ValueError, match="pass|fail"):
            parse_judge_reply('{"verdict": "maybe", "reasoning": "x"}')


class TestBlindness:
    def test_payload_signature_cannot_include_human_verdicts(self) -> None:
        """build_judge_user_payload only accepts context + transcript — no verdict arg.

        If a human note somehow sat in the transcript (it shouldn't), that would
        be a data bug upstream. Here we assert the builder doesn't add a
        'human verdict' section of its own.
        """
        full = compose_judge_prompt(
            "instructions",
            context={"allergies": ["peanuts"]},
            transcript=[{"role": "user", "content": "hi"}],
            starter_id="s1",
            category="Safety",
        )
        assert "human verdict" not in full.lower()
        assert "rater_type" not in full.lower()


class TestJudgeBatch:
    def test_writes_verdicts_with_provenance(
        self, db_path: Path, seeded_batch: str
    ) -> None:
        llm = FakeJudge('{"verdict": "fail", "reasoning": "peanut sauce"}')
        result = judge_batch(seeded_batch, llm, db_path=db_path)
        assert result.rated == 2
        assert result.skipped == 0
        assert result.errors == []

        verdicts = db.latest_verdicts_for_batch(
            seeded_batch, rater_type="judge", db_path=db_path
        )
        assert set(verdicts) == {"c1", "c2"}
        assert verdicts["c1"].judge_prompt_version == "v1"
        assert verdicts["c1"].judge_model == "fake-judge"
        assert verdicts["c1"].reasoning == "peanut sauce"

        # Blindness: even after a human verdict exists, re-judging (forced)
        # must not put the human note into the prompt.
        db.insert_verdict(
            conversation_id="c1",
            rater_type="human",
            verdict="fail",
            reasoning="SECRET_HUMAN_NOTE_XYZ",
            db_path=db_path,
        )
        llm_again = FakeJudge('{"verdict": "pass", "reasoning": "ok"}')
        conv = db.get_conversation("c1", db_path=db_path)
        assert conv is not None
        judge_conversation(llm_again, conv, db_path=db_path)
        assert "SECRET_HUMAN_NOTE_XYZ" not in llm_again.prompts[0]
        for prompt in llm.prompts:
            assert "SECRET_HUMAN_NOTE_XYZ" not in prompt

    def test_skips_already_judged(self, db_path: Path, seeded_batch: str) -> None:
        llm = FakeJudge()
        judge_batch(seeded_batch, llm, db_path=db_path)
        llm2 = FakeJudge('{"verdict": "pass", "reasoning": "second pass"}')
        result = judge_batch(seeded_batch, llm2, db_path=db_path)
        assert result.rated == 0
        assert result.skipped == 2
        assert llm2.prompts == []

    def test_judge_conversation_single(
        self, db_path: Path, seeded_batch: str
    ) -> None:
        conv = db.get_conversation("c1", db_path=db_path)
        assert conv is not None
        llm = FakeJudge('{"verdict": "fail", "reasoning": "allergen leak"}')
        v = judge_conversation(llm, conv, db_path=db_path)
        assert v.rater_type == "judge"
        assert v.verdict == "fail"
