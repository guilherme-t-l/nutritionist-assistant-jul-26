"""Starter file → batch of stored conversations.

Owns the Workflow 1 / Workflow 3 generation half:
  load starters.json → call TargetAgent per starter → write to SQLite.

No FastAPI, no LLM SDK — just orchestration. The UI (Step 3) will call
`generate_batch` from a background task; tests call it with a FakeAgent.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from eval_framework import db
from eval_framework.adapters.base import ConversationStarter, TargetAgent

# Default location for checked-in starter files.
STARTERS_DIR = Path(__file__).resolve().parent / "starters"

ProgressCallback = Callable[[int, int, str], None]
"""(completed, total, starter_id) — optional hook for the Batches UI."""


@dataclass(frozen=True)
class StarterFile:
    """Parsed contents of one starters/*.json file."""

    name: str
    description: str
    starters: list[ConversationStarter]
    path: Path


def load_starter_file(path: Path | str) -> StarterFile:
    """Parse a starter JSON file into typed dataclasses.

    Raises ValueError with a clear message if required keys are missing —
    better to fail at load time than mid-batch.
    """
    file_path = Path(path)
    raw = json.loads(file_path.read_text(encoding="utf-8"))
    if "name" not in raw or "starters" not in raw:
        raise ValueError(
            f"{file_path.name}: starter file must have 'name' and 'starters' keys"
        )
    starters: list[ConversationStarter] = []
    for i, item in enumerate(raw["starters"]):
        for key in ("id", "category", "context", "turns"):
            if key not in item:
                raise ValueError(
                    f"{file_path.name}: starters[{i}] missing required key {key!r}"
                )
        if not isinstance(item["turns"], list) or not item["turns"]:
            raise ValueError(
                f"{file_path.name}: starters[{i}] ({item['id']!r}) "
                "needs a non-empty 'turns' list"
            )
        starters.append(
            ConversationStarter(
                id=item["id"],
                category=item["category"],
                context=item["context"],
                turns=list(item["turns"]),
            )
        )
    return StarterFile(
        name=raw["name"],
        description=raw.get("description", ""),
        starters=starters,
        path=file_path,
    )


def list_starter_files(directory: Path | str = STARTERS_DIR) -> list[Path]:
    """Return sorted paths of *.json starter files in `directory`."""
    root = Path(directory)
    if not root.is_dir():
        return []
    return sorted(root.glob("*.json"))


def populate_batch(
    batch_id: str,
    agent: TargetAgent,
    starter_file: Path | str | StarterFile,
    *,
    mode: str = "manual",
    db_path: Path | str = db.DEFAULT_DB_PATH,
    on_progress: ProgressCallback | None = None,
) -> db.Batch:
    """Fill an existing batch (status=generating) with conversations.

    Used by the FastAPI background task: the route creates the batch row
    immediately so the UI can poll, then this runs off-request.
    """
    loaded = (
        starter_file
        if isinstance(starter_file, StarterFile)
        else load_starter_file(starter_file)
    )
    batch = db.get_batch(batch_id, db_path=db_path)
    if batch is None:
        raise KeyError(f"Unknown batch_id: {batch_id}")

    total = len(loaded.starters)
    try:
        for index, starter in enumerate(loaded.starters):
            conversation = agent.run_conversation(starter)
            db.insert_conversation(
                batch_id=batch_id,
                starter_id=starter.id,
                category=starter.category,
                context=starter.context,
                transcript=conversation.transcript,
                agent_meta=_with_mode(conversation.agent_meta, mode),
                db_path=db_path,
            )
            if on_progress is not None:
                on_progress(index + 1, total, starter.id)
        db.update_batch_status(batch_id, "ready", db_path=db_path)
    except Exception:
        db.update_batch_status(batch_id, "error", db_path=db_path)
        raise

    refreshed = db.get_batch(batch_id, db_path=db_path)
    assert refreshed is not None
    return refreshed


def generate_batch(
    agent: TargetAgent,
    starter_file: Path | str | StarterFile,
    *,
    mode: str = "manual",
    db_path: Path | str = db.DEFAULT_DB_PATH,
    on_progress: ProgressCallback | None = None,
    batch_id: str | None = None,
) -> db.Batch:
    """Create a batch row, then populate it. Convenience for CLI / tests.

    `mode` is recorded as provenance ("manual" | "judge"); judge auto-rating
    is wired in Step 4 after generation returns.

    Per-starter HTTP failures are NOT fatal — they land in agent_meta and
    the batch still completes as `ready`. Unexpected exceptions mark the
    batch `error` and re-raise.
    """
    loaded = (
        starter_file
        if isinstance(starter_file, StarterFile)
        else load_starter_file(starter_file)
    )
    batch = db.create_batch(
        name=loaded.name,
        starter_file=loaded.path.name,
        batch_id=batch_id,
        status="generating",
        db_path=db_path,
    )
    return populate_batch(
        batch.batch_id,
        agent,
        loaded,
        mode=mode,
        db_path=db_path,
        on_progress=on_progress,
    )


def _with_mode(agent_meta: dict[str, Any], mode: str) -> dict[str, Any]:
    """Attach generation mode without mutating the adapter's original dict."""
    return {**agent_meta, "generation_mode": mode}


__all__ = [
    "STARTERS_DIR",
    "StarterFile",
    "load_starter_file",
    "list_starter_files",
    "populate_batch",
    "generate_batch",
]
