"""POST /session/resume — DB read-only; new session seeded from stored plan."""

from __future__ import annotations

from pathlib import Path
from typing import Iterator

import pytest
from fastapi.testclient import TestClient

from agent.schemas import MealPlan, UserProfile
from agent.session import SessionStore
from agent.users import UserStore
from src.app.dependencies import get_llm, get_session_store, get_user_store
from src.app.main import app
from tests.conftest import CANNED_PLAN_JSON, FakeLLM


@pytest.fixture
def user_store(tmp_path: Path) -> UserStore:
    return UserStore(tmp_path / "users.db")


@pytest.fixture
def auth_client(
    fake_llm: FakeLLM, session_store: SessionStore, user_store: UserStore
) -> Iterator[TestClient]:
    app.dependency_overrides[get_llm] = lambda: fake_llm
    app.dependency_overrides[get_session_store] = lambda: session_store
    app.dependency_overrides[get_user_store] = lambda: user_store

    with TestClient(app) as test_client:
        yield test_client

    app.dependency_overrides.clear()


def _seed_user_with_plan(user_store: UserStore) -> MealPlan:
    profile = UserProfile(goal="lose_weight", calorie_target=1800)
    plan = MealPlan.model_validate_json(CANNED_PLAN_JSON)
    user_store.save_profile_and_plan("demo1", profile, plan)
    return plan


def test_resume_requires_auth(auth_client: TestClient) -> None:
    assert auth_client.post("/session/resume").status_code == 401


def test_resume_requires_stored_plan(
    auth_client: TestClient, user_store: UserStore
) -> None:
    auth_client.post("/login", json={"username": "demo1", "password": "password1"})
    response = auth_client.post("/session/resume")
    assert response.status_code == 400


def test_resume_twice_same_db_plan_different_session_ids(
    auth_client: TestClient, user_store: UserStore, session_store: SessionStore
) -> None:
    plan = _seed_user_with_plan(user_store)
    plan_before = plan.model_dump()
    auth_client.post("/login", json={"username": "demo1", "password": "password1"})

    first = auth_client.post("/session/resume")
    second = auth_client.post("/session/resume")
    assert first.status_code == 200, first.text
    assert second.status_code == 200, second.text

    body1 = first.json()
    body2 = second.json()
    assert body1["session_id"] != body2["session_id"]
    assert body1["plan"] == body2["plan"]

    # DB active_plan unchanged after both resumes.
    user = user_store.get_user("demo1")
    assert user is not None
    assert user.active_plan is not None
    assert user.active_plan.model_dump() == plan_before

    # Memory sessions seeded with plan + empty history.
    for sid in (body1["session_id"], body2["session_id"]):
        session = session_store.get(sid)
        assert session is not None
        assert session.current_plan is not None
        assert session.current_plan.model_dump() == plan_before
        assert session.history == []


def test_chat_works_after_resume(
    auth_client: TestClient, user_store: UserStore, fake_llm: FakeLLM
) -> None:
    _seed_user_with_plan(user_store)
    auth_client.post("/login", json={"username": "demo1", "password": "password1"})
    resume = auth_client.post("/session/resume")
    session_id = resume.json()["session_id"]

    chat = auth_client.post(
        "/chat",
        json={"session_id": session_id, "message": "more protein please"},
    )
    assert chat.status_code == 200, chat.text
    assert chat.json()["plan"]["notes"] == "Balanced day."

    # First chat after resume: system prompt already includes the stored plan.
    system = fake_llm.calls[0]["system"]
    assert "Current meal plan" in system
