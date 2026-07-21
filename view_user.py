#!/usr/bin/env python3
"""Pretty-print a user's preferences and meal plan from Supabase."""

from __future__ import annotations

import sys

from dotenv import load_dotenv

from agent.users import UserStore

load_dotenv()


def main() -> None:
    username = sys.argv[1] if len(sys.argv) > 1 else "demo1"

    store = UserStore()
    user = store.get_user(username)

    if user is None:
        print(f"User {username!r} not found.")
        sys.exit(1)

    profile = user.profile.model_dump() if user.profile else None
    plan = user.active_plan.model_dump() if user.active_plan else None

    w = 60
    print("=" * w)
    print(f"  USER: {user.username}")
    print("=" * w)

    print("\n── Preferences ──")
    if not profile:
        print("  (none)")
    else:
        print(f"  Goal:           {profile.get('goal')}")
        print(f"  Calories:       {profile.get('calorie_target')}")
        print(f"  Protein (g):    {profile.get('protein_g_target')}")
        print(f"  Carbs (g):      {profile.get('carbs_g_target')}")
        print(f"  Fat (g):        {profile.get('fat_g_target')}")
        print(f"  Meals/day:      {profile.get('meals_per_day')}")
        print(f"  Cuisines:       {', '.join(profile.get('cuisine_preferences') or []) or '—'}")
        print(f"  Flavors:        {', '.join(profile.get('flavor_profiles') or []) or '—'}")
        print(f"  Allergies:      {', '.join(profile.get('allergies') or []) or '—'}")
        print(f"  Dislikes:       {', '.join(profile.get('disliked_ingredients') or []) or '—'}")

    print("\n── Meal Plan ──")
    if not plan:
        print("  (none)")
    else:
        print(f"  Notes:          {plan.get('notes') or '—'}")
        print(f"  Total calories: {plan.get('total_calories', '—')}")
        meals = plan.get("meals") or []
        print(f"  Meals:          {len(meals)}\n")
        for i, meal in enumerate(meals, 1):
            print(f"  [{i}] {meal.get('name', 'Untitled')}")
            if meal.get("description"):
                print(f"      {meal['description']}")
            for ing in meal.get("ingredients") or []:
                macros = (
                    f"{ing.get('calories', 0)} kcal · "
                    f"P{ing.get('protein_g', 0)} "
                    f"C{ing.get('carbs_g', 0)} "
                    f"F{ing.get('fat_g', 0)}"
                )
                print(f"      • {ing.get('name')} — {ing.get('quantity')}  ({macros})")
            print()

    print("=" * w)


if __name__ == "__main__":
    main()
