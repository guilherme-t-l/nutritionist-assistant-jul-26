#!/usr/bin/env python3
"""Pretty-print a user's preferences and meal plan from users.db."""

import json
import sqlite3
import sys
from pathlib import Path

DB = Path(__file__).resolve().parent / "users.db"


def main() -> None:
    username = sys.argv[1] if len(sys.argv) > 1 else "demo1"

    conn = sqlite3.connect(DB)
    row = conn.execute(
        "SELECT username, profile_json, active_plan_json FROM users WHERE username = ?",
        (username,),
    ).fetchone()
    conn.close()

    if not row:
        print(f"User {username!r} not found.")
        sys.exit(1)

    name, profile_json, plan_json = row
    profile = json.loads(profile_json) if profile_json else None
    plan = json.loads(plan_json) if plan_json else None

    w = 60
    print("=" * w)
    print(f"  USER: {name}")
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
