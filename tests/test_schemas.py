"""Unit tests for the Pydantic data contract.

We test both good input (it accepts what we expect) and bad input (it
rejects what we expect). The bad-input tests matter most: they're what
protects us from a misbehaving LLM.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from agent.schemas import Food, Meal, MealPlan, PersonalFood, UserProfile


class TestUserProfile:
    def test_valid_profile_parses(self) -> None:
        profile = UserProfile(
            goal="lose_weight",
            allergies=["peanuts"],
            calorie_target=1800,
            cuisine_preferences=["Bahian", "Japanese"],
            flavor_profiles=["savory", "umami"],
            disliked_ingredients=["cilantro"],
            meals_per_day=5,
            protein_g_target=150,
        )

        assert profile.goal == "lose_weight"
        assert profile.allergies == ["peanuts"]
        assert profile.cuisine_preferences == ["Bahian", "Japanese"]
        assert profile.flavor_profiles == ["savory", "umami"]
        assert profile.disliked_ingredients == ["cilantro"]
        assert profile.meals_per_day == 5
        assert profile.protein_g_target == 150

    def test_missing_goal_is_rejected(self) -> None:
        with pytest.raises(ValidationError):
            UserProfile(calorie_target=2000)  # type: ignore[call-arg]

    def test_absurd_calorie_target_is_rejected(self) -> None:
        # Teaching note: we bound calorie_target so a typo like 20000
        # doesn't silently propagate into a 20,000 kcal meal plan.
        with pytest.raises(ValidationError):
            UserProfile(goal="maintain", calorie_target=20_000)

    def test_list_fields_default_to_empty_and_cuisine_defaults_to_brazilian(self) -> None:
        # Minimal profile: only the two truly-required fields. Everything
        # else should get a sensible default so old callers keep working.
        profile = UserProfile(goal="maintain", calorie_target=2000)

        assert profile.allergies == []
        assert profile.disliked_ingredients == []
        assert profile.flavor_profiles == []
        assert profile.cuisine_preferences == ["Brazilian"]
        assert profile.meals_per_day == 3
        assert profile.protein_g_target is None
        assert profile.carbs_g_target is None
        assert profile.fat_g_target is None

    def test_meals_per_day_must_be_positive(self) -> None:
        # Zero meals is nonsense — Pydantic should reject it.
        with pytest.raises(ValidationError):
            UserProfile(goal="maintain", calorie_target=2000, meals_per_day=0)

    def test_meals_per_day_upper_bound_rejects_typos(self) -> None:
        # 50 is almost certainly a typo; we cap at 8.
        with pytest.raises(ValidationError):
            UserProfile(goal="maintain", calorie_target=2000, meals_per_day=50)

    def test_flavor_profiles_rejects_unknown_values(self) -> None:
        # The `Literal` constraint should reject anything outside the allowed set.
        with pytest.raises(ValidationError):
            UserProfile(
                goal="maintain",
                calorie_target=2000,
                flavor_profiles=["funky"],  # type: ignore[list-item]
            )

    def test_allergies_and_dislikes_are_independent_lists(self) -> None:
        # One of the points of this phase: these two lists must NOT be merged.
        # A safety constraint (allergy) and a preference (dislike) are
        # different things and the prompt will treat them differently.
        profile = UserProfile(
            goal="maintain",
            calorie_target=2000,
            allergies=["shellfish"],
            disliked_ingredients=["cilantro"],
        )

        assert profile.allergies == ["shellfish"]
        assert profile.disliked_ingredients == ["cilantro"]
        assert "cilantro" not in profile.allergies
        assert "shellfish" not in profile.disliked_ingredients

    def test_macro_targets_are_optional_but_reject_negatives(self) -> None:
        # Unset is fine: most users will just target calories.
        UserProfile(goal="maintain", calorie_target=2000)
        # Set is fine.
        UserProfile(goal="maintain", calorie_target=2000, protein_g_target=180)
        # Negative is not fine — can't eat -20g of protein.
        with pytest.raises(ValidationError):
            UserProfile(goal="maintain", calorie_target=2000, protein_g_target=-20)
        with pytest.raises(ValidationError):
            UserProfile(goal="maintain", calorie_target=2000, carbs_g_target=-1)
        with pytest.raises(ValidationError):
            UserProfile(goal="maintain", calorie_target=2000, fat_g_target=-5)


class TestMealPlan:
    @staticmethod
    def _meal(name: str = "Test Meal", calories: int = 500) -> Meal:
        # Build a meal whose foods sum to `calories`. We give the first food
        # everything and pad with a zero-macro food so the sum is exact.
        return Meal(
            name=name,
            description="A test meal.",
            ingredients=[
                Food(name="rice", quantity="1 xícara", calories=calories, protein_g=20, carbs_g=60, fat_g=10),
                Food(name="beans", quantity="1 colher", calories=0, protein_g=0, carbs_g=0, fat_g=0),
            ],
        )

    def test_valid_meal_plan_parses(self) -> None:
        plan = MealPlan(
            meals=[
                self._meal("Breakfast", 500),
                self._meal("Lunch", 500),
                self._meal("Dinner", 500),
                self._meal("Afternoon snack", 150),
            ],
            notes="Balanced Brazilian day.",
        )

        assert plan.total_calories == 500 + 500 + 500 + 150
        assert len(plan.meals) == 4

    def test_meal_plan_accepts_variable_length_meals_list(self) -> None:
        # Whether the user asked for 3 meals or 6, the schema should cope.
        three = MealPlan(meals=[self._meal(f"M{i}") for i in range(3)])
        six = MealPlan(meals=[self._meal(f"M{i}") for i in range(6)])

        assert len(three.meals) == 3
        assert len(six.meals) == 6
        assert six.total_calories == 500 * 6

    def test_meal_macros_are_summed_from_foods(self) -> None:
        # The computed fields must reflect the sum of the food macros,
        # regardless of how many foods the meal has.
        meal = Meal(
            name="Rice & chicken",
            description="post-gym",
            ingredients=[
                Food(name="rice", quantity="150 g", calories=210, protein_g=4, carbs_g=45, fat_g=0),
                Food(name="chicken breast", quantity="100 g", calories=165, protein_g=31, carbs_g=0, fat_g=4),
            ],
        )

        assert meal.calories == 375
        assert meal.protein_g == 35
        assert meal.carbs_g == 45
        assert meal.fat_g == 4

    def test_negative_food_calories_are_rejected(self) -> None:
        with pytest.raises(ValidationError):
            Food(name="bad", quantity="1 unit", calories=-10, protein_g=0, carbs_g=0, fat_g=0)

    def test_food_quantity_is_required(self) -> None:
        # A food row without a portion size is useless to the user — the
        # schema should refuse it loudly, not silently accept it.
        with pytest.raises(ValidationError):
            Food(name="rice", calories=210, protein_g=4, carbs_g=45, fat_g=0)  # type: ignore[call-arg]

    def test_food_quantity_rejects_empty_string(self) -> None:
        # `min_length=1` guards against the LLM producing `"quantity": ""`
        # as a cheap way to satisfy the type check. Empty is not a portion.
        with pytest.raises(ValidationError):
            Food(name="rice", quantity="", calories=210, protein_g=4, carbs_g=45, fat_g=0)

    def test_empty_meals_list_is_rejected(self) -> None:
        # A day with zero meals is not a valid plan.
        with pytest.raises(ValidationError):
            MealPlan(meals=[])


class TestPersonalFood:
    def test_valid_pancake_parses_and_ingredients_default_to_empty(self) -> None:
        food = PersonalFood(
            id="pancake-1",
            name="Special Pancake",
            serving_size=1,
            serving_unit="pancake",
            calories=140,
            protein_g=10,
            carbs_g=12,
            fat_g=4,
        )

        assert food.name == "Special Pancake"
        assert food.serving_size == 1
        assert food.ingredients == []
        # A whole serving stores as 1, not 1.0.
        assert food.model_dump()["serving_size"] == 1

    def test_missing_ingredients_in_json_becomes_empty_list(self) -> None:
        food = PersonalFood.model_validate(
            {
                "id": "pancake-1",
                "name": "Special Pancake",
                "serving_size": 1,
                "serving_unit": "pancake",
                "calories": 140,
                "protein_g": 10,
                "carbs_g": 12,
                "fat_g": 4,
            }
        )

        assert food.ingredients == []

    def test_blank_name_zero_serving_and_negative_calories_are_rejected(self) -> None:
        base = {
            "id": "pancake-1",
            "name": "Special Pancake",
            "serving_size": 1,
            "serving_unit": "pancake",
            "calories": 140,
            "protein_g": 10,
            "carbs_g": 12,
            "fat_g": 4,
        }
        with pytest.raises(ValidationError):
            PersonalFood.model_validate({**base, "name": "   "})
        with pytest.raises(ValidationError):
            PersonalFood.model_validate({**base, "serving_unit": ""})
        with pytest.raises(ValidationError):
            PersonalFood.model_validate({**base, "serving_size": 0})
        with pytest.raises(ValidationError):
            PersonalFood.model_validate({**base, "calories": -1})

    def test_ingredients_are_kept_as_notes(self) -> None:
        food = PersonalFood(
            id="pancake-1",
            name="  Special Pancake  ",
            serving_size=1,
            serving_unit="pancake",
            calories=140,
            protein_g=10,
            carbs_g=12,
            fat_g=4,
            ingredients=["oat flour", "  ", "egg"],
        )

        assert food.name == "Special Pancake"
        assert food.ingredients == ["oat flour", "egg"]
