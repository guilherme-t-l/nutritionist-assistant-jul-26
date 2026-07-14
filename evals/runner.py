"""Phase 3 eval runner. Run with:  `python -m evals.runner`

For each synthetic profile:
  1. Build the system prompt + first user message (same code path /plan uses).
  2. Call the LLM. Trace the call to traces.db.
  3. Score the reply on four metrics.
  4. Print one row per profile, plus an aggregate footer per metric.

CLI flags (kept tiny — this is a learning tool, not a CLI app):
  --limit N         only run the first N profiles (handy for quick iteration)
  --skip-judge      skip cuisine_relevance (useful when no API key is free)
  --no-trace        don't write to traces.db (for dry runs)

How data flows in one row:
  profile (json) → UserProfile (validated) → prompt strings → llm.chat()
    → raw_reply → MealPlan (parsed) → 4 metric scores → printed row + trace.

Concurrency: deliberately serial. Gemini's free tier rate-limits aggressively,
and a serial loop is enormously easier to debug. Phase 4 may revisit.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

# `dotenv` isn't a runtime requirement of agent/llm.py, but the eval runner
# is meant to be invoked from the CLI, so we load .env explicitly here.
from dotenv import load_dotenv

from agent.llm import LLM, GeminiLLM, Message
from agent.prompts import build_initial_user_message, build_system_prompt
from agent.schemas import MealPlan, UserProfile
from agent.tracing import Trace, init_db, record_trace
from evals.metrics import MetricResult, allergen_leak, cuisine_relevance, json_valid, target_accuracy

# Resolved relative to THIS file so `python -m evals.runner` works no matter
# which directory you invoke it from.
PROFILES_PATH = Path(__file__).resolve().parent / "datasets" / "profiles.json"


@dataclass
class RowResult:
    """One profile's full result — passed to the printer and (optionally) JSON dump."""

    label: str
    raw_reply: str
    latency_ms: int
    error: str | None
    metrics: dict[str, MetricResult]


def main(argv: list[str] | None = None) -> int:
    """Entry point. Returns a process exit code."""
    args = _parse_args(argv)
    load_dotenv()

    profiles = _load_profiles(args.limit)
    print(f"Loaded {len(profiles)} profile(s) from {PROFILES_PATH.name}.")

    llm = _build_llm()

    if not args.no_trace:
        db_path = init_db()
        print(f"Tracing to {db_path}")

    rows: list[RowResult] = []
    for i, (label, profile) in enumerate(profiles, start=1):
        print(f"\n[{i}/{len(profiles)}] {label} ...", flush=True)
        row = _run_one(label, profile, llm, judge=llm if not args.skip_judge else None)
        rows.append(row)

        if not args.no_trace:
            _persist_trace(row, profile)

        # Per-row inline summary so a long run streams useful output
        # rather than going silent until the end.
        _print_row_inline(row)

    print()
    _print_summary(rows)
    return 0


# ---------- core per-profile logic -------------------------------------------


def _run_one(
    label: str,
    profile: UserProfile,
    llm: LLM,
    judge: LLM | None,
) -> RowResult:
    """Run one profile through the agent and score the reply on all metrics."""
    # Same layout as POST /plan: persona + profile in system, short task as user.
    system_prompt = build_system_prompt(profile)
    user_message = Message(role="user", content=build_initial_user_message())

    raw_reply = ""
    error: str | None = None
    started = time.perf_counter()
    try:
        raw_reply = llm.chat(
            messages=[user_message],
            system=system_prompt,
            response_schema=MealPlan,
        )
    except Exception as exc:  # noqa: BLE001 — top-level eval safety net
        # Bare `except Exception` is normally a smell, but here the runner's
        # job is to keep going even if one profile blows up. We capture the
        # message and record a 0-score row.
        error = f"{type(exc).__name__}: {exc}"
    latency_ms = int((time.perf_counter() - started) * 1000)

    metrics: dict[str, MetricResult] = {}

    # 1. JSON valid — operates on the raw string, even if the call errored.
    metrics["json_valid"] = json_valid.score(raw_reply)

    # The remaining metrics need a parsed plan. If parsing fails, we record
    # 0/fail across the board — it's the honest answer.
    try:
        plan = MealPlan.model_validate_json(raw_reply) if raw_reply else None
    except Exception as exc:  # noqa: BLE001 — same rationale
        plan = None
        if error is None:
            error = f"parse failed: {exc}"

    if plan is None:
        skipped = MetricResult(score=0.0, passed=False, details="no parseable plan")
        metrics["allergen_leak"] = skipped
        metrics["target_accuracy"] = skipped
        metrics["cuisine_relevance"] = skipped
    else:
        metrics["allergen_leak"] = allergen_leak.score(plan, profile)
        metrics["target_accuracy"] = target_accuracy.score(plan, profile)
        if judge is not None:
            metrics["cuisine_relevance"] = cuisine_relevance.score(plan, profile, judge)
        else:
            metrics["cuisine_relevance"] = MetricResult(
                score=0.0, passed=False, details="judge skipped"
            )

    return RowResult(
        label=label,
        raw_reply=raw_reply,
        latency_ms=latency_ms,
        error=error,
        metrics=metrics,
    )


