"""Aggregate SQLite rows into dashboard payloads.

Uses the pure functions in metrics.py for pass rate / alignment so the
numbers on the screen match the unit-tested definitions exactly.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import date
from pathlib import Path
from typing import Any

from eval_framework import db
from eval_framework.metrics import alignment, disagreement_label, pass_rate


def _parse_day(value: str | None) -> date | None:
    if not value:
        return None
    # Accept YYYY-MM-DD or full ISO timestamps; take the date part.
    return date.fromisoformat(value[:10])


def _conv_day(created_at: str) -> date:
    return date.fromisoformat(created_at[:10])


def _in_range(created_at: str, date_from: date | None, date_to: date | None) -> bool:
    day = _conv_day(created_at)
    if date_from and day < date_from:
        return False
    if date_to and day > date_to:
        return False
    return True


def _review_url(batch_id: str, index: int) -> str:
    return f"/batches/{batch_id}/review?i={index}"


def _rate_dict(pr: Any) -> dict[str, Any]:
    return {
        "passes": pr.passes,
        "fails": pr.fails,
        "total": pr.total,
        "rate": pr.rate,
    }


def collect_scoped_rows(
    *,
    batch_id: str | None,
    date_from: str | None,
    date_to: str | None,
    db_path: Path | str = db.DEFAULT_DB_PATH,
) -> list[dict[str, Any]]:
    """Return one dict per conversation in scope, with latest human/judge verdicts.

    Each row:
      conversation_id, batch_id, starter_id, category, created_at,
      index (position within batch), human (Verdict|None), judge (Verdict|None)
    """
    d_from = _parse_day(date_from)
    d_to = _parse_day(date_to)

    if batch_id:
        batches = [b for b in [db.get_batch(batch_id, db_path=db_path)] if b]
    else:
        batches = db.list_batches(db_path=db_path)

    rows: list[dict[str, Any]] = []
    for batch in batches:
        convs = db.list_conversations(batch.batch_id, db_path=db_path)
        human_map = db.latest_verdicts_for_batch(
            batch.batch_id, rater_type="human", db_path=db_path
        )
        judge_map = db.latest_verdicts_for_batch(
            batch.batch_id, rater_type="judge", db_path=db_path
        )
        for index, conv in enumerate(convs):
            if not _in_range(conv.created_at, d_from, d_to):
                continue
            rows.append(
                {
                    "conversation_id": conv.conversation_id,
                    "batch_id": conv.batch_id,
                    "batch_name": batch.name,
                    "starter_id": conv.starter_id,
                    "category": conv.category,
                    "created_at": conv.created_at,
                    "index": index,
                    "human": human_map.get(conv.conversation_id),
                    "judge": judge_map.get(conv.conversation_id),
                }
            )
    return rows


def build_dashboard_data(
    *,
    batch_id: str | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
    db_path: Path | str = db.DEFAULT_DB_PATH,
) -> dict[str, Any]:
    """Full JSON payload for GET /api/dashboard-data."""
    rows = collect_scoped_rows(
        batch_id=batch_id,
        date_from=date_from,
        date_to=date_to,
        db_path=db_path,
    )
    batches = [
        {"batch_id": b.batch_id, "name": b.name, "created_at": b.created_at}
        for b in db.list_batches(db_path=db_path)
    ]

    human_verdicts = [r["human"].verdict for r in rows if r["human"] is not None]
    judge_verdicts = [r["judge"].verdict for r in rows if r["judge"] is not None]
    human_pr = pass_rate(human_verdicts)
    judge_pr = pass_rate(judge_verdicts)

    human_by_id = {
        r["conversation_id"]: r["human"].verdict
        for r in rows
        if r["human"] is not None
    }
    judge_by_id = {
        r["conversation_id"]: r["judge"].verdict
        for r in rows
        if r["judge"] is not None
    }
    align = alignment(human_by_id, judge_by_id)

    reviewed = sum(1 for r in rows if r["human"] or r["judge"])

    return {
        "filters": {
            "batch": batch_id or "all",
            "from": date_from,
            "to": date_to,
        },
        "batches": batches,
        "overview": _overview(rows, human_pr, judge_pr, align, reviewed),
        "agent_vs_human": _agent_vs_human(rows, human_pr),
        "judge_vs_agent": _judge_vs_agent(rows, judge_pr, align),
        "judge_vs_human": _judge_vs_human(rows, align, human_by_id, judge_by_id),
    }


def _overview(
    rows: list[dict[str, Any]],
    human_pr: Any,
    judge_pr: Any,
    align: Any,
    reviewed: int,
) -> dict[str, Any]:
    # Pass-rate trend: one point per calendar day that has ratings.
    by_day_human: dict[str, list[str]] = defaultdict(list)
    by_day_judge: dict[str, list[str]] = defaultdict(list)
    for r in rows:
        day = r["created_at"][:10]
        if r["human"]:
            by_day_human[day].append(r["human"].verdict)
        if r["judge"]:
            by_day_judge[day].append(r["judge"].verdict)
    all_days = sorted(set(by_day_human) | set(by_day_judge))
    trend = []
    for day in all_days:
        h = pass_rate(by_day_human.get(day, []))
        j = pass_rate(by_day_judge.get(day, []))
        trend.append(
            {
                "date": day,
                "human_rate": h.rate,
                "judge_rate": j.rate,
                "human_n": h.total,
                "judge_n": j.total,
            }
        )

    return {
        "total_conversations": len(rows),
        "total_reviewed": reviewed,
        "human_pass_rate": _rate_dict(human_pr),
        "judge_pass_rate": _rate_dict(judge_pr),
        "alignment_rate": align.rate,
        "alignment_n": align.quadrants.total,
        "pass_rate_trend": trend,
        "comparison_types": [
            {
                "name": "Agent vs Human",
                "measures": "agent",
                "sample_size": human_pr.total,
                "pass_rate": human_pr.rate,
            },
            {
                "name": "Judge vs Agent",
                "measures": "agent",
                "sample_size": judge_pr.total,
                "pass_rate": judge_pr.rate,
            },
            {
                "name": "Judge vs Human",
                "measures": "judge",
                "sample_size": align.quadrants.total,
                "pass_rate": align.rate,  # alignment rate in this column
                "metric_label": "alignment_rate",
            },
        ],
    }


def _agent_vs_human(rows: list[dict[str, Any]], human_pr: Any) -> dict[str, Any]:
    by_category: dict[str, list[str]] = defaultdict(list)
    audit: list[dict[str, Any]] = []
    for r in rows:
        if r["human"] is None:
            continue
        by_category[r["category"]].append(r["human"].verdict)
        audit.append(
            {
                "conversation_id": r["conversation_id"],
                "batch_id": r["batch_id"],
                "starter_id": r["starter_id"],
                "category": r["category"],
                "verdict": r["human"].verdict,
                "reasoning": r["human"].reasoning,
                "created_at": r["human"].created_at,
                "review_url": _review_url(r["batch_id"], r["index"]),
            }
        )
    # Newest first in the audit log.
    audit.sort(key=lambda x: x["created_at"], reverse=True)

    category_bars = []
    for cat in sorted(by_category):
        pr = pass_rate(by_category[cat])
        category_bars.append(
            {
                "category": cat,
                "pass_rate": pr.rate,
                "passes": pr.passes,
                "fails": pr.fails,
                "total": pr.total,
            }
        )

    return {
        "pass_fail": {"pass": human_pr.passes, "fail": human_pr.fails},
        "pass_rate": _rate_dict(human_pr),
        "by_category": category_bars,
        "audit_log": audit,
    }


def _judge_vs_agent(
    rows: list[dict[str, Any]], judge_pr: Any, align: Any
) -> dict[str, Any]:
    failures = []
    for r in rows:
        j = r["judge"]
        if j is None or j.verdict != "fail":
            continue
        failures.append(
            {
                "conversation_id": r["conversation_id"],
                "batch_id": r["batch_id"],
                "starter_id": r["starter_id"],
                "category": r["category"],
                "reasoning": j.reasoning,
                "judge_prompt_version": j.judge_prompt_version,
                "judge_model": j.judge_model,
                "created_at": j.created_at,
                "review_url": _review_url(r["batch_id"], r["index"]),
            }
        )
    failures.sort(key=lambda x: x["created_at"], reverse=True)

    return {
        "pass_rate": _rate_dict(judge_pr),
        "alignment_rate": align.rate,
        "alignment_n": align.quadrants.total,
        "failures": failures,
    }


def _judge_vs_human(
    rows: list[dict[str, Any]],
    align: Any,
    human_by_id: dict[str, str],
    judge_by_id: dict[str, str],
) -> dict[str, Any]:
    q = align.quadrants
    disagreements = []
    shared = set(human_by_id) & set(judge_by_id)
    row_by_id = {r["conversation_id"]: r for r in rows}
    for conv_id in shared:
        h = human_by_id[conv_id]
        j = judge_by_id[conv_id]
        label = disagreement_label(h, j)
        if label is None:
            continue
        r = row_by_id[conv_id]
        disagreements.append(
            {
                "conversation_id": conv_id,
                "batch_id": r["batch_id"],
                "starter_id": r["starter_id"],
                "category": r["category"],
                "human_verdict": h,
                "judge_verdict": j,
                "label": label,
                "judge_reasoning": r["judge"].reasoning if r["judge"] else None,
                "human_reasoning": r["human"].reasoning if r["human"] else None,
                "review_url": _review_url(r["batch_id"], r["index"]),
            }
        )

    return {
        "alignment_rate": align.rate,
        "alignment_n": q.total,
        "quadrants": {
            "both_pass": q.both_pass,
            "both_fail": q.both_fail,
            "human_pass_judge_fail": q.human_pass_judge_fail,
            "human_fail_judge_pass": q.human_fail_judge_pass,
            "false_positives": q.false_positives,
            "false_negatives": q.false_negatives,
            "matches": q.matches,
            "total": q.total,
        },
        "disagreements": disagreements,
    }


__all__ = [
    "collect_scoped_rows",
    "build_dashboard_data",
]
