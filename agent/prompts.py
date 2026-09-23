# Prompt building. Pure functions, no I/O — string in, string out.
#
# Why this file exists:
#   1. Unit-test prompt wording without calling the LLM.
#   2. Change create or edit instructions in one place, then run evals.
#
# How a system prompt is assembled (the important mental model):
#
#   SHARED context          +   MODE-SPECIFIC job text   (+ plan JSON for edit)
#   (_build_shared_context)     (create OR edit)            (edit only)
#
#   • /plan  → build_create_system_prompt(profile)
#   • /chat  → build_edit_system_prompt(profile, plan, personal_foods?)
#   • import adapt → create prompt + personal foods (if any) + a suffix in plan_import.py
#
# Shared = who the agent is + this user's hard constraints (calories, allergies…).
# Create/edit = what the agent should DO with those facts (invent vs revise).

from __future__ import annotations

from agent.schemas import MealPlan, PersonalFood, UserProfile


# ---------------------------------------------------------------------------
# Constants — fixed strings reused by the builders below
# ---------------------------------------------------------------------------

# Maps the profile's goal enum → a short phrase after "The user goal is to …".
# Leading underscore = "module-private": other files shouldn't import this.
_GOAL_PHRASING = {
    "lose_weight": "lose weight gradually and sustainably",
    "maintain": "maintain their current weight",
    "gain_muscle": "gain muscle mass",
}

# First user turn for /plan. Profile details live in the system prompt;
# this message is only the task ("please generate…").
_INITIAL_USER_MESSAGE = "Generate my meal plan based on my goals and preferences."

# Prefix glued in front of pasted/PDF text for /plan/import (as_is mode):
# "structure what they gave you" — do not rewrite for preferences.
_IMPORT_USER_MESSAGE_PREFIX = (
    "Convert the following meal plan into the required MealPlan JSON schema. "
    "Keep the user's meals and ingredients as close as possible — do not "
    "rewrite them to match preferences:\n\n"
)

# Same idea for import adapt mode: start from their plan, fit preferences.
_IMPORT_ADAPT_USER_MESSAGE_PREFIX = (
    "Here is my existing meal plan. Edit it to match my preferences "
    "(targets, allergies, dislikes, cuisines, meals per day). Keep what "
    "already fits; change what doesn't:\n\n"
)

# Create job text for /plan. Invent a full day; no current-plan JSON attached.
def _create_job_instructions(meals_per_day: int) -> str:
    return f"""

Your job is to CREATE a realistic daily meal plan from scratch using Brazilian ingredients and cooking traditions, adapted to the user's preferences. Prioritize balance and variety across the day.

Produce a full day of exactly {meals_per_day} meals.
"""


# Edit job text for /chat. Placed BEFORE the plan JSON so the model reads
def _edit_job_instructions(meals_per_day: int) -> str:
    return f"""

Your job is to EDIT the current meal plan (below), not create a new one from scratch. The current plan is the baseline: assume the user likes it unless they explicitly say otherwise or ask for a completely new plan.

Editing principles:

- Make the smallest set of changes that satisfies the user's request while keeping the plan practical, balanced, and realistic. Keep changes local to the request: do not touch unrelated meals or add optimizations nobody asked for.
- Escalate only when necessary, in this order of preference:
  1. Adjust quantities of existing foods.
  2. Replace individual foods within a meal / Modify a single meal.
  3. Modify multiple meals.
  4. Rewrite the entire plan — only when smaller changes cannot reasonably satisfy the request or the nutritional requirements. If you do this, explicitly explain to the user why a larger rewrite was necessary.
- When changing foods, prefer variety across the day: avoid unnecessarily repeating the same ingredient or protein source across multiple meals, especially for non-common foods.
- If the user says they skipped a meal, treat it as not eaten: remove it and redistribute its calories and macros across the rest of the day as appropriate.
- The result should feel like a carefully edited version of the existing plan that preserves the user's food preferences and eating patterns — not a new plan with similar calories and macros.
- The user's usual meal count is {meals_per_day}. Treat this as the default, not a requirement. Prefer preserving the number of meals when it reasonably satisfies the user's request, but increase or decrease it whenever doing so results in a more practical, a more natural meal plan, or if the user skipped a meal...

Current meal plan:
"""


