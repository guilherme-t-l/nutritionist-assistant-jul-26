# SQLite-backed store for durable user accounts (profile + active meal plan).
#
# Guests never touch this file — they stay in SessionStore (memory only).
# Do not reuse traces.db; auth/persistence lives in users.db.

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from pathlib import Path

from agent.schemas import MealPlan, UserProfile

# Project root / users.db — separate from traces.db.
DEFAULT_DB_PATH = Path(__file__).resolve().parent.parent / "users.db"

# MVP hardcoded credentials. Plaintext is intentional for this learning MVP.
DEMO_USERS: list[tuple[str, str]] = [
    ("demo1", "password1"),
    ("demo2", "password2"),
    ("demo3", "password3"),
    ("demo4", "password4"),
    ("demo5", "password5"),
]


@dataclass
class UserRecord:
    username: str
    profile: UserProfile | None
    active_plan: MealPlan | None


class UserStore:
    """Tiny repository: credentials + durable profile/plan per username."""

    def __init__(self, db_path: Path | str = DEFAULT_DB_PATH) -> None:
        self._db_path = Path(db_path)
        self._init_db()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self._db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS users (
                    username TEXT PRIMARY KEY,
                    password TEXT NOT NULL,
                    profile_json TEXT,
                    active_plan_json TEXT
                )
                """
            )
            for username, password in DEMO_USERS:
                conn.execute(
                    """
                    INSERT OR IGNORE INTO users (username, password, profile_json, active_plan_json)
                    VALUES (?, ?, NULL, NULL)
                    """,
                    (username, password),
                )
            conn.commit()

    def verify_credentials(self, username: str, password: str) -> bool:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT password FROM users WHERE username = ?",
                (username,),
            ).fetchone()
        if row is None:
            return False
        return row["password"] == password

    def get_user(self, username: str) -> UserRecord | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT username, profile_json, active_plan_json FROM users WHERE username = ?",
                (username,),
            ).fetchone()
        if row is None:
            return None
        profile = (
            UserProfile.model_validate_json(row["profile_json"])
            if row["profile_json"]
            else None
        )
        active_plan = (
            MealPlan.model_validate_json(row["active_plan_json"])
            if row["active_plan_json"]
            else None
        )
        return UserRecord(
            username=row["username"],
            profile=profile,
            active_plan=active_plan,
        )

    def save_profile(self, username: str, profile: UserProfile) -> None:
        """Update profile_json only — leaves active_plan_json alone."""
        with self._connect() as conn:
            conn.execute(
                "UPDATE users SET profile_json = ? WHERE username = ?",
                (profile.model_dump_json(), username),
            )
            conn.commit()

    def save_plan(self, username: str, plan: MealPlan) -> None:
        """Update active_plan_json only — leaves profile_json alone."""
        with self._connect() as conn:
            conn.execute(
                "UPDATE users SET active_plan_json = ? WHERE username = ?",
                (plan.model_dump_json(), username),
            )
            conn.commit()

    def save_profile_and_plan(
        self, username: str, profile: UserProfile, plan: MealPlan
    ) -> None:
        """Write both columns — used after a successful POST /plan."""
        with self._connect() as conn:
            conn.execute(
                """
                UPDATE users
                SET profile_json = ?, active_plan_json = ?
                WHERE username = ?
                """,
                (profile.model_dump_json(), plan.model_dump_json(), username),
            )
            conn.commit()
