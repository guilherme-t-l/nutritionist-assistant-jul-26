"""Pure metric functions for the eval framework.

No I/O, no LLM, no FastAPI — just math over verdict strings.
That makes these functions easy to unit-test and safe to call from
the dashboard API or the CLI without side effects.

Metric definitions (from the PRD):
  - Pass rate      = passes ÷ rated conversations (per rater type)
  - Alignment rate = conversations where human and judge match
                     ÷ conversations that have BOTH verdicts
  - False positive = judge Pass, human Fail  (dangerous: judge waves through a reject)
  - False negative = judge Fail, human Pass  (noisy but safe)
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Literal

VerdictValue = Literal["pass", "fail"]


@dataclass(frozen=True)
class PassRate:
    """Pass rate over a bag of binary verdicts."""

    passes: int
    fails: int
    total: int
    rate: float | None  # None when total == 0 (undefined, not zero)


@dataclass(frozen=True)
class AlignmentQuadrants:
    """2×2 agreement grid for human vs judge on the same conversations.

    Only conversations that have *both* a human and a judge verdict
    contribute to these counts.
    """

    both_pass: int
    both_fail: int
    human_pass_judge_fail: int  # false negative (from the human's POV)
    human_fail_judge_pass: int  # false positive (from the human's POV)

    @property
    def total(self) -> int:
        return (
            self.both_pass
            + self.both_fail
            + self.human_pass_judge_fail
            + self.human_fail_judge_pass
        )

    @property
    def matches(self) -> int:
        return self.both_pass + self.both_fail

    @property
    def false_positives(self) -> int:
        """Judge Pass + human Fail — the dangerous kind."""
        return self.human_fail_judge_pass

    @property
    def false_negatives(self) -> int:
        """Judge Fail + human Pass — noisy but safe."""
        return self.human_pass_judge_fail


@dataclass(frozen=True)
class Alignment:
    """Alignment summary: rate + the 2×2 grid that produced it."""

    quadrants: AlignmentQuadrants
    rate: float | None  # None when no paired verdicts exist


def pass_rate(verdicts: Iterable[str]) -> PassRate:
    """Compute pass rate from an iterable of 'pass' / 'fail' strings.

    Unknown values raise ValueError so typos surface immediately
    instead of silently skewing the rate.
    """
    passes = 0
    fails = 0
    for v in verdicts:
        if v == "pass":
            passes += 1
        elif v == "fail":
            fails += 1
        else:
            raise ValueError(f"Unknown verdict: {v!r} (expected 'pass' or 'fail')")
    total = passes + fails
    rate = (passes / total) if total else None
    return PassRate(passes=passes, fails=fails, total=total, rate=rate)


def alignment(
    human_by_conversation: dict[str, str],
    judge_by_conversation: dict[str, str],
) -> Alignment:
    """Compare human vs judge verdicts, keyed by conversation_id.

    Conversations missing either side are skipped — alignment is only
    defined where both raters have spoken. That matches the PRD:
    "conversations with both verdicts."
    """
    both_pass = 0
    both_fail = 0
    human_pass_judge_fail = 0
    human_fail_judge_pass = 0

    # Intersection: only ids present in both maps.
    shared_ids = set(human_by_conversation) & set(judge_by_conversation)
    for conv_id in shared_ids:
        h = human_by_conversation[conv_id]
        j = judge_by_conversation[conv_id]
        if h not in ("pass", "fail") or j not in ("pass", "fail"):
            raise ValueError(
                f"Unknown verdict pair for {conv_id!r}: human={h!r}, judge={j!r}"
            )
        if h == "pass" and j == "pass":
            both_pass += 1
        elif h == "fail" and j == "fail":
            both_fail += 1
        elif h == "pass" and j == "fail":
            human_pass_judge_fail += 1
        else:  # h == "fail" and j == "pass"
            human_fail_judge_pass += 1

    quads = AlignmentQuadrants(
        both_pass=both_pass,
        both_fail=both_fail,
        human_pass_judge_fail=human_pass_judge_fail,
        human_fail_judge_pass=human_fail_judge_pass,
    )
    rate = (quads.matches / quads.total) if quads.total else None
    return Alignment(quadrants=quads, rate=rate)


def disagreement_label(human: str, judge: str) -> str | None:
    """Classify a disagreeing pair, or return None if they agree.

    Returns 'false_positive' | 'false_negative' | None.
    Useful for the disagreement table on the Judge vs Human dashboard view.
    """
    if human == judge:
        return None
    if human == "fail" and judge == "pass":
        return "false_positive"
    if human == "pass" and judge == "fail":
        return "false_negative"
    raise ValueError(f"Unknown verdict pair: human={human!r}, judge={judge!r}")


__all__ = [
    "PassRate",
    "AlignmentQuadrants",
    "Alignment",
    "pass_rate",
    "alignment",
    "disagreement_label",
]