# ---------------------------------------------------------------------------
# Shared context — used by BOTH create and edit modes
# ---------------------------------------------------------------------------
# Persona + hard rules + this user's constraints.
# Helpers below only prepare VALUES (lists joined, empty defaults).
# The sentences here are the actual prompt the model sees.
def _build_shared_context(profile: UserProfile) -> str:
    goal = _GOAL_PHRASING.get(profile.goal, profile.goal)
    allergies = _allergies(profile.allergies)
    dislikes = _dislikes(profile.disliked_ingredients)
    cuisines = _cuisines(profile.cuisine_preferences)
    flavors = _flavors(profile.flavor_profiles)
    macros = _macro_targets(profile)

    return (
    "You are a warm, practical Brazilian nutritionist who creates realistic, enjoyable meal plans using foods the user is likely to eat.\n\n"

    "Primary objective:\n"
    f"- Help the user {goal}.\n"
    f"- Target approximately {profile.calorie_target} kcal/day, ideally within 5% and never more than 10%.\n"
    f"{macros}"

    "Preferences:\n"
    f"- Preferred cuisines: {cuisines}. Feel free to mix them naturally throughout the day.\n"
    f"- Preferred flavor profiles: {flavors}.\n"
    f"- Foods to avoid when reasonably possible: {dislikes}.\n\n"

    "Hard safety constraints:\n"
    f"- Allergies: {allergies}.\n"
    "- Never include these ingredients or foods that commonly contain them.\n\n"
    )


# ---------------------------------------------------------------------------
# Public builders — shared facts + the matching job constant
# ---------------------------------------------------------------------------

# /plan (+ evals): shared + create job.
def build_create_system_prompt(profile: UserProfile) -> str:
    return _build_shared_context(profile) + _create_job_instructions(profile.meals_per_day)


# /chat: shared constraints, then the library (only if non-empty), then the
# edit job and the current plan JSON. An empty library adds nothing — the
# prompt stays the same as a user who has never saved a personal food.
def build_edit_system_prompt(
    profile: UserProfile,
    plan: MealPlan,
    personal_foods: list[PersonalFood] | None = None,
) -> str:
    return (
        _build_shared_context(profile)
        + format_personal_foods_section(personal_foods or [])
        + _edit_job_instructions(profile.meals_per_day)
        + plan.model_dump_json()
    )


# Library block for edit and for import-adapt. "" when there is nothing to say,
# so we never send "Personal foods: none."
def format_personal_foods_section(foods: list[PersonalFood]) -> str:
    if not foods:
        return ""
    lines = [_format_personal_food_line(food) for food in foods]
    catalog = "\n".join(lines)
    return f"""
Personal foods this user already eats (context only — not the meal plan):

The current meal plan is the anchor. These are foods this user eats. They are not approved, not recommended, and not preferred over the meal plan.

Use a personal food only when one of these is true:
1. The user explicitly asks for it (for example, "I want my pancakes tomorrow"). Choose a quantity that fits the day's targets, and add other foods if that serving alone misses the meal.
2. The user asks for a substitution (for example, "What can I eat instead of breakfast?"). Personal foods are options alongside other suitable foods, not the whole menu.
3. The user says they already have it (for example, "I have my pancakes ready"). Fit that food into the meal and keep the nutritional targets.

Do not insert personal foods on your own.
Do not prefer a personal food only because it is in this list.
Do not limit suggestions to this list.
Allergies still win. A personal food that conflicts with an allergy is not used.
Macros below are for one serving. Multiply by the number of servings you choose, then round to whole kcal and grams.

{catalog}
"""


# ---------------------------------------------------------------------------
# User / assistant message helpers (not the system prompt)
# ---------------------------------------------------------------------------

