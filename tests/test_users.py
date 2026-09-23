"""Unit tests for UserStore write rules (in-memory FakeUserStore)."""

from __future__ import annotations

from agent.schemas import MealPlan, PersonalFood, UserProfile
from agent.users import _parse_personal_foods
from tests.conftest import CANNED_PLAN_JSON, FakeUserStore


def _sample_profile(**overrides: object) -> UserProfile:
    data: dict = {
        "goal": "lose_weight",
        "calorie_target": 1800,
        "allergies": ["peanuts"],
        "cuisine_preferences": ["brazilian"],
    }
    data.update(overrides)
    return UserProfile(**data)


def _sample_plan() -> MealPlan:
    return MealPlan.model_validate_json(CANNED_PLAN_JSON)


def test_verify_credentials_seeds_demo_users() -> None:
    store = FakeUserStore()

    assert store.verify_credentials("demo1", "password1") is True
    assert store.verify_credentials("demo1", "wrong") is False
    assert store.verify_credentials("nobody", "password1") is False


def test_get_user_starts_with_null_profile_and_plan() -> None:
    store = FakeUserStore()
    user = store.get_user("demo1")

    assert user is not None
    assert user.username == "demo1"
    assert user.profile is None
    assert user.active_plan is None


def test_save_profile_does_not_clear_plan() -> None:
    store = FakeUserStore()
    profile = _sample_profile()
    plan = _sample_plan()
    store.save_profile_and_plan("demo1", profile, plan)

    updated = _sample_profile(calorie_target=2000)
    store.save_profile("demo1", updated)

    user = store.get_user("demo1")
    assert user is not None
    assert user.profile is not None
    assert user.profile.calorie_target == 2000
    assert user.active_plan is not None
    assert user.active_plan.model_dump() == plan.model_dump()


def test_save_plan_leaves_profile_alone() -> None:
    store = FakeUserStore()
    profile = _sample_profile()
    plan = _sample_plan()
    store.save_profile_and_plan("demo1", profile, plan)

    # Mutate notes so the round-trip is obviously a new plan write.
    new_plan = plan.model_copy(update={"notes": "Updated after chat."})
    store.save_plan("demo1", new_plan)

    user = store.get_user("demo1")
    assert user is not None
    assert user.profile == profile
    assert user.active_plan is not None
    assert user.active_plan.notes == "Updated after chat."


def test_save_profile_and_plan_round_trip() -> None:
    store = FakeUserStore()
    profile = _sample_profile()
    plan = _sample_plan()
    store.save_profile_and_plan("demo2", profile, plan)

    user = store.get_user("demo2")
    assert user is not None
    assert user.profile == profile
    assert user.active_plan == plan


def _pancake() -> PersonalFood:
    return PersonalFood(
        id="pancake-1",
        name="Special Pancake",
        serving_size=1,
        serving_unit="pancake",
        calories=140,
        protein_g=10,
        carbs_g=12,
        fat_g=4,
    )


def test_null_personal_foods_column_reads_as_empty_list() -> None:
    # The real column is null until the user saves something.
    assert _parse_personal_foods(None) == []
    assert _parse_personal_foods("") == []


def test_save_personal_foods_round_trip_leaves_profile_and_plan() -> None:
    store = FakeUserStore()
    profile = _sample_profile()
    plan = _sample_plan()
    store.save_profile_and_plan("demo1", profile, plan)

    store.save_personal_foods("demo1", [_pancake()])

    user = store.get_user("demo1")
    assert user is not None
    assert user.personal_foods == [_pancake()]
    assert user.profile == profile
    assert user.active_plan == plan

    # The other direction: rewriting the plan does not drop the library.
    store.save_plan("demo1", plan.model_copy(update={"notes": "Still the plan."}))
    again = store.get_user("demo1")
    assert again is not None
    assert again.personal_foods == [_pancake()]
    assert again.active_plan is not None
    assert again.active_plan.notes == "Still the plan."
