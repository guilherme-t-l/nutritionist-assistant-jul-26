# Pydantic data contracts used everywhere in the agent.
#
# These models are the single source of truth for the shapes of data flowing
# between the HTML form, the backend, the LLM, and the tests. If the LLM
# returns JSON that doesn't match these shapes, Pydantic raises a validation
# error and we reject the reply — better to fail loud than serve garbage.
#
# Teaching note: Pydantic `BaseModel` turns a Python class into a runtime-
# validated schema. `Field(..., ge=0)` means "required, must be >= 0".

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import AfterValidator, BaseModel, Field, computed_field, field_serializer


# `Literal[...]` restricts a value to one of these three exact strings.
# Pydantic rejects anything else at validation time with a clear error.
Goal = Literal["lose_weight", "maintain", "gain_muscle"]

# Same pattern for flavor profiles: a small, closed set of allowed values.
# The user can pick several of these (see `UserProfile.flavor_profiles`).
FlavorProfile = Literal["savory", "sweet", "spicy", "umami", "sour", "bitter"]


# What the user tells us about themselves on the onboarding form.
# Built by FastAPI when POST /plan's JSON body is parsed, then stored on the
# Session so /chat's system-prompt builder can re-read the same constraints
# on every refinement.
#
# Subclassing `BaseModel` is how a class becomes a Pydantic schema:
# free JSON serialization, parsing, and runtime type-checking on every field.
class UserProfile(BaseModel):
    goal: Goal
    allergies: list[str] = Field(
        # `default_factory=list` runs `list()` per instance. Never write
        # `= []` as a default — all instances would share the same list.
        default_factory=list,
        description="Safety constraints: ingredients that are medically off-limits.",
    )
    disliked_ingredients: list[str] = Field(
        default_factory=list,
        description=(
            "Preferences: foods the user would rather not eat, but that "
            "wouldn't harm them. Kept separate from `allergies` so the prompt "
            "can treat the two differently."
        ),
    )
    calorie_target: int = Field(
        # `ge` = greater-or-equal, `le` = less-or-equal. Pydantic enforces
        # these bounds; no need to write `if x < 800: raise ...` by hand.
        ge=800,
        le=5000,
        description="Daily calorie target; bounded to catch typos.",
    )
    # `int | None` means "an int or None". `None` is the default, so the
    # field is optional. When set, it must be >= 0 (no negative macros).
    protein_g_target: int | None = Field(default=None, ge=0)
    carbs_g_target: int | None = Field(default=None, ge=0)
    fat_g_target: int | None = Field(default=None, ge=0)
    cuisine_preferences: list[str] = Field(
        # A list default lets the user pick several cuisines at once
        # (e.g. ["Bahian", "Japanese"]). Defaults to the safe catch-all.
        default_factory=lambda: ["Brazilian"],
        description="Free-form cuisine hints, e.g. ['Bahian', 'Japanese'].",
    )
    flavor_profiles: list[FlavorProfile] = Field(
        default_factory=list,
        description="Taste axes the user enjoys; constrained to the FlavorProfile literals.",
    )
    meals_per_day: int = Field(
        default=3,
        # `ge=1` rejects zero/negative meal counts. `le=8` is a sanity cap
        # so a typo like 50 doesn't ask the LLM for 50 meals.
        ge=1,
        le=8,
        description="How many meals the user eats per day — shapes plan length.",
    )


# A single food item inside a meal, with its own macros.
# Built by Pydantic when MealPlan.model_validate_json(raw_reply) parses the
# LLM's response. We store macros per food (not per meal) so the agent is
# forced to think about composition, and so the UI can later show where the
# calories on a plate actually come from.
class Food(BaseModel):
    name: str
    # Free-form portion string so the LLM can express Brazilian idioms
    # naturally: "1 xícara", "2 colheres de sopa", "150 g", "1 unidade média".
    # A structured (amount, unit) pair would force rigid picks and lose the
    # colloquial shape users actually recognize on a plate.
    # `min_length=1` rejects empty strings; `max_length=40` is a sanity cap
    # so the LLM can't cram a sentence in here.
    quantity: str = Field(
        min_length=1,
        max_length=40,
        description=(
            "Human-readable portion size for this ingredient, e.g. "
            "'1 xícara', '2 colheres de sopa', '150 g', '1 unidade média'. "
            "Use Brazilian household measures when natural; otherwise grams."
        ),
    )
    calories: int = Field(ge=0)
    protein_g: int = Field(ge=0)
    carbs_g: int = Field(ge=0)
    fat_g: int = Field(ge=0)


