"""Unit tests for UserStore write rules (in-memory FakeUserStore)."""

from __future__ import annotations

from agent.schemas import MealPlan, UserProfile
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
