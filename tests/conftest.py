"""Shared pytest fixtures.

A `TestClient` plus a `FakeLLM` that records every call and replies with
a canned MealPlan JSON. FakeUserStore / FakeSessionStore keep tests offline
(no Supabase credentials needed).
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import Iterator

import pytest
from fastapi.testclient import TestClient
from pydantic import BaseModel

from agent.llm import Message
from agent.schemas import MealPlan, UserProfile
from agent.session import Session
from agent.users import UserRecord
from src.app.dependencies import get_llm, get_session_store, get_user_store
from src.app.main import app


CANNED_PLAN_JSON = """
{
  "meals": [
    {
      "name": "Tapioca com queijo",
      "description": "Simple Brazilian breakfast.",
      "ingredients": [
        {"name": "tapioca flour", "quantity": "4 colheres de sopa", "calories": 250, "protein_g": 0, "carbs_g": 55, "fat_g": 0},
        {"name": "cheese",        "quantity": "40 g",               "calories": 150, "protein_g": 15, "carbs_g": 0,  "fat_g": 12}
      ]
    },
    {
      "name": "Feijoada lite",
      "description": "Lightened feijoada.",
      "ingredients": [
        {"name": "black beans", "quantity": "1 concha",    "calories": 200, "protein_g": 15, "carbs_g": 35, "fat_g": 1},
        {"name": "pork",        "quantity": "100 g",       "calories": 300, "protein_g": 25, "carbs_g": 0,  "fat_g": 19},
        {"name": "rice",        "quantity": "1/2 xícara",  "calories": 200, "protein_g": 0,  "carbs_g": 45, "fat_g": 0}
      ]
    },
    {
      "name": "Grilled fish with farofa",
      "description": "Grilled tilapia with manioc farofa.",
      "ingredients": [
        {"name": "tilapia",       "quantity": "150 g",           "calories": 250, "protein_g": 40, "carbs_g": 0,  "fat_g": 10},
        {"name": "cassava flour", "quantity": "3 colheres",      "calories": 300, "protein_g": 2,  "carbs_g": 50, "fat_g": 5},
        {"name": "onion",         "quantity": "1/2 unidade",     "calories": 50,  "protein_g": 3,  "carbs_g": 0,  "fat_g": 3}
      ]
    }
  ],
  "notes": "Balanced day."
}
"""

# Same five demos the real store seeds from DEMO_USER_PASSWORDS — hardcoded
# here so tests never need that env var.
_DEMO_PASSWORDS = {
    "demo1": "password1",
    "demo2": "password2",
    "demo3": "password3",
    "demo4": "password4",
    "demo5": "password5",
}


@dataclass
class FakeLLM:
    """Deterministic stand-in for GeminiLLM.

    Records every `chat` call so tests can assert on what was sent,
    and returns `canned_reply` each time.
    """

    canned_reply: str = CANNED_PLAN_JSON
    calls: list[dict] = field(default_factory=list)

    def chat(
        self,
        messages: list[Message],
        *,
        system: str,
        response_schema: type[BaseModel],
    ) -> str:
        self.calls.append(
            {
                "messages": list(messages),
                "system": system,
                "response_schema": response_schema,
            }
        )
        return self.canned_reply


class FakeUserStore:
    """In-memory stand-in for UserStore — same methods, no network."""

    def __init__(self) -> None:
        self._passwords: dict[str, str] = dict(_DEMO_PASSWORDS)
        self._profiles: dict[str, UserProfile | None] = {
            u: None for u in _DEMO_PASSWORDS
        }
        self._plans: dict[str, MealPlan | None] = {u: None for u in _DEMO_PASSWORDS}

    def verify_credentials(self, username: str, password: str) -> bool:
        return self._passwords.get(username) == password

    def get_user(self, username: str) -> UserRecord | None:
        if username not in self._passwords:
            return None
        return UserRecord(
            username=username,
            profile=self._profiles[username],
            active_plan=self._plans[username],
        )

    def save_profile(self, username: str, profile: UserProfile) -> None:
        self._profiles[username] = profile

    def save_plan(self, username: str, plan: MealPlan) -> None:
        self._plans[username] = plan

    def save_profile_and_plan(
        self, username: str, profile: UserProfile, plan: MealPlan
    ) -> None:
        self._profiles[username] = profile
        self._plans[username] = plan


class FakeSessionStore:
    """In-memory stand-in for SessionStore — same methods, including save()."""

    def __init__(self) -> None:
        self._sessions: dict[str, Session] = {}

    def create(
        self,
        profile: UserProfile,
        current_plan: MealPlan | None = None,
    ) -> tuple[str, Session]:
        session_id = uuid.uuid4().hex
        session = Session(profile=profile, current_plan=current_plan)
        self._sessions[session_id] = session
        return session_id, session

    def get(self, session_id: str) -> Session | None:
        return self._sessions.get(session_id)

    def save(self, session_id: str, session: Session) -> None:
        self._sessions[session_id] = session


@pytest.fixture
def fake_llm() -> FakeLLM:
    return FakeLLM()


@pytest.fixture
def session_store() -> FakeSessionStore:
    """Fresh in-memory store per test — also injectable so tests can inspect sessions."""
    return FakeSessionStore()


@pytest.fixture
def user_store() -> FakeUserStore:
    """Fresh fake user store per test — never hits Supabase."""
    return FakeUserStore()


@pytest.fixture
def client(
    fake_llm: FakeLLM, session_store: FakeSessionStore, user_store: FakeUserStore
) -> Iterator[TestClient]:
    """TestClient with the LLM, SessionStore, and UserStore overridden.

    Each test gets its OWN stores so tests don't leak state.
    """
    app.dependency_overrides[get_llm] = lambda: fake_llm
    app.dependency_overrides[get_session_store] = lambda: session_store
    app.dependency_overrides[get_user_store] = lambda: user_store

    with TestClient(app) as test_client:
        yield test_client

    app.dependency_overrides.clear()