# Synthetic first user turn for /plan so /plan and /chat both call llm.chat
# with the same input shape (system + messages). Content is just the task.
def build_initial_user_message() -> str:
    return _INITIAL_USER_MESSAGE


# /plan/import as_is: prefix + the user's pasted or PDF-extracted plan text.
# .strip() removes leading/trailing whitespace so we don't waste tokens.
def build_import_user_message(source_text: str) -> str:
    return _IMPORT_USER_MESSAGE_PREFIX + source_text.strip()


# /plan/import adapt: same pattern, but the prefix asks to fit preferences.
def build_import_adapt_user_message(source_text: str) -> str:
    return _IMPORT_ADAPT_USER_MESSAGE_PREFIX + source_text.strip()


# What we store in conversation history instead of the full MealPlan JSON.
# Prefer the model's own `notes` field; fall back if it left notes blank.
def build_assistant_note(plan: MealPlan) -> str:
    note = plan.notes.strip()
    if note:
        return note
    return "Updated the meal plan."


# ---------------------------------------------------------------------------
# Value helpers — each returns ONLY the variable inserted into the template
# above (joined lists / empty defaults). Not full sentences.
# ---------------------------------------------------------------------------

# ['peanuts', 'shellfish'] → 'peanuts, shellfish' ; [] → 'none known'
def _allergies(allergies: list[str]) -> str:
    if not allergies:
        return "none known"
    return ", ".join(allergies)


# ['cilantro'] → 'cilantro' ; [] → 'none'
def _dislikes(dislikes: list[str]) -> str:
    if not dislikes:
        return "none"
    return ", ".join(dislikes)


# ['Bahian', 'Japanese'] → 'Bahian and Japanese' ; [] → 'any Brazilian-leaning'
def _cuisines(cuisines: list[str]) -> str:
    if not cuisines:
        return "any Brazilian-leaning"
    return _join_natural(cuisines)


# ['savory', 'umami'] → 'savory, umami' ; [] → 'no strong preference'
def _flavors(flavors: list[str]) -> str:
    if not flavors:
        return "no strong preference"
    return ", ".join(flavors)


# Optional macro targets (g/day). Returns "" when none are set so the shared
# prompt does not gain a blank line for unset fields.
def _macro_targets(profile: UserProfile) -> str:
    # (label_for_prompt, value_from_profile) pairs so we can loop the same way.
    targets: list[tuple[str, int | None]] = [
        ("protein", profile.protein_g_target),
        ("carbs", profile.carbs_g_target),
        ("fat", profile.fat_g_target),
    ]
    # Keep only macros the user actually set (value is not None).
    set_targets = [(label, val) for label, val in targets if val is not None]
    if not set_targets:
        return ""
    lines = [f"Target {label}: {val}g per day." for label, val in set_targets]
    # Join with newlines and end with \n so the next prompt line sits cleanly.
    return "\n".join(lines) + "\n"


def _format_serving_size(value: float) -> str:
    if float(value).is_integer():
        return str(int(value))
    return f"{value:g}"


def _format_personal_food_line(food: PersonalFood) -> str:
    size = _format_serving_size(food.serving_size)
    line = (
        f"- {food.name} — {size} {food.serving_unit} — "
        f"{food.calories} kcal, {food.protein_g}g protein, "
        f"{food.carbs_g}g carbs, {food.fat_g}g fat per serving."
    )
    if food.ingredients:
        # Notes for the agent ("what's in it"). Not extra macros.
        line += " Ingredients: " + ", ".join(food.ingredients) + "."
    return line


# Natural-language list join:
#   ['Bahian', 'Japanese']            → 'Bahian and Japanese'
#   ['Bahian', 'Japanese', 'Mineira'] → 'Bahian, Japanese and Mineira'
def _join_natural(items: list[str]) -> str:
    if not items:
        return ""
    if len(items) == 1:
        return items[0]
    # items[:-1] = all but last; items[-1] = last item.
    return ", ".join(items[:-1]) + " and " + items[-1]
