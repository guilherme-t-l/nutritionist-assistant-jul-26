"""SQLite store for the eval framework.

Three tables — batches, conversations, verdicts — matching the PRD schema.
SQLite (not Supabase) so the framework stays portable: copy the folder,
no credentials, it works. Same trade-off as the project's `traces.db`.

Teaching notes:
  - `sqlite3` is stdlib (no new dependency).
  - `with sqlite3.connect(...) as conn` commits on clean exit, rolls back
    on exception — the context-manager pattern from file I/O.
  - `?` placeholders + a tuple of values is the only safe way to pass data
    into SQL. Never interpolate with f-strings.
"""

from __future__ import annotations

import json
import sqlite3
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

# Resolves relative to THIS file so callers find the same db whether
# invoked from the project root or from inside eval_framework/.
DEFAULT_DB_PATH = Path(__file__).resolve().parent / "data" / "evals.db"

BatchStatus = Literal["generating", "ready", "error"]
RaterType = Literal["human", "judge"]
VerdictValue = Literal["pass", "fail"]


# ---------- row shapes (plain dataclasses the rest of the framework uses) ------


@dataclass(frozen=True)
class Batch:
    batch_id: str
    name: str
    starter_file: str
    created_at: str
    status: str


@dataclass(frozen=True)
class Conversation:
    conversation_id: str
    batch_id: str
    starter_id: str
    category: str
    context: dict[str, Any]
    transcript: list[dict[str, str]]
    agent_meta: dict[str, Any]
    created_at: str


@dataclass(frozen=True)
class Verdict:
    verdict_id: str
    conversation_id: str
    rater_type: str
    verdict: str
    reasoning: str | None
    judge_prompt_version: str | None
    judge_model: str | None
    created_at: str


_SCHEMA = """
CREATE TABLE IF NOT EXISTS batches (
    batch_id     TEXT PRIMARY KEY,
    name         TEXT NOT NULL,
    starter_file TEXT NOT NULL,
    created_at   TEXT NOT NULL,
    status       TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS conversations (
    conversation_id TEXT PRIMARY KEY,
    batch_id        TEXT NOT NULL REFERENCES batches(batch_id),
    starter_id      TEXT NOT NULL,
    category        TEXT NOT NULL,
    context_json    TEXT NOT NULL,
    transcript_json TEXT NOT NULL,
    agent_meta_json TEXT NOT NULL,
    created_at      TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS verdicts (
    verdict_id           TEXT PRIMARY KEY,
    conversation_id      TEXT NOT NULL REFERENCES conversations(conversation_id),
    rater_type           TEXT NOT NULL,
    verdict              TEXT NOT NULL,
    reasoning            TEXT,
    judge_prompt_version TEXT,
    judge_model          TEXT,
    created_at           TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_conversations_batch
    ON conversations(batch_id);
CREATE INDEX IF NOT EXISTS idx_verdicts_conversation
    ON verdicts(conversation_id);
"""


def _utc_now() -> str:
    """ISO-8601 UTC timestamp with millisecond precision + Z suffix.

    Milliseconds matter: two batches created in the same second would
    otherwise sort ambiguously on the Batches home screen.
    """
    return (
        datetime.now(timezone.utc)
        .isoformat(timespec="milliseconds")
        .replace("+00:00", "Z")
    )


def _new_id() -> str:
    """Random UUID hex — short enough for URLs, unique enough for local use."""
    return uuid.uuid4().hex


