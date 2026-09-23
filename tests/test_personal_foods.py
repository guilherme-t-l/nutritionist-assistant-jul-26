"""Personal food library: CRUD, guest rejection, and prompt wiring.

The library is context for edit and import-adapt. Creating a plan, saving a
plan, and discarding a plan must not rewrite it.
"""

from __future__ import annotations

from fastapi.testclient import TestClient

from agent.schemas import MealPlan
from tests.conftest import CANNED_PLAN_JSON, FakeLLM, FakeUserStore


PANCAKE = {
    "name": "Special Pancake",
    "serving_size": 1,
    "serving_unit": "pancake",
    "calories": 140,
    "protein_g": 10,
    "carbs_g": 12,
    "fat_g": 4,
    "ingredients": ["oat flour", "egg"],
}

PROFILE = {
    "goal": "maintain",
    "calorie_target": 2000,
    "cuisine_preferences": ["Brazilian"],
    "allergies": ["peanuts"],
}


def _login(client: TestClient, username: str = "demo1") -> None:
    response = client.post(
        "/login",
        json={"username": username, "password": f"password{username[-1]}"},
    )
    assert response.status_code == 200, response.text


def test_guest_personal_food_endpoints_are_rejected(client: TestClient) -> None:
    assert client.get("/personal-foods").status_code == 401
    assert client.post("/personal-foods", json=PANCAKE).status_code == 401
    assert client.put("/personal-foods/missing", json=PANCAKE).status_code == 401
    assert client.delete("/personal-foods/missing").status_code == 401


def test_create_list_edit_and_delete(client: TestClient) -> None:
    _login(client)

    created = client.post("/personal-foods", json=PANCAKE)
    assert created.status_code == 201, created.text
    food_id = created.json()["id"]
    assert created.json()["name"] == "Special Pancake"
    assert created.json()["calories"] == 140

    listed = client.get("/personal-foods")
    assert listed.status_code == 200
    assert len(listed.json()) == 1
    assert listed.json()[0]["id"] == food_id
    assert listed.json()[0]["ingredients"] == ["oat flour", "egg"]

    edited = client.put(
        f"/personal-foods/{food_id}",
        json={**PANCAKE, "fat_g": 5, "ingredients": []},
    )
    assert edited.status_code == 200, edited.text
    assert edited.json()["id"] == food_id
    assert edited.json()["fat_g"] == 5
    assert edited.json()["ingredients"] == []

    other = client.post(
        "/personal-foods",
        json={**PANCAKE, "name": "Sunday Brownie", "serving_unit": "square"},
    )
    assert other.status_code == 201, other.text
    other_id = other.json()["id"]

    removed = client.delete(f"/personal-foods/{food_id}")
    assert removed.status_code == 204

    remaining = client.get("/personal-foods").json()
    assert [item["id"] for item in remaining] == [other_id]
    assert remaining[0]["name"] == "Sunday Brownie"


def test_invalid_food_and_unknown_id_leave_the_list_unchanged(
    client: TestClient,
) -> None:
    _login(client)
    created = client.post("/personal-foods", json=PANCAKE)
    assert created.status_code == 201, created.text
    food_id = created.json()["id"]
    before = client.get("/personal-foods").json()

    blank = client.post("/personal-foods", json={**PANCAKE, "name": "   "})
    assert blank.status_code == 422

    zero = client.post("/personal-foods", json={**PANCAKE, "serving_size": 0})
    assert zero.status_code == 422

    negative = client.post("/personal-foods", json={**PANCAKE, "calories": -5})
    assert negative.status_code == 422

    missing_edit = client.put("/personal-foods/does-not-exist", json=PANCAKE)
    assert missing_edit.status_code == 404
    missing_delete = client.delete("/personal-foods/does-not-exist")
    assert missing_delete.status_code == 404

    assert client.get("/personal-foods").json() == before
    assert before[0]["id"] == food_id


