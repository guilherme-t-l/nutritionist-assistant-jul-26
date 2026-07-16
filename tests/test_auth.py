"""Tests for login / logout /auth/me — login must not mutate profile/plan."""

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
from src.app.routes.auth import COOKIE_NAME
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


def test_login_sets_cookie_and_reports_no_plan(auth_client: TestClient) -> None:
    response = auth_client.post(
        "/login",
        json={"username": "demo1", "password": "password1"},
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body == {"username": "demo1", "has_plan": False}
    assert response.cookies.get(COOKIE_NAME) == "demo1"


def test_login_bad_password_returns_401(auth_client: TestClient) -> None:
    response = auth_client.post(
        "/login",
        json={"username": "demo1", "password": "wrong"},
    )

    assert response.status_code == 401
    assert response.json()["detail"] == "Invalid username or password"
    assert COOKIE_NAME not in response.cookies


def test_login_does_not_mutate_db(
    auth_client: TestClient, user_store: UserStore
) -> None:
    profile = UserProfile(goal="maintain", calorie_target=2000)
    plan = MealPlan.model_validate_json(CANNED_PLAN_JSON)
    user_store.save_profile_and_plan("demo1", profile, plan)
    before = user_store.get_user("demo1")
    assert before is not None
    before_profile = before.profile.model_dump() if before.profile else None
    before_plan = before.active_plan.model_dump() if before.active_plan else None

    response = auth_client.post(
        "/login",
        json={"username": "demo1", "password": "password1"},
    )
    assert response.status_code == 200
    assert response.json()["has_plan"] is True

    after = user_store.get_user("demo1")
    assert after is not None
    assert (after.profile.model_dump() if after.profile else None) == before_profile
    assert (after.active_plan.model_dump() if after.active_plan else None) == before_plan


def test_auth_me_requires_cookie(auth_client: TestClient) -> None:
    response = auth_client.get("/auth/me")
    assert response.status_code == 401


def test_auth_me_returns_user_with_plan(
    auth_client: TestClient, user_store: UserStore
) -> None:
    profile = UserProfile(goal="lose_weight", calorie_target=1800)
    plan = MealPlan.model_validate_json(CANNED_PLAN_JSON)
    user_store.save_profile_and_plan("demo1", profile, plan)

    auth_client.post("/login", json={"username": "demo1", "password": "password1"})
    response = auth_client.get("/auth/me")

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["username"] == "demo1"
    assert body["has_plan"] is True
    assert body["profile"]["calorie_target"] == 1800
    assert body["plan"]["notes"] == "Balanced day."


def test_logout_clears_cookie(auth_client: TestClient) -> None:
    auth_client.post("/login", json={"username": "demo1", "password": "password1"})
    assert auth_client.cookies.get(COOKIE_NAME) == "demo1"

    response = auth_client.post("/logout")
    assert response.status_code == 200
    assert response.json() == {"ok": True}

    # TestClient drops cleared cookies from its jar.
    assert auth_client.cookies.get(COOKIE_NAME) is None
    assert auth_client.get("/auth/me").status_code == 401
