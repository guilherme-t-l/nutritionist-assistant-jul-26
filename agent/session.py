# Supabase-backed conversation store, keyed by session ID.
#
# Previously an in-memory dict — fine on one long-lived process, useless on
# Vercel where each request can hit a fresh instance. Now every create/get/save
# is a round-trip to the `sessions` table.
#
# Mental model: mutate the Session object in the route, then call save().
# Without save(), the next serverless invocation will not see your changes.

from __future__ import annotations

import json
import os
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone

from supabase import Client, create_client

from agent.llm import Message
from agent.schemas import MealPlan, UserProfile


@dataclass
class Session:
    profile: UserProfile
    current_plan: MealPlan | None = None
    # `field(default_factory=list)` = each Session gets its own list.
    history: list[Message] = field(default_factory=list)


def _require_env(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise RuntimeError(
            f"Missing {name}. Add it to .env (local) or Vercel env vars (deploy)."
        )
    return value


def _history_to_json(history: list[Message]) -> str:
    return json.dumps([{"role": m.role, "content": m.content} for m in history])


def _history_from_json(raw: str | list | None) -> list[Message]:
    if raw is None or raw == "":
        return []
    data = json.loads(raw) if isinstance(raw, str) else raw
    return [Message(role=item["role"], content=item["content"]) for item in data]


class SessionStore:
    """Repository for guest/logged-in conversation state in Supabase."""

    def __init__(self, client: Client | None = None) -> None:
        if client is None:
            client = create_client(
                _require_env("SUPABASE_URL"),
                _require_env("SUPABASE_SERVICE_ROLE_KEY"),
            )
        self._client = client

    def create(
        self,
        profile: UserProfile,
        current_plan: MealPlan | None = None,
    ) -> tuple[str, Session]:
        session_id = uuid.uuid4().hex
        session = Session(profile=profile, current_plan=current_plan)
        self.save(session_id, session)
        return session_id, session

    def get(self, session_id: str) -> Session | None:
        result = (
            self._client.table("sessions")
            .select("profile_json, current_plan_json, history_json")
            .eq("session_id", session_id)
            .limit(1)
            .execute()
        )
        if not result.data:
            return None
        row = result.data[0]
        profile = UserProfile.model_validate_json(row["profile_json"])
        current_plan = (
            MealPlan.model_validate_json(row["current_plan_json"])
            if row["current_plan_json"]
            else None
        )
        history = _history_from_json(row["history_json"])
        return Session(profile=profile, current_plan=current_plan, history=history)

    def save(self, session_id: str, session: Session) -> None:
        """Write the full session row. Routes must call this after mutating."""
        row = {
            "session_id": session_id,
            "profile_json": session.profile.model_dump_json(),
            "current_plan_json": (
                session.current_plan.model_dump_json() if session.current_plan else None
            ),
            "history_json": _history_to_json(session.history),
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }
        self._client.table("sessions").upsert(row, on_conflict="session_id").execute()
