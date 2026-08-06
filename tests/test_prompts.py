"""Unit tests for prompt building.

Because create/edit builders are pure functions, we can assert on the
exact phrases they emit — no LLM, no network, no fixtures.
"""

from __future__ import annotations

from agent.prompts import (
    build_assistant_note,
    build_create_system_prompt,
    build_edit_system_prompt,
    build_initial_user_message,
)
from agent.schemas import Food, Meal, MealPlan, UserProfile


# Tiny MealPlan fixture for edit-prompt / assistant-note tests.
# `*` before `notes` means callers must pass notes by name
# (`notes="..."`), so we don't accidentally mix it up with positional args.
def _sample_plan(*, notes: str = "Swapped lunch for a lighter option.") -> MealPlan:
    return MealPlan(
        meals=[
            Meal(
                name="Almoço",
                description="Grilled chicken with salad",
                ingredients=[
                    Food(
                        name="grilled chicken",
                        quantity="150g",
                        calories=250,
                        protein_g=40,
                        carbs_g=0,
                        fat_g=8,
                    ),
                ],
            ),
        ],
        notes=notes,
    )


# --- Shared profile constraints (asserted via create prompt) -----------------


def test_system_prompt_includes_allergies_loudly() -> None:
    profile = UserProfile(
        goal="lose_weight",
        allergies=["peanuts", "shellfish"],
        calorie_target=1800,
        cuisine_preferences=["Mineira"],
    )

    prompt = build_create_system_prompt(profile)

    assert "peanuts" in prompt
    assert "shellfish" in prompt
    assert "CRITICAL" in prompt


def test_system_prompt_uses_none_known_when_allergies_empty() -> None:
    profile = UserProfile(goal="maintain", calorie_target=2000)

    prompt = build_create_system_prompt(profile)

    # Template always includes the CRITICAL line; empty list → "none known".
    assert "allergic to: none known" in prompt
    assert "CRITICAL" in prompt


def test_system_prompt_mentions_calorie_target_and_cuisines_plural() -> None:
    profile = UserProfile(
        goal="gain_muscle",
        calorie_target=2800,
        cuisine_preferences=["Bahian", "Japanese"],
    )

    prompt = build_create_system_prompt(profile)

    assert "2800" in prompt
    assert "Bahian" in prompt
    assert "Japanese" in prompt
    # The phrasing is plural even when there could be one cuisine; this
    # locks in the "cuisines plural" wording.
    assert "Cuisine preferences" in prompt
    assert "gain muscle" in prompt


def test_system_prompt_keeps_allergies_and_dislikes_distinct() -> None:
    # The whole point of Phase 1.5: safety and preference must NOT be merged.
    profile = UserProfile(
        goal="maintain",
        calorie_target=2000,
        allergies=["shellfish"],
        disliked_ingredients=["cilantro"],
    )

    prompt = build_create_system_prompt(profile)

    assert "CRITICAL" in prompt
    assert "shellfish" in prompt
    assert "AVOID WHEN POSSIBLE" in prompt
    assert "cilantro" in prompt
    # And crucially — they should NOT appear on the same rule line. The
    # rough check: the CRITICAL block should NOT mention cilantro, and
    # the AVOID block should NOT mention shellfish.
    critical_start = prompt.index("CRITICAL")
    avoid_start = prompt.index("AVOID WHEN POSSIBLE")
    critical_block = prompt[critical_start:avoid_start]
    avoid_block = prompt[avoid_start:]
    assert "cilantro" not in critical_block
    assert "shellfish" not in avoid_block


def test_system_prompt_uses_none_when_dislikes_empty() -> None:
    profile = UserProfile(
        goal="maintain",
        calorie_target=2000,
        allergies=["peanuts"],
    )

    prompt = build_create_system_prompt(profile)

    # Template always includes the AVOID line; empty list → "none".
    assert "dislikes: none" in prompt
    assert "AVOID WHEN POSSIBLE" in prompt


def test_system_prompt_includes_macro_targets_when_set() -> None:
    profile = UserProfile(
        goal="gain_muscle",
        calorie_target=2800,
        protein_g_target=180,
        carbs_g_target=300,
        fat_g_target=80,
    )

    prompt = build_create_system_prompt(profile)

    assert "180" in prompt
    assert "300" in prompt
    assert "80" in prompt
    assert "protein" in prompt.lower()
    assert "carbs" in prompt.lower()
    assert "fat" in prompt.lower()


