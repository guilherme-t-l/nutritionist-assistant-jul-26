# Supabase-backed store for durable user accounts (profile + active meal plan).
#
# Guests never touch this file — they stay in SessionStore.
# Talks to Supabase over HTTPS (service-role key) — safe for serverless.

from __future__ import annotations

import json
import os
from dataclasses import dataclass

from supabase import Client, create_client

from agent.schemas import MealPlan, UserProfile


@dataclass
class UserRecord:
    username: str
    profile: UserProfile | None
    active_plan: MealPlan | None


def _require_env(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise RuntimeError(
            f"Missing {name}. Add it to .env (local) or Vercel env vars (deploy)."
        )
    return value


def _demo_passwords() -> dict[str, str]:
    """Parse DEMO_USER_PASSWORDS JSON, e.g. {"demo1":"password1",...}."""
    raw = _require_env("DEMO_USER_PASSWORDS")
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise RuntimeError(
            "DEMO_USER_PASSWORDS must be valid JSON, "
            'e.g. {"demo1":"password1","demo2":"password2"}'
        ) from exc
    if not isinstance(data, dict) or not data:
        raise RuntimeError("DEMO_USER_PASSWORDS must be a non-empty JSON object.")
    return {str(k): str(v) for k, v in data.items()}


class UserStore:
    """Tiny repository: credentials + durable profile/plan per username."""

    def __init__(self, client: Client | None = None) -> None:
        if client is None:
            client = create_client(
                _require_env("SUPABASE_URL"),
                _require_env("SUPABASE_SERVICE_ROLE_KEY"),
            )
        self._client = client
        self._seed_demo_users()

    def _seed_demo_users(self) -> None:
        """Insert demo rows if missing — never overwrite an existing password."""
        for username, password in _demo_passwords().items():
            existing = (
                self._client.table("users")
                .select("username")
                .eq("username", username)
                .limit(1)
                .execute()
            )
            if existing.data:
                continue
            self._client.table("users").insert(
                {
                    "username": username,
                    "password": password,
                    "profile_json": None,
                    "active_plan_json": None,
                }
            ).execute()

    def verify_credentials(self, username: str, password: str) -> bool:
        result = (
            self._client.table("users")
            .select("password")
            .eq("username", username)
            .limit(1)
            .execute()
        )
        if not result.data:
            return False
        return result.data[0]["password"] == password

    def get_user(self, username: str) -> UserRecord | None:
        result = (
            self._client.table("users")
            .select("username, profile_json, active_plan_json")
            .eq("username", username)
            .limit(1)
            .execute()
        )
        if not result.data:
            return None
        row = result.data[0]
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
        (
            self._client.table("users")
            .update({"profile_json": profile.model_dump_json()})
            .eq("username", username)
            .execute()
        )

    def save_plan(self, username: str, plan: MealPlan) -> None:
        """Update active_plan_json only — leaves profile_json alone."""
        (
            self._client.table("users")
            .update({"active_plan_json": plan.model_dump_json()})
            .eq("username", username)
            .execute()
        )

    def save_profile_and_plan(
        self, username: str, profile: UserProfile, plan: MealPlan
    ) -> None:
        """Write both columns — used after a successful POST /plan."""
        (
            self._client.table("users")
            .update(
                {
                    "profile_json": profile.model_dump_json(),
                    "active_plan_json": plan.model_dump_json(),
                }
            )
            .eq("username", username)
            .execute()
        )
