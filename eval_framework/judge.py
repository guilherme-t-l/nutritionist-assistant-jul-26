"""LLM-as-a-judge: load prompt → call JudgeLLM → write verdicts.

Blind by construction: the prompt builder never receives human verdicts.
Provenance: every judge verdict stores prompt version + model name.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from eval_framework import db
from eval_framework.adapters.base import JudgeLLM

PROMPTS_DIR = Path(__file__).resolve().parent / "judge_prompts"
DEFAULT_PROMPT_VERSION = "v1"

ProgressCallback = Callable[[int, int, str], None]


@dataclass(frozen=True)
class JudgeReply:
    verdict: str  # 'pass' | 'fail'
    reasoning: str


@dataclass(frozen=True)
class JudgeBatchResult:
    batch_id: str
    rated: int
    skipped: int
    errors: list[str]


def load_judge_prompt(version: str = DEFAULT_PROMPT_VERSION) -> str:
    """Load `judge_prompts/{version}.md` as the system/instructions text."""
    path = PROMPTS_DIR / f"{version}.md"
    if not path.is_file():
        raise FileNotFoundError(f"Judge prompt not found: {path}")
    return path.read_text(encoding="utf-8")


def build_judge_user_payload(
    *,
    context: dict[str, Any],
    transcript: list[dict[str, str]],
    starter_id: str,
    category: str,
) -> str:
    """Assemble the user-facing half of the judge prompt.

    Deliberately omits human verdicts — blindness is a property of this
    function's signature, not a comment we hope callers remember.
    """
    return (
        f"## Starter\n"
        f"- id: {starter_id}\n"
        f"- category: {category}\n\n"
        f"## Context (user profile / constraints)\n"
        f"```json\n{json.dumps(context, indent=2, ensure_ascii=False)}\n```\n\n"
        f"## Transcript\n"
        f"```json\n{json.dumps(transcript, indent=2, ensure_ascii=False)}\n```\n\n"
        f"Return your JSON verdict now."
    )


def compose_judge_prompt(
    instructions: str,
    *,
    context: dict[str, Any],
    transcript: list[dict[str, str]],
    starter_id: str,
    category: str,
) -> str:
    """Full prompt string sent to JudgeLLM.complete(...)."""
    payload = build_judge_user_payload(
        context=context,
        transcript=transcript,
        starter_id=starter_id,
        category=category,
    )
    return f"{instructions.strip()}\n\n---\n\n{payload}"


def parse_judge_reply(raw: str) -> JudgeReply:
    """Parse `{verdict, reasoning}` from model text; tolerate markdown fences."""
    text = raw.strip()
    # Strip ```json ... ``` if the model ignored mime-type instructions.
    fence = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, flags=re.DOTALL)
    if fence:
        text = fence.group(1)
    else:
        # Fall back to the first {...} object in the string.
        brace = re.search(r"\{.*\}", text, flags=re.DOTALL)
        if brace:
            text = brace.group(0)

    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ValueError(f"Judge reply is not valid JSON: {raw!r}") from exc

    verdict = str(data.get("verdict", "")).strip().lower()
    reasoning = str(data.get("reasoning", "")).strip()
    if verdict not in ("pass", "fail"):
        raise ValueError(f"Judge verdict must be pass|fail, got {verdict!r}")
    if not reasoning:
        reasoning = "(no reasoning provided)"
    return JudgeReply(verdict=verdict, reasoning=reasoning)


def judge_model_name(llm: JudgeLLM) -> str | None:
    """Best-effort model label for provenance (GeminiJudge exposes model_name)."""
    return getattr(llm, "model_name", None)


def judge_conversation(
    llm: JudgeLLM,
    conversation: db.Conversation,
    *,
    prompt_version: str = DEFAULT_PROMPT_VERSION,
    db_path: Path | str = db.DEFAULT_DB_PATH,
    instructions: str | None = None,
) -> db.Verdict:
    """Rate one conversation and persist a judge verdict."""
    instructions = instructions if instructions is not None else load_judge_prompt(prompt_version)
    prompt = compose_judge_prompt(
        instructions,
        context=conversation.context,
        transcript=conversation.transcript,
        starter_id=conversation.starter_id,
        category=conversation.category,
    )
    # Blindness check for tests / future auditors: human verdicts must never
    # appear in the prompt string we send.
    assert "human verdict" not in prompt.lower()

    raw = llm.complete(prompt)
    reply = parse_judge_reply(raw)
    return db.insert_verdict(
        conversation_id=conversation.conversation_id,
        rater_type="judge",
        verdict=reply.verdict,  # type: ignore[arg-type]
        reasoning=reply.reasoning,
        judge_prompt_version=prompt_version,
        judge_model=judge_model_name(llm),
        db_path=db_path,
    )


def judge_batch(
    batch_id: str,
    llm: JudgeLLM,
    *,
    prompt_version: str = DEFAULT_PROMPT_VERSION,
    db_path: Path | str = db.DEFAULT_DB_PATH,
    skip_already_judged: bool = True,
    on_progress: ProgressCallback | None = None,
) -> JudgeBatchResult:
    """Rate every conversation in a batch (Workflow 2 alignment / Workflow 3).

    By default skips conversations that already have a judge verdict, so
    re-running an alignment check is idempotent.
    """
    batch = db.get_batch(batch_id, db_path=db_path)
    if batch is None:
        raise KeyError(f"Unknown batch_id: {batch_id}")

    conversations = db.list_conversations(batch_id, db_path=db_path)
    existing = db.latest_verdicts_for_batch(
        batch_id, rater_type="judge", db_path=db_path
    )
    instructions = load_judge_prompt(prompt_version)

    rated = 0
    skipped = 0
    errors: list[str] = []
    total = len(conversations)

    for index, conv in enumerate(conversations):
        if skip_already_judged and conv.conversation_id in existing:
            skipped += 1
            if on_progress is not None:
                on_progress(index + 1, total, conv.starter_id)
            continue
        try:
            judge_conversation(
                llm,
                conv,
                prompt_version=prompt_version,
                db_path=db_path,
                instructions=instructions,
            )
            rated += 1
        except Exception as exc:  # noqa: BLE001 — collect and continue
            errors.append(f"{conv.starter_id}: {exc}")
        if on_progress is not None:
            on_progress(index + 1, total, conv.starter_id)

    return JudgeBatchResult(
        batch_id=batch_id,
        rated=rated,
        skipped=skipped,
        errors=errors,
    )


__all__ = [
    "PROMPTS_DIR",
    "DEFAULT_PROMPT_VERSION",
    "JudgeReply",
    "JudgeBatchResult",
    "load_judge_prompt",
    "build_judge_user_payload",
    "compose_judge_prompt",
    "parse_judge_reply",
    "judge_conversation",
    "judge_batch",
]