def test_system_prompt_omits_macro_targets_when_unset() -> None:
    profile = UserProfile(goal="maintain", calorie_target=2000)

    prompt = build_create_system_prompt(profile)

    # None of the macro-target lines should appear when the user set none.
    assert "Target protein" not in prompt
    assert "Target carbs" not in prompt
    assert "Target fat" not in prompt


def test_system_prompt_includes_only_set_macro_targets() -> None:
    # Partial set: only protein. Prompt should mention it but NOT carbs/fat.
    profile = UserProfile(
        goal="gain_muscle",
        calorie_target=2500,
        protein_g_target=150,
    )

    prompt = build_create_system_prompt(profile)

    assert "150" in prompt
    assert "Target protein" in prompt
    assert "Target carbs" not in prompt
    assert "Target fat" not in prompt


def test_system_prompt_states_meal_count() -> None:
    profile = UserProfile(goal="maintain", calorie_target=2000, meals_per_day=5)

    prompt = build_create_system_prompt(profile)

    # Create mode must hard-require the profile meal count.
    assert "exactly 5 meals" in prompt


def test_system_prompt_includes_flavor_profiles_when_set() -> None:
    profile = UserProfile(
        goal="maintain",
        calorie_target=2000,
        flavor_profiles=["savory", "umami"],
    )

    prompt = build_create_system_prompt(profile)

    assert "savory" in prompt
    assert "umami" in prompt


# --- Create-only -------------------------------------------------------------


def test_create_prompt_states_create_job_and_omits_edit_plan() -> None:
    profile = UserProfile(goal="maintain", calorie_target=2000)

    prompt = build_create_system_prompt(profile)

    assert "CREATE a realistic daily meal plan from scratch" in prompt
    assert "Current meal plan" not in prompt
    assert "Editing principles" not in prompt
    assert "When the user asks for a change" not in prompt


# --- Edit-only ---------------------------------------------------------------


def test_edit_prompt_includes_edit_job_and_current_plan() -> None:
    profile = UserProfile(goal="maintain", calorie_target=2000)
    plan = _sample_plan()

    prompt = build_edit_system_prompt(profile, plan)

    assert "EDIT the current meal plan" in prompt
    assert "Editing principles" in prompt
    assert "Current meal plan:" in prompt
    assert "Almoço" in prompt
    assert "grilled chicken" in prompt
    # The whole serialized plan should appear — that's the source of truth.
    assert plan.model_dump_json() in prompt
    # Create-from-scratch must not be the primary job in edit mode.
    assert "CREATE a realistic daily meal plan from scratch" not in prompt


def test_edit_prompt_includes_shared_profile_constraints() -> None:
    profile = UserProfile(
        goal="lose_weight",
        calorie_target=1800,
        allergies=["peanuts"],
        meals_per_day=4,
    )
    plan = _sample_plan()

    prompt = build_edit_system_prompt(profile, plan)

    assert "Brazilian nutritionist" in prompt
    assert "1800" in prompt
    assert "CRITICAL" in prompt
    assert "peanuts" in prompt
    # Usual count is guidance; create's hard lock must not appear in edit.
    assert "usual meal count is 4" in prompt
    assert "Produce a full day of exactly" not in prompt


# --- User / assistant message helpers ----------------------------------------


# First user turn is just the task. Calories / cuisine / meal count already
# live in the system prompt, so repeating them here would waste tokens.
def test_initial_user_message_is_short_task_without_profile_fields() -> None:
    message = build_initial_user_message()

    assert message == "Generate my meal plan based on my goals and preferences."
    assert "2100" not in message
    assert "Paulista" not in message
    assert "Japanese" not in message


# History stores a short note (usually plan.notes), not the full MealPlan JSON.
def test_assistant_note_uses_plan_notes() -> None:
    plan = _sample_plan(notes="Made lunch lighter.")

    assert build_assistant_note(plan) == "Made lunch lighter."


# If the model left notes blank (or whitespace-only), we still need something
# short to append to history — never an empty string or a full plan dump.
def test_assistant_note_falls_back_when_notes_empty() -> None:
    plan = _sample_plan(notes="   ")

    assert build_assistant_note(plan) == "Updated the meal plan."
