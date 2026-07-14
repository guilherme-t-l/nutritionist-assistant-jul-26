# Prompt building. Pure functions, no I/O.
#
# Keeping prompts in their own file means we can:
#   1. Unit-test them without hitting the LLM.
#   2. Tweak wording in one place and immediately run evals on the diff
#      (Phase 4 will depend on this).

from __future__ import annotations

from agent.schemas import MealPlan, UserProfile


# Leading underscore = "module-private" by convention. Other files shouldn't
# import `_GOAL_PHRASING` directly — it's an implementation detail of this file.
_GOAL_PHRASING = {
    "lose_weight": "is trying to lose weight gradually and sustainably",
    "maintain": "wants to maintain their current weight",
    "gain_muscle": "is trying to gain muscle mass",
}

_INITIAL_USER_MESSAGE = "Generate my meal plan based on my goals and preferences."


# Called from /plan AND /chat on every request. Turns a UserProfile (and, once
# it exists, the latest MealPlan) into the "system" instruction string we send
# to the LLM — the agent's personality, this user's hard constraints, and the
# current plan as the source of truth for what to edit.
#
# Note: allergies and dislikes are rendered as TWO separate prompt lines on
# purpose:
#   - Allergies  -> "CRITICAL ... never include" (safety-critical).
#   - Dislikes   -> "AVOID WHEN POSSIBLE"         (preference).
# Merging them would risk the LLM treating a dislike like an emergency, or an
# allergy like a mild hint.
def build_system_prompt(profile: UserProfile, plan: MealPlan | None = None) -> str:
    # `dict.get(key, default)` returns the value if the key exists, otherwise
    # the default — never raises KeyError. Safer than `_GOAL_PHRASING[key]`.
    goal_text = _GOAL_PHRASING.get(profile.goal, profile.goal)
    allergies_text = _format_allergies(profile.allergies)
    dislikes_text = _format_dislikes(profile.disliked_ingredients)
    cuisines_text = _format_cuisines(profile.cuisine_preferences)
    flavors_text = _format_flavors(profile.flavor_profiles)
    macro_text = _format_macro_targets(profile)

    # Adjacent string literals (no comma between them) get concatenated by
    # Python at parse time. `f"..."` strings interpolate `{expr}` inline.
    prompt = (
        "You are a warm, practical Brazilian nutritionist. "
        "You design realistic daily meal plans using Brazilian ingredients "
        "and cooking traditions, adapted to the user's preferences. You always "
        f"reply with a full day of exactly {profile.meals_per_day} meals that "
        "together hit the user's calorie target within ~10%.\n\n"
        f"The user {goal_text}. "
        f"Their daily calorie target is {profile.calorie_target} kcal.\n"
        f"{macro_text}"
        f"{cuisines_text}\n"
        f"{flavors_text}\n\n"
        f"{allergies_text}\n"
        f"{dislikes_text}"
    )

    # If the plan is not None, we add the current meal plan to the prompt (for the first call it usually won't have a plan yet).
    if plan is not None:
        prompt += (
            "\n\nCurrent meal plan:\n"
            + plan.model_dump_json()
        )

    return prompt


# Called ONLY from /plan (not /chat). Synthesizes the user's first "turn" so
# that /plan and /chat both end up calling llm.chat(...) with the same shape
# of input. Profile fields live in the system prompt — this message is just
# the task.
def build_initial_user_message() -> str:
    return _INITIAL_USER_MESSAGE


# Short note stored in history instead of the full MealPlan JSON. Prefer the
# plan's own `notes`; fall back when the model left that field empty.
def build_assistant_note(plan: MealPlan) -> str:
    note = plan.notes.strip()
    if note:
        return note
    return "Updated the meal plan."


# Helper for build_system_prompt above. Turns the user's allergies list into
# the SAFETY-CRITICAL line of the prompt, or a fallback line if empty.
def _format_allergies(allergies: list[str]) -> str:
    # `if not allergies` is the truthy-check idiom: empty list -> False ->
    # we enter this branch. Works for None, "", [], {}, 0 — all "falsy".
    if not allergies:
        return "The user has no known food allergies."
    # `", ".join(list)` = glue every element with ", " between them.
    # Counter-intuitive call site: you call it on the SEPARATOR, not the list.
    joined = ", ".join(allergies)
    return (
        f"CRITICAL (safety): the user is allergic to: {joined}. "
        "Never include these ingredients, or anything that typically "
        "contains them, in any meal. This is a hard safety requirement."
    )


# Helper for build_system_prompt. Renders disliked ingredients as a
# PREFERENCE line — deliberately softer wording than allergies, so the LLM
# doesn't treat them as safety-critical.
def _format_dislikes(dislikes: list[str]) -> str:
    if not dislikes:
        return "The user has no strong ingredient dislikes."
    joined = ", ".join(dislikes)
    return (
        f"AVOID WHEN POSSIBLE (preference): the user dislikes: {joined}. "
        "These are not dangerous — they are strong preferences. Do not build "
        "meals around them; substitute freely."
    )


# Helper for build_system_prompt. Renders cuisine preferences into one prompt
# line, using "X and Y" / "X, Y and Z" style joining via _join_or().
def _format_cuisines(cuisines: list[str]) -> str:
    if not cuisines:
        return "Cuisine: any Brazilian-leaning style is fine."
    joined = _join_or(cuisines)
    # Plural wording even for one item reads fine and keeps the prompt
    # consistent for the multi-cuisine cases we now support.
    return f"Cuisine preferences: {joined}. Feel free to blend them across the day."


# Helper for build_system_prompt. Renders preferred flavor profiles
# (savory, sweet, spicy, ...) into one prompt line.
def _format_flavors(flavors: list[str]) -> str:
    if not flavors:
        return "Flavor profile: no strong preference — cook balanced."
    joined = ", ".join(flavors)
    return f"Preferred flavor profiles: {joined}."


# Helper for build_system_prompt. Emits one prompt line per macro target that
# the user actually set (protein / carbs / fat in grams). Returns "" when
# none are set, so the surrounding prompt doesn't end up with a blank line.
def _format_macro_targets(profile: UserProfile) -> str:
    # Pair the Python attribute with the label we'd show in the prompt.
    # `list[tuple[str, int | None]]` of (label, value) so we can loop uniformly.
    targets: list[tuple[str, int | None]] = [
        ("protein", profile.protein_g_target),
        ("carbs", profile.carbs_g_target),
        ("fat", profile.fat_g_target),
    ]
    # `[... for ... if ...]` = list comprehension WITH a filter. Keeps only
    # the rows whose value is not None.
    set_targets = [(label, val) for label, val in targets if val is not None]
    if not set_targets:
        return ""
    lines = [f"Target {label}: {val}g per day." for label, val in set_targets]
    # `"\n".join(lines) + "\n"` -> each line on its own row, block ends in \n.
    return "\n".join(lines) + "\n"


# Small string helper used by _format_cuisines.
# Joins a list with commas, using " and " before the LAST element.
#   ['Bahian', 'Japanese']            -> 'Bahian and Japanese'
#   ['Bahian', 'Japanese', 'Mineira'] -> 'Bahian, Japanese and Mineira'
def _join_or(items: list[str]) -> str:
    if not items:
        return ""
    if len(items) == 1:
        return items[0]
    # `items[:-1]` = all but last; `items[-1]` = last. Classic Python slicing.
    return ", ".join(items[:-1]) + " and " + items[-1]
