"""PUT /profile — updates profile only; active_plan untouched."""

from __future__ import annotations

from typing import Iterator

import pytest
from fastapi.testclient import TestClient

from agent.schemas import MealPlan, UserProfile
from src.app.dependencies import get_llm, get_session_store, get_user_store
from src.app.main import app
from tests.conftest import CANNED_PLAN_JSON, FakeLLM, FakeSessionStore, FakeUserStore


@pytest.fixture
def auth_client(
    fake_llm: FakeLLM, session_store: FakeSessionStore, user_store: FakeUserStore
) -> Iterator[TestClient]:
    app.dependency_overrides[get_llm] = lambda: fake_llm
    app.dependency_overrides[get_session_store] = lambda: session_store
    app.dependency_overrides[get_user_store] = lambda: user_store

    with TestClient(app) as test_client:
        yield test_client

    app.dependency_overrides.clear()


def test_put_profile_requires_auth(auth_client: TestClient) -> None:
    response = auth_client.put(
        "/profile",
        json={"goal": "maintain", "calorie_target": 2000},
    )
    assert response.status_code == 401


def test_put_profile_updates_profile_not_plan(
    auth_client: TestClient, user_store: FakeUserStore, session_store: FakeSessionStore
) -> None:
    profile = UserProfile(goal="lose_weight", calorie_target=1800)
    plan = MealPlan.model_validate_json(CANNED_PLAN_JSON)
    user_store.save_profile_and_plan("demo1", profile, plan)
    plan_before = plan.model_dump()

    auth_client.post("/login", json={"username": "demo1", "password": "password1"})
    session_id, session = session_store.create(profile, current_plan=plan)

    response = auth_client.put(
        "/profile",
        json={
            "goal": "lose_weight",
            "calorie_target": 2000,
            "allergies": ["peanuts"],
            "session_id": session_id,
        },
    )
    assert response.status_code == 200, response.text
    assert response.json()["profile"]["calorie_target"] == 2000

    user = user_store.get_user("demo1")
    assert user is not None
    assert user.profile is not None
    assert user.profile.calorie_target == 2000
    assert user.profile.allergies == ["peanuts"]
    assert user.active_plan is not None
    assert user.active_plan.model_dump() == plan_before

    # Session: new profile, same plan, history untouched.
    assert session.profile.calorie_target == 2000
    assert session.current_plan is not None
    assert session.current_plan.model_dump() == plan_before
    assert session.history == []
