"""Persist profile/plan on /plan and /plan/save only after successful generation."""

from __future__ import annotations

from agent.schemas import MealPlan
from agent.users import UserStore
from fastapi.testclient import TestClient

from tests.conftest import CANNED_PLAN_JSON, FakeLLM


PLAN_BODY = {
    "goal": "lose_weight",
    "calorie_target": 1800,
    "allergies": ["peanuts"],
    "cuisine_preferences": ["brazilian"],
}


def test_guest_plan_does_not_write_db(
    client: TestClient, user_store: UserStore
) -> None:
    response = client.post("/plan", json=PLAN_BODY)
    assert response.status_code == 200, response.text

    user = user_store.get_user("demo1")
    assert user is not None
    assert user.profile is None
    assert user.active_plan is None


def test_logged_in_plan_writes_profile_and_plan(
    client: TestClient, user_store: UserStore
) -> None:
    client.post("/login", json={"username": "demo1", "password": "password1"})
    response = client.post("/plan", json=PLAN_BODY)
    assert response.status_code == 200, response.text

    user = user_store.get_user("demo1")
    assert user is not None
    assert user.profile is not None
    assert user.profile.calorie_target == 1800
    assert user.active_plan is not None
    assert user.active_plan.notes == "Balanced day."


def test_plan_failure_does_not_write(
    client: TestClient, user_store: UserStore, fake_llm: FakeLLM
) -> None:
    fake_llm.canned_reply = '{"not": "a meal plan"}'
    client.post("/login", json={"username": "demo1", "password": "password1"})

    response = client.post("/plan", json=PLAN_BODY)
    assert response.status_code == 502

    user = user_store.get_user("demo1")
    assert user is not None
    assert user.profile is None
    assert user.active_plan is None


def test_chat_does_not_write_plan(
    client: TestClient, user_store: UserStore, fake_llm: FakeLLM
) -> None:
    client.post("/login", json={"username": "demo1", "password": "password1"})
    plan_resp = client.post("/plan", json=PLAN_BODY)
    session_id = plan_resp.json()["session_id"]

    updated = MealPlan.model_validate_json(CANNED_PLAN_JSON)
    updated = updated.model_copy(update={"notes": "More protein."})
    fake_llm.canned_reply = updated.model_dump_json()

    chat = client.post(
        "/chat",
        json={"session_id": session_id, "message": "add more protein"},
    )
    assert chat.status_code == 200, chat.text
    assert chat.json()["plan"]["notes"] == "More protein."

    user = user_store.get_user("demo1")
    assert user is not None
    assert user.profile is not None
    assert user.profile.calorie_target == 1800
    assert user.active_plan is not None
    # Working plan changed in the response; DB still has the pre-chat plan.
    assert user.active_plan.notes == "Balanced day."


def test_save_plan_writes_after_chat(
    client: TestClient, user_store: UserStore, fake_llm: FakeLLM
) -> None:
    client.post("/login", json={"username": "demo1", "password": "password1"})
    plan_resp = client.post("/plan", json=PLAN_BODY)
    session_id = plan_resp.json()["session_id"]

    updated = MealPlan.model_validate_json(CANNED_PLAN_JSON)
    updated = updated.model_copy(update={"notes": "More protein."})
    fake_llm.canned_reply = updated.model_dump_json()

    chat = client.post(
        "/chat",
        json={"session_id": session_id, "message": "add more protein"},
    )
    assert chat.status_code == 200, chat.text

    before = user_store.get_user("demo1")
    assert before is not None
    assert before.active_plan is not None
    assert before.active_plan.notes == "Balanced day."

    saved = client.post("/plan/save", json={"session_id": session_id})
    assert saved.status_code == 200, saved.text
    assert saved.json()["ok"] is True

    user = user_store.get_user("demo1")
    assert user is not None
    assert user.profile is not None
    assert user.profile.calorie_target == 1800  # profile unchanged
    assert user.active_plan is not None
    assert user.active_plan.notes == "More protein."


def test_save_plan_requires_auth(client: TestClient) -> None:
    plan_resp = client.post("/plan", json=PLAN_BODY)
    session_id = plan_resp.json()["session_id"]
    response = client.post("/plan/save", json={"session_id": session_id})
    assert response.status_code == 401


def test_save_plan_unknown_session(client: TestClient) -> None:
    client.post("/login", json={"username": "demo1", "password": "password1"})
    response = client.post("/plan/save", json={"session_id": "does-not-exist"})
    assert response.status_code == 404


def test_discard_plan_restores_saved_after_chat(
    client: TestClient, user_store: UserStore, fake_llm: FakeLLM
) -> None:
    client.post("/login", json={"username": "demo1", "password": "password1"})
    plan_resp = client.post("/plan", json=PLAN_BODY)
    session_id = plan_resp.json()["session_id"]

    updated = MealPlan.model_validate_json(CANNED_PLAN_JSON)
    updated = updated.model_copy(update={"notes": "More protein."})
    fake_llm.canned_reply = updated.model_dump_json()

    chat = client.post(
        "/chat",
        json={"session_id": session_id, "message": "add more protein"},
    )
    assert chat.status_code == 200, chat.text
    assert chat.json()["plan"]["notes"] == "More protein."

    discarded = client.post("/plan/discard", json={"session_id": session_id})
    assert discarded.status_code == 200, discarded.text
    assert discarded.json()["plan"]["notes"] == "Balanced day."

    # DB active_plan unchanged; only the working session was restored.
    user = user_store.get_user("demo1")
    assert user is not None
    assert user.active_plan is not None
    assert user.active_plan.notes == "Balanced day."

    # Further chat uses the restored plan as the baseline.
    again = MealPlan.model_validate_json(CANNED_PLAN_JSON)
    again = again.model_copy(update={"notes": "After discard."})
    fake_llm.canned_reply = again.model_dump_json()
    chat2 = client.post(
        "/chat",
        json={"session_id": session_id, "message": "tweak again"},
    )
    assert chat2.status_code == 200, chat2.text
    assert chat2.json()["plan"]["notes"] == "After discard."
    still = user_store.get_user("demo1")
    assert still is not None
    assert still.active_plan is not None
    assert still.active_plan.notes == "Balanced day."


def test_discard_plan_requires_auth(client: TestClient) -> None:
    plan_resp = client.post("/plan", json=PLAN_BODY)
    session_id = plan_resp.json()["session_id"]
    response = client.post("/plan/discard", json={"session_id": session_id})
    assert response.status_code == 401


def test_discard_plan_unknown_session(client: TestClient) -> None:
    client.post("/login", json={"username": "demo1", "password": "password1"})
    client.post("/plan", json=PLAN_BODY)
    response = client.post("/plan/discard", json={"session_id": "does-not-exist"})
    assert response.status_code == 404


def test_chat_failure_does_not_overwrite_plan(
    client: TestClient, user_store: UserStore, fake_llm: FakeLLM
) -> None:
    client.post("/login", json={"username": "demo1", "password": "password1"})
    plan_resp = client.post("/plan", json=PLAN_BODY)
    session_id = plan_resp.json()["session_id"]
    before = user_store.get_user("demo1")
    assert before is not None
    assert before.active_plan is not None
    plan_before = before.active_plan.model_dump()

    fake_llm.canned_reply = "not-json"
    chat = client.post(
        "/chat",
        json={"session_id": session_id, "message": "break please"},
    )
    assert chat.status_code == 502

    after = user_store.get_user("demo1")
    assert after is not None
    assert after.active_plan is not None
    assert after.active_plan.model_dump() == plan_before
    assert after.profile == before.profile