def init_db(db_path: Path | str = DEFAULT_DB_PATH) -> Path:
    """Create the db file and tables if they don't exist. Idempotent."""
    path = Path(db_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(path) as conn:
        conn.executescript(_SCHEMA)
    return path


def _connect(db_path: Path | str) -> sqlite3.Connection:
    """Open a connection with Row factory so we can index columns by name."""
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    # Enforce FK constraints (off by default in SQLite).
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


# ---------- batches ------------------------------------------------------------


def create_batch(
    *,
    name: str,
    starter_file: str,
    batch_id: str | None = None,
    status: BatchStatus = "generating",
    db_path: Path | str = DEFAULT_DB_PATH,
) -> Batch:
    """Insert a new batch row. Defaults to status='generating'."""
    init_db(db_path)
    row = Batch(
        batch_id=batch_id or f"{name}-{_utc_now()[:10]}-{_new_id()[:8]}",
        name=name,
        starter_file=starter_file,
        created_at=_utc_now(),
        status=status,
    )
    with _connect(db_path) as conn:
        conn.execute(
            """
            INSERT INTO batches (batch_id, name, starter_file, created_at, status)
            VALUES (?, ?, ?, ?, ?)
            """,
            (row.batch_id, row.name, row.starter_file, row.created_at, row.status),
        )
    return row


def update_batch_status(
    batch_id: str,
    status: BatchStatus,
    *,
    db_path: Path | str = DEFAULT_DB_PATH,
) -> None:
    """Flip a batch's status (generating → ready | error)."""
    with _connect(db_path) as conn:
        cursor = conn.execute(
            "UPDATE batches SET status = ? WHERE batch_id = ?",
            (status, batch_id),
        )
        if cursor.rowcount == 0:
            raise KeyError(f"Unknown batch_id: {batch_id}")


def get_batch(batch_id: str, *, db_path: Path | str = DEFAULT_DB_PATH) -> Batch | None:
    with _connect(db_path) as conn:
        row = conn.execute(
            "SELECT * FROM batches WHERE batch_id = ?", (batch_id,)
        ).fetchone()
    return _batch_from_row(row) if row else None


def list_batches(*, db_path: Path | str = DEFAULT_DB_PATH) -> list[Batch]:
    """Newest first — matches the Batches home screen ordering."""
    init_db(db_path)
    with _connect(db_path) as conn:
        # rowid DESC is the tiebreaker when two batches share a timestamp.
        rows = conn.execute(
            "SELECT * FROM batches ORDER BY created_at DESC, rowid DESC"
        ).fetchall()
    return [_batch_from_row(r) for r in rows]


def _batch_from_row(row: sqlite3.Row) -> Batch:
    return Batch(
        batch_id=row["batch_id"],
        name=row["name"],
        starter_file=row["starter_file"],
        created_at=row["created_at"],
        status=row["status"],
    )


# ---------- conversations ------------------------------------------------------


def insert_conversation(
    *,
    batch_id: str,
    starter_id: str,
    category: str,
    context: dict[str, Any],
    transcript: list[dict[str, str]],
    agent_meta: dict[str, Any] | None = None,
    conversation_id: str | None = None,
    db_path: Path | str = DEFAULT_DB_PATH,
) -> Conversation:
    """Persist one generated conversation under a batch."""
    row = Conversation(
        conversation_id=conversation_id or _new_id(),
        batch_id=batch_id,
        starter_id=starter_id,
        category=category,
        context=context,
        transcript=transcript,
        agent_meta=agent_meta or {},
        created_at=_utc_now(),
    )
    with _connect(db_path) as conn:
        conn.execute(
            """
            INSERT INTO conversations (
                conversation_id, batch_id, starter_id, category,
                context_json, transcript_json, agent_meta_json, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                row.conversation_id,
                row.batch_id,
                row.starter_id,
                row.category,
                json.dumps(row.context),
                json.dumps(row.transcript),
                json.dumps(row.agent_meta),
                row.created_at,
            ),
        )
    return row


def get_conversation(
    conversation_id: str, *, db_path: Path | str = DEFAULT_DB_PATH
) -> Conversation | None:
    with _connect(db_path) as conn:
        row = conn.execute(
            "SELECT * FROM conversations WHERE conversation_id = ?",
            (conversation_id,),
        ).fetchone()
    return _conversation_from_row(row) if row else None


def list_conversations(
    batch_id: str, *, db_path: Path | str = DEFAULT_DB_PATH
) -> list[Conversation]:
    """All conversations in a batch, oldest first (stable review order)."""
    with _connect(db_path) as conn:
        rows = conn.execute(
            """
            SELECT * FROM conversations
            WHERE batch_id = ?
            ORDER BY created_at ASC, starter_id ASC
            """,
            (batch_id,),
        ).fetchall()
    return [_conversation_from_row(r) for r in rows]


def _conversation_from_row(row: sqlite3.Row) -> Conversation:
    return Conversation(
        conversation_id=row["conversation_id"],
        batch_id=row["batch_id"],
        starter_id=row["starter_id"],
        category=row["category"],
        context=json.loads(row["context_json"]),
        transcript=json.loads(row["transcript_json"]),
        agent_meta=json.loads(row["agent_meta_json"]),
        created_at=row["created_at"],
    )


# ---------- verdicts -----------------------------------------------------------


def insert_verdict(
    *,
    conversation_id: str,
    rater_type: RaterType,
    verdict: VerdictValue,
    reasoning: str | None = None,
    judge_prompt_version: str | None = None,
    judge_model: str | None = None,
    verdict_id: str | None = None,
    db_path: Path | str = DEFAULT_DB_PATH,
) -> Verdict:
    """Append a verdict. Re-rating inserts a new row; latest wins on read.

    Human verdicts leave judge_* as None. Judge verdicts should always
    carry prompt version + model name for provenance.
    """
    if verdict not in ("pass", "fail"):
        raise ValueError(f"verdict must be 'pass' or 'fail', got {verdict!r}")
    if rater_type not in ("human", "judge"):
        raise ValueError(f"rater_type must be 'human' or 'judge', got {rater_type!r}")

    row = Verdict(
        verdict_id=verdict_id or _new_id(),
        conversation_id=conversation_id,
        rater_type=rater_type,
        verdict=verdict,
        reasoning=reasoning,
        judge_prompt_version=judge_prompt_version,
        judge_model=judge_model,
        created_at=_utc_now(),
    )
    with _connect(db_path) as conn:
        conn.execute(
            """
            INSERT INTO verdicts (
                verdict_id, conversation_id, rater_type, verdict,
                reasoning, judge_prompt_version, judge_model, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                row.verdict_id,
                row.conversation_id,
                row.rater_type,
                row.verdict,
                row.reasoning,
                row.judge_prompt_version,
                row.judge_model,
                row.created_at,
            ),
        )
    return row


def list_verdicts_for_conversation(
    conversation_id: str, *, db_path: Path | str = DEFAULT_DB_PATH
) -> list[Verdict]:
    """All verdicts for one conversation, oldest first."""
    with _connect(db_path) as conn:
        rows = conn.execute(
            """
            SELECT * FROM verdicts
            WHERE conversation_id = ?
            ORDER BY created_at ASC
            """,
            (conversation_id,),
        ).fetchall()
    return [_verdict_from_row(r) for r in rows]


def latest_verdicts_for_batch(
    batch_id: str,
    *,
    rater_type: RaterType | None = None,
    db_path: Path | str = DEFAULT_DB_PATH,
) -> dict[str, Verdict]:
    """Latest verdict per conversation for a batch (optionally filtered by rater).

    Returns {conversation_id: Verdict}. "Latest wins" supports re-editable
    human ratings without deleting history.
    """
    # Strategy: pull all matching verdicts ordered by time, then keep the
    # last one seen per conversation_id. Simple and correct for MVP sizes
    # (tens of conversations, not millions).
    sql = """
        SELECT v.*
        FROM verdicts v
        JOIN conversations c ON c.conversation_id = v.conversation_id
        WHERE c.batch_id = ?
    """
    params: list[Any] = [batch_id]
    if rater_type is not None:
        sql += " AND v.rater_type = ?"
        params.append(rater_type)
    sql += " ORDER BY v.created_at ASC, v.rowid ASC"

    with _connect(db_path) as conn:
        rows = conn.execute(sql, params).fetchall()

    latest: dict[str, Verdict] = {}
    for row in rows:
        v = _verdict_from_row(row)
        latest[v.conversation_id] = v
    return latest


def _verdict_from_row(row: sqlite3.Row) -> Verdict:
    return Verdict(
        verdict_id=row["verdict_id"],
        conversation_id=row["conversation_id"],
        rater_type=row["rater_type"],
        verdict=row["verdict"],
        reasoning=row["reasoning"],
        judge_prompt_version=row["judge_prompt_version"],
        judge_model=row["judge_model"],
        created_at=row["created_at"],
    )


__all__ = [
    "DEFAULT_DB_PATH",
    "Batch",
    "Conversation",
    "Verdict",
    "init_db",
    "create_batch",
    "update_batch_status",
    "get_batch",
    "list_batches",
    "insert_conversation",
    "get_conversation",
    "list_conversations",
    "insert_verdict",
    "list_verdicts_for_conversation",
    "latest_verdicts_for_batch",
]
