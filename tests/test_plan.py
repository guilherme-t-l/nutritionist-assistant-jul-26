"""End-to-end tests for POST /plan with a fake LLM."""

from __future__ import annotations

import json

from fastapi.testclient import TestClient

from tests.conftest import FakeLLM, FakeSessionStore


def test_plan_returns_session_and_valid_meal_plan(
    client: TestClient, fake_llm: FakeLLM
) -> None:
    response = client.post(
        "/plan",
        json={
            "goal": "maintain",
            "calorie_target": 2000,
            "cuisine_preferences": ["Bahian"],
            "allergies": ["peanuts"],
        },
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert "session_id" in body
    # The canned plan has 3 meals, in order: tapioca, feijoada, grilled fish.
    assert body["plan"]["meals"][0]["name"] == "Tapioca com queijo"
    assert body["plan"]["total_calories"] == 1700


# After /plan succeeds: current_plan holds the MealPlan object, and history's
# model turn is a short note (from plan.notes) — not the full JSON reply.
def test_plan_stores_current_plan_and_short_history_note(
    client: TestClient, fake_llm: FakeLLM, session_store: FakeSessionStore
) -> None:
    response = client.post(
        "/plan",
        json={
            "goal": "maintain",
            "calorie_target": 2000,
            "cuisine_preferences": ["Bahian"],
        },
    )

    assert response.status_code == 200, response.text
    session_id = response.json()["session_id"]
    session = session_store.get(session_id)
    assert session is not None
    assert session.current_plan is not None
    assert session.current_plan.meals[0].name == "Tapioca com queijo"

    assert len(session.history) == 2
    assert session.history[0].role == "user"
    assert session.history[1].role == "model"
    # Canned plan notes = "Balanced day." — never the raw MealPlan JSON.
    assert session.history[1].content == "Balanced day."
    assert "meals" not in session.history[1].content

    # First LLM call: system has profile, but not a "Current meal plan" block.
    system = fake_llm.calls[0]["system"]
    assert "Current meal plan" not in system


def test_plan_forwards_full_profile_into_system_prompt(
    client: TestClient, fake_llm: FakeLLM
) -> None:
    client.post(
        "/plan",
        json={
            "goal": "lose_weight",
            "calorie_target": 1800,
            "cuisine_preferences": ["Bahian", "Japanese"],
            "flavor_profiles": ["savory", "umami"],
            "allergies": ["shellfish"],
            "disliked_ingredients": ["cilantro"],
            "protein_g_target": 140,
            "meals_per_day": 4,
        },
    )

    assert len(fake_llm.calls) == 1
    system = fake_llm.calls[0]["system"]
    # Everything the user gave us should show up somewhere in the prompt.
    assert "shellfish" in system
    assert "cilantro" in system
    assert "1800" in system
    assert "Bahian" in system
    assert "Japanese" in system
    assert "savory" in system
    assert "umami" in system
    assert "140" in system
    # And safety vs preference should NOT be merged.
    assert "CRITICAL" in system
    assert "AVOID WHEN POSSIBLE" in system


def test_plan_returns_requested_number_of_meals(
    client: TestClient, fake_llm: FakeLLM
) -> None:
    # The FakeLLM is deterministic: it returns whatever `canned_reply` holds.
    # For THIS test we want to prove that an N-meal plan flows end-to-end
    # through /plan without the schema or route clipping it to 3. So we set
    # the fake reply to a 5-meal JSON document first.
    five_meal_plan = {
        "meals": [
            {
                "name": f"Meal {i + 1}",
                "description": "stub",
                "ingredients": [
                    {"name": "rice", "quantity": "1 xícara", "calories": 400, "protein_g": 10, "carbs_g": 70, "fat_g": 5}
                ],
            }
            for i in range(5)
        ],
        "notes": "Five-meal day.",
    }
    fake_llm.canned_reply = json.dumps(five_meal_plan)

    profile = {
        "goal": "gain_muscle",
        "calorie_target": 2500,
        "cuisine_preferences": ["Bahian"],
        "meals_per_day": 5,
    }
    response = client.post("/plan", json=profile)

    assert response.status_code == 200, response.text
    body = response.json()
    assert len(body["plan"]["meals"]) == profile["meals_per_day"]
    assert body["plan"]["total_calories"] == 400 * 5
    # And the prompt sent to the LLM should have asked for 5 meals.
    system = fake_llm.calls[0]["system"]
    assert "5" in system


def test_plan_rejects_bad_profile(client: TestClient) -> None:
    # calorie_target out of bounds — Pydantic should reject BEFORE the LLM is called
    response = client.post(
        "/plan",
        json={
            "goal": "maintain",
            "calorie_target": 50_000,
            "cuisine_preferences": ["Brazilian"],
        },
    )

    assert response.status_code == 422


def test_plan_rejects_invalid_flavor_profile(client: TestClient) -> None:
    # "funky" is not in the FlavorProfile Literal — Pydantic must reject.
    response = client.post(
        "/plan",
        json={
            "goal": "maintain",
            "calorie_target": 2000,
            "flavor_profiles": ["funky"],
        },
    )

    assert response.status_code == 422