# ---------- IO: profiles, LLM, traces ----------------------------------------


def _load_profiles(limit: int | None) -> list[tuple[str, UserProfile]]:
    """Read profiles.json and validate each entry as a UserProfile."""
    data = json.loads(PROFILES_PATH.read_text())
    out: list[tuple[str, UserProfile]] = []
    for entry in data:
        # `model_validate` accepts a dict (vs. `_json` which accepts a str).
        # If a profile has bad shape, this raises with a clear error — better
        # to fail fast on dataset issues than silently skip.
        profile = UserProfile.model_validate(entry["profile"])
        out.append((entry["label"], profile))
    if limit is not None:
        out = out[:limit]
    return out


def _build_llm() -> LLM:
    """Build a real Gemini client. Errors loudly if no API key is set."""
    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key or api_key == "your-key-here":
        raise SystemExit(
            "GEMINI_API_KEY not set. Put a real key in .env, or run with "
            "--limit 0 if you only want to validate the runner wiring."
        )
    return GeminiLLM(api_key=api_key)


def _persist_trace(row: RowResult, profile: UserProfile) -> None:
    """Write one trace row per (profile, plan) result.

    `extra` carries metric scores so a later notebook can pivot on them
    without re-running anything.
    """
    extra: dict[str, Any] = {
        "metrics": {name: asdict(result) for name, result in row.metrics.items()},
    }
    record_trace(
        Trace(
            profile_label=row.label,
            kind="plan",
            system_prompt=build_system_prompt(profile),
            user_messages=[
                {"role": "user", "content": build_initial_user_message()}
            ],
            raw_reply=row.raw_reply,
            latency_ms=row.latency_ms,
            error=row.error,
            extra=extra,
        )
    )


# ---------- pretty printing ---------------------------------------------------


_METRIC_ORDER = ["json_valid", "allergen_leak", "target_accuracy", "cuisine_relevance"]


def _print_row_inline(row: RowResult) -> None:
    """One-line streaming summary so long runs aren't silent."""
    if row.error:
        print(f"  ERROR: {row.error}")
        return
    cells = [
        f"{name}={'OK' if row.metrics[name].passed else 'FAIL'}({row.metrics[name].score:.2f})"
        for name in _METRIC_ORDER
    ]
    print(f"  {row.latency_ms} ms | " + " | ".join(cells))


def _print_summary(rows: list[RowResult]) -> None:
    """Final per-profile table + aggregate per metric. Stdout only — no
    template engine, no rich.print, just `print()`. Easy to copy into a
    notes file."""

    if not rows:
        print("No rows — nothing to summarize.")
        return

    # Per-profile table
    label_w = max(len(r.label) for r in rows)
    header = f"{'profile'.ljust(label_w)}  " + "  ".join(
        name.rjust(8) for name in _METRIC_ORDER
    )
    print("=" * len(header))
    print(header)
    print("-" * len(header))
    for r in rows:
        cells = [
            f"{r.metrics[name].score:.2f}".rjust(8) for name in _METRIC_ORDER
        ]
        print(f"{r.label.ljust(label_w)}  " + "  ".join(cells))
    print("-" * len(header))

    # Aggregate footer (mean + pass-rate per metric)
    means = {
        name: sum(r.metrics[name].score for r in rows) / len(rows)
        for name in _METRIC_ORDER
    }
    pass_rates = {
        name: sum(1 for r in rows if r.metrics[name].passed) / len(rows)
        for name in _METRIC_ORDER
    }
    mean_row = "  ".join(f"{means[name]:.2f}".rjust(8) for name in _METRIC_ORDER)
    pass_row = "  ".join(
        f"{int(pass_rates[name] * 100)}%".rjust(8) for name in _METRIC_ORDER
    )
    print(f"{'mean'.ljust(label_w)}  " + mean_row)
    print(f"{'pass %'.ljust(label_w)}  " + pass_row)
    print("=" * len(header))

    n_errors = sum(1 for r in rows if r.error)
    if n_errors:
        print(f"\n{n_errors}/{len(rows)} profile(s) errored — see traces.db for details.")


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    """Tiny CLI surface — flags only for the things you'd actually toggle."""
    parser = argparse.ArgumentParser(
        prog="evals.runner",
        description="Run all profiles through the agent and score the replies.",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Only run the first N profiles (handy for quick iteration).",
    )
    parser.add_argument(
        "--skip-judge",
        action="store_true",
        help="Skip cuisine_relevance (it makes a second LLM call per row).",
    )
    parser.add_argument(
        "--no-trace",
        action="store_true",
        help="Do not write traces to traces.db.",
    )
    return parser.parse_args(argv)


if __name__ == "__main__":
    sys.exit(main())