def test_chat_sees_a_food_saved_before_the_message(
    client: TestClient, fake_llm: FakeLLM, user_store: FakeUserStore
) -> None:
    _login(client)
    created = client.post("/personal-foods", json=PANCAKE)
    assert created.status_code == 201, created.text

    plan_response = client.post("/plan", json=PROFILE)
    assert plan_response.status_code == 200, plan_response.text
    # /plan is create-from-scratch: the library must not be in that prompt.
    assert "Personal foods" not in fake_llm.calls[0]["system"]
    assert "Special Pancake" not in fake_llm.calls[0]["system"]

    session_id = plan_response.json()["session_id"]
    chat_response = client.post(
        "/chat",
        json={"session_id": session_id, "message": "I want my pancakes tomorrow."},
    )
    assert chat_response.status_code == 200, chat_response.text

    system = fake_llm.calls[1]["system"]
    assert "Special Pancake" in system
    assert "1 pancake" in system
    assert "Do not insert personal foods on your own." in system
    assert "Current meal plan:" in system
    # Chat does not write the library or the saved plan.
    user = user_store.get_user("demo1")
    assert user is not None
    assert len(user.personal_foods) == 1
    assert user.active_plan is not None
    assert user.active_plan.notes == "Balanced day."


def test_saving_and_discarding_a_plan_does_not_change_the_library(
    client: TestClient, fake_llm: FakeLLM, user_store: FakeUserStore
) -> None:
    _login(client)
    client.post("/personal-foods", json=PANCAKE)
    plan_response = client.post("/plan", json=PROFILE)
    session_id = plan_response.json()["session_id"]
    library_before = user_store.get_personal_foods("demo1")

    updated = MealPlan.model_validate_json(CANNED_PLAN_JSON)
    updated = updated.model_copy(update={"notes": "Lighter lunch."})
    fake_llm.canned_reply = updated.model_dump_json()
    chat = client.post(
        "/chat",
        json={"session_id": session_id, "message": "make lunch lighter"},
    )
    assert chat.status_code == 200, chat.text

    saved = client.post("/plan/save", json={"session_id": session_id})
    assert saved.status_code == 200, saved.text
    assert user_store.get_personal_foods("demo1") == library_before

    discarded = client.post("/plan/discard", json={"session_id": session_id})
    assert discarded.status_code == 200, discarded.text
    assert user_store.get_personal_foods("demo1") == library_before


def test_editing_the_library_does_not_change_the_active_plan(
    client: TestClient, user_store: FakeUserStore
) -> None:
    _login(client)
    plan_response = client.post("/plan", json=PROFILE)
    assert plan_response.status_code == 200, plan_response.text
    plan_before = user_store.get_user("demo1")
    assert plan_before is not None
    assert plan_before.active_plan is not None
    saved_plan = plan_before.active_plan.model_dump()

    created = client.post("/personal-foods", json=PANCAKE)
    food_id = created.json()["id"]
    edited = client.put(
        f"/personal-foods/{food_id}",
        json={**PANCAKE, "fat_g": 5},
    )
    assert edited.status_code == 200, edited.text

    user = user_store.get_user("demo1")
    assert user is not None
    assert user.active_plan is not None
    assert user.active_plan.model_dump() == saved_plan
    assert user.personal_foods[0].fat_g == 5


def test_import_adapt_includes_library_and_as_is_does_not(
    client: TestClient, fake_llm: FakeLLM
) -> None:
    _login(client)
    client.post("/personal-foods", json=PANCAKE)

    as_is = client.post(
        "/plan/import",
        json={
            "profile": PROFILE,
            "source_text": "Breakfast: eggs. Lunch: rice and beans. Dinner: fish.",
            "mode": "as_is",
        },
    )
    assert as_is.status_code == 200, as_is.text
    assert "Personal foods" not in fake_llm.calls[0]["system"]
    assert "Special Pancake" not in fake_llm.calls[0]["system"]

    adapted = client.post(
        "/plan/import",
        json={
            "profile": PROFILE,
            "source_text": CANNED_PLAN_JSON,
            "mode": "adapt",
        },
    )
    assert adapted.status_code == 200, adapted.text
    system = fake_llm.calls[1]["system"]
    assert "Special Pancake" in system
    assert "not approved, not recommended, and not preferred" in system
    assert "peanuts" in system