# A single meal within a MealPlan.
#
# Meal-level macros are NOT stored directly — they're computed from the
# `ingredients` list via the @computed_field properties below. That guarantees
# the totals always equal the sum of parts — no chance for the LLM to claim
# "500 kcal" while the listed foods add up to 700.
class Meal(BaseModel):
    name: str
    description: str
    ingredients: list[Food]

    # `@computed_field` turns a Python `@property` into a serialized OUTPUT
    # field (Pydantic v2). The LLM is NOT asked to produce these — they're
    # computed from `ingredients` on the way out, so the frontend still sees
    # `meal.calories`, `meal.protein_g`, etc. in the returned JSON.
    #
    # Two stacked decorators: `@property` makes `meal.calories` look like an
    # attribute (no parentheses); `@computed_field` tells Pydantic to include
    # it in the JSON output. `sum(... for ...)` is a generator expression —
    # like a list comprehension but without building the intermediate list.

    # Total meal calories — sum of the ingredients' calories. Computed on
    # serialization (when FastAPI turns the MealPlan into JSON).
    @computed_field  # type: ignore[prop-decorator]
    @property
    def calories(self) -> int:
        return sum(food.calories for food in self.ingredients)

    # Total meal protein in grams — sum of the ingredients'. Computed on
    # serialization.
    @computed_field  # type: ignore[prop-decorator]
    @property
    def protein_g(self) -> int:
        return sum(food.protein_g for food in self.ingredients)

    # Total meal carbs in grams — sum of the ingredients'. Computed on
    # serialization.
    @computed_field  # type: ignore[prop-decorator]
    @property
    def carbs_g(self) -> int:
        return sum(food.carbs_g for food in self.ingredients)

    # Total meal fat in grams — sum of the ingredients'. Computed on
    # serialization.
    @computed_field  # type: ignore[prop-decorator]
    @property
    def fat_g(self) -> int:
        return sum(food.fat_g for food in self.ingredients)


# A full day of meals — the shape returned by BOTH /plan and /chat.
#
# The `meals` list is ordered so the plan can flex from 3 meals to 5 or 6
# depending on the user's `meals_per_day`. The LLM names each meal itself
# ("Breakfast", "Mid-morning snack", "Lunch", ...) — names are NOT baked
# into the schema.
class MealPlan(BaseModel):
    # `min_length=1` is the only hard structural constraint. We don't cap
    # the upper bound here because `UserProfile.meals_per_day` already does.
    meals: list[Meal] = Field(min_length=1)
    notes: str = Field(
        default="",
        description="Short explanation from the agent, e.g. what it swapped and why.",
    )

    # Total plan calories for the full day — sum of each meal's computed
    # calories. Produced on serialization, so the frontend gets it in the
    # JSON without anyone assigning it.
    @computed_field  # type: ignore[prop-decorator]
    @property
    def total_calories(self) -> int:
        return sum(meal.calories for meal in self.meals)


# A label the user typed. `AfterValidator` runs after Pydantic accepts the
# string: trim spaces, then reject a blank. "   " is not a food name.
def _required_label(value: str) -> str:
    stripped = value.strip()
    if not stripped:
        raise ValueError("must not be blank")
    return stripped


# Ingredient lines are notes. Drop blanks; do not turn them into macros.
def _clean_ingredients(value: list[str]) -> list[str]:
    return [item.strip() for item in value if item.strip()]


# `Annotated` attaches rules to a type so several models can share them.
# `AfterValidator` is the function that runs on the value after basic parsing.
Label = Annotated[str, AfterValidator(_required_label)]
IngredientNotes = Annotated[list[str], AfterValidator(_clean_ingredients)]


def _whole_number_if_integral(value: float) -> int | float:
    """1.0 stores as 1. 1.5 stays 1.5. The library's serving size is a number."""
    if float(value).is_integer():
        return int(value)
    return value


# What the user types when adding or editing a food. No `id` yet — the server
# assigns that on create, and the URL supplies it on edit.
class PersonalFoodDraft(BaseModel):
    name: Label
    # `gt=0` means strictly greater than zero. 0 and negatives are rejected.
    serving_size: float = Field(gt=0)
    serving_unit: Label
    # Same idea as `Food`: macros cannot be negative. They describe ONE serving.
    calories: int = Field(ge=0)
    protein_g: int = Field(ge=0)
    carbs_g: int = Field(ge=0)
    fat_g: int = Field(ge=0)
    # Optional recipe lines. Missing in JSON becomes []. Not summed into macros.
    ingredients: IngredientNotes = Field(default_factory=list)

    # `@field_serializer` changes how this field is written to JSON.
    # A serving of 1 should look like `1`, not `1.0`.
    @field_serializer("serving_size")
    def _serialize_serving_size(self, value: float) -> int | float:
        return _whole_number_if_integral(value)


# One saved serving in the user's personal library.
# This is a new type. It does not change `Food` or `MealPlan`.
# `Food.quantity` is a display string ("1 pancake"). Here the size and the
# unit are separate so the agent can scale "2 pancakes" from one serving.
class PersonalFood(PersonalFoodDraft):
    id: str = Field(min_length=1)
