"""Unit tests for eval_framework.metrics — pure math, no I/O."""

from __future__ import annotations

import pytest

from eval_framework.metrics import (
    alignment,
    disagreement_label,
    pass_rate,
)


class TestPassRate:
    def test_all_pass(self) -> None:
        result = pass_rate(["pass", "pass", "pass"])
        assert result.passes == 3
        assert result.fails == 0
        assert result.total == 3
        assert result.rate == 1.0

    def test_mixed(self) -> None:
        result = pass_rate(["pass", "fail", "pass", "fail"])
        assert result.passes == 2
        assert result.fails == 2
        assert result.total == 4
        assert result.rate == 0.5

    def test_empty_is_undefined(self) -> None:
        result = pass_rate([])
        assert result.total == 0
        assert result.rate is None

    def test_unknown_verdict_raises(self) -> None:
        with pytest.raises(ValueError, match="Unknown verdict"):
            pass_rate(["pass", "maybe"])


class TestAlignment:
    def test_perfect_agreement(self) -> None:
        human = {"c1": "pass", "c2": "fail"}
        judge = {"c1": "pass", "c2": "fail"}
        result = alignment(human, judge)
        assert result.rate == 1.0
        assert result.quadrants.both_pass == 1
        assert result.quadrants.both_fail == 1
        assert result.quadrants.false_positives == 0
        assert result.quadrants.false_negatives == 0

    def test_false_positive_and_negative(self) -> None:
        # human Fail + judge Pass = false positive (dangerous)
        # human Pass + judge Fail = false negative (noisy)
        human = {"fp": "fail", "fn": "pass"}
        judge = {"fp": "pass", "fn": "fail"}
        result = alignment(human, judge)
        assert result.rate == 0.0
        assert result.quadrants.false_positives == 1
        assert result.quadrants.false_negatives == 1
        assert result.quadrants.matches == 0

    def test_skips_unpaired_conversations(self) -> None:
        human = {"only_human": "pass", "shared": "pass"}
        judge = {"only_judge": "fail", "shared": "pass"}
        result = alignment(human, judge)
        assert result.quadrants.total == 1
        assert result.rate == 1.0

    def test_empty_intersection_is_undefined(self) -> None:
        result = alignment({"a": "pass"}, {"b": "fail"})
        assert result.rate is None
        assert result.quadrants.total == 0


class TestDisagreementLabel:
    def test_agree_returns_none(self) -> None:
        assert disagreement_label("pass", "pass") is None
        assert disagreement_label("fail", "fail") is None

    def test_false_positive(self) -> None:
        assert disagreement_label("fail", "pass") == "false_positive"

    def test_false_negative(self) -> None:
        assert disagreement_label("pass", "fail") == "false_negative"
