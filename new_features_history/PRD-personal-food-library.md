# PRD — Personal Food Library

## Goal

Let a logged-in user keep a personal list of foods they already eat (with a serving and macros), and let the agent use that list when adapting a meal — without treating those foods as approved, recommended, or preferable to the meal plan.

## Problem today

The agent only knows two things about how this user eats:

- **Profile** (`UserProfile`) — targets, allergies, dislikes, cuisines
- **Active meal plan** (`MealPlan`) — the formal plan, made of `Food` lines (`name`, `quantity`, calories, protein, carbs, fat)

Anything the user eats outside that plan (“my pancakes”, “the brownie I make on Sundays”) exists only if they type it into chat. The agent then has to guess the macros, and the guess is gone on the next session.

There is no place for the user to view, add, edit, or delete those foods.

## Key principle

Personal foods expand what the agent knows about how the user eats. They do not redefine what the user should eat.

The meal plan stays the anchor. The library is context.

## Design

### Who this is for

Logged-in users only. Guests have no durable user row (`UserStore` is never written for them), so they get no library and no library UI. Their chat behavior stays as it is today.

### What a personal food is

One saved item = one serving of something the user eats, with the same nutritional fields as a meal-plan `Food`, plus a structured serving so the agent can scale it.

| Field | Required | Meaning |
|---|---|---|
| `id` | yes | Stable id for edit and delete |
| `name` | yes | What the user calls it, e.g. `Special Pancake` |
| `serving_size` | yes | Number of units in one serving, e.g. `1` |
| `serving_unit` | yes | Unit label, e.g. `pancake` |
| `calories` | yes | kcal for **one** serving |
| `protein_g` | yes | grams for one serving |
| `carbs_g` | yes | grams for one serving |
| `fat_g` | yes | grams for one serving |
| `ingredients` | no | Optional recipe lines, e.g. `["oat flour", "egg", "cocoa"]` |

Example the user sees:

```
Special Pancake
1 pancake · 140 kcal · 12C · 10P · 4F
[Edit] [Delete]
```

Stored as:

```json
{
  "id": "…",
  "name": "Special Pancake",
  "serving_size": 1,
  "serving_unit": "pancake",
  "calories": 140,
  "protein_g": 10,
  "carbs_g": 12,
  "fat_g": 4,
  "ingredients": []
}
```

**Macros are per one serving, and they are the source of truth.** Ingredient lines are a note for the user and the agent (“what’s in it”). They are not summed into the macros. A user who knows “1 pancake = 140 kcal” should not have to enter flour, egg, and cocoa as separate foods.

**Why not reuse `Food` as-is.** A meal-plan `Food.quantity` is a display string (`"1 pancake"`, `"150 g"`). That string is what the plan shows, but it is a poor place to store “the unit I scale from.” `serving_size` + `serving_unit` is that unit. When the agent puts the food on the plan, it still writes a normal `Food`: quantity becomes a string such as `"2 pancakes"`, and the macros are the per-serving numbers multiplied by how many servings it chose (rounded to whole grams / kcal, because `Food` stores ints).

The library row itself is never rewritten just because a meal used it.

### Where it is stored

A new JSON list on the existing `users` row, next to `profile_json` and `active_plan_json` — for example `personal_foods_json`.

Same pattern as profile and plan: one blob per user, read and written through `UserStore`. The list is small (foods one person cooks, not a catalog), so search and filter happen in the browser over the full list. No separate foods table in this version.

Empty or missing column means “no personal foods.” That user behaves exactly as today.

### User capabilities

A **My foods** screen, logged-in only, opened from the app header (same area as **Updated Preferences**). It is not part of the meal-plan pane. The plan pane still shows only the current plan.

The user can:

- View every personal food, with serving and macros in the line format above
- Search / filter by name (client-side; case-insensitive substring)
- Add a food (name, serving size, serving unit, four macros; ingredients optional)
- Edit those same fields
- Delete a food

Validation on save:

- `name` and `serving_unit` non-empty
- `serving_size` greater than 0
- calories, protein, carbs, fat each ≥ 0 (same idea as `Food`)

Unknown id on edit or delete returns an error and does not change the list.

### Agent behavior

The library is included in the **edit** system prompt (`build_edit_system_prompt`, used by `POST /chat`), after the profile constraints and before the current meal plan. It is also included when an import is adapted to preferences (that path is also “edit this plan”). It is **not** included in the from-scratch create prompt (`build_create_system_prompt` / `POST /plan`).

Why edit only: the first plan is generated from a fixed task line (“Generate my meal plan…”). There is no user sentence yet that asks for a personal food. Putting the library on that call invites the model to season a new plan with pancakes just because they exist. Adaptation is where the three triggers below actually happen.

If the list is empty, omit the section. Do not send “Personal foods: none.”

The prompt must say, in substance:

- The meal plan is the anchor. Personal foods are foods this user eats; they are not approved, recommended, or preferred.
- Use a personal food only when one of these is true:
  1. The user explicitly asks for it (“I want my pancakes tomorrow.”) — choose a quantity that fits the day’s targets, and add other foods if the serving alone misses the meal.
  2. The user asks for a substitution (“What can I eat instead of breakfast?”) — personal foods are options alongside other suitable foods, not the whole menu.
  3. The user says they already have it (“I have my pancakes ready.”) — fit that food into the meal and keep the nutritional targets.
- Do not insert personal foods on your own.
- Do not prefer a personal food only because it is in the library.
- Do not limit suggestions to the library.
- Allergies still win. A personal food that conflicts with an allergy is not used.

The agent does the quantity math in the reply (2 pancakes → 280 kcal, 20P / 24C / 8F). No new calculator tool in this version.

Chat still returns a normal `MealPlan`. Saving or discarding the plan does not change the library, and editing the library does not change `active_plan`.

Each `/chat` call reads the library from `UserStore` at request time, not from a copy stored on the session. A food added or edited before the next message is what the agent sees.

## Out of scope

- Guests
- Treating library foods as nutritionist-approved or as favorites that outrank the plan
- Seeding a brand-new plan from the library
- A global / USDA food database, barcode scan, or macro lookup
- Ingredient-level macros, or checking that ingredients sum to the serving
- Sharing a library between users
- A search tool the model must call (the full list goes in the prompt; revisit only if lists get large)
- Changing `Food` or `MealPlan` shape

## Acceptance criteria

- [ ] Logged-in user can list, search by name, add, edit, and delete personal foods; each row shows serving and macros (`1 pancake · 140 kcal · 12C · 10P · 4F`)
- [ ] Macros and serving are stored per one serving; optional ingredients are saved and shown, and are not required
- [ ] Invalid name, serving, or negative macros are rejected; the stored list is unchanged
- [ ] Guest has no library endpoints that succeed, and no **My foods** control
- [ ] Edit prompt for a user with foods includes the library plus the “not approved / do not force / do not prefer / do not restrict” rules, and still includes the current meal plan as the thing to edit
- [ ] Edit prompt for a user with no foods matches today’s prompt (no personal-food section)
- [ ] Create prompt (`/plan`) does not include the library
- [ ] Saving or discarding a meal plan does not write the library; editing the library does not write `active_plan`
- [ ] A food added before the next `/chat` is present in that call’s system prompt

---

## Dev plan (simple)

Do these in order. After each step, stop and review the diff before moving on.

### Step 1 — `PersonalFood` schema

Add a `PersonalFood` model in `agent/schemas.py` with the fields in the table above. This is a new type. It does not change `Food` or `MealPlan`.

**Test:** a valid pancake parses; empty name, `serving_size <= 0`, and negative calories are rejected; missing `ingredients` becomes an empty list.

### Step 2 — Persist the list on the user

Add `personal_foods_json` on `users` (Supabase). Extend `UserRecord` and `UserStore` with read and replace-the-list methods. Do not touch `profile_json` or `active_plan_json` in those methods.

**Test:** save a list, read it back; a user with a null column reads as an empty list; saving foods does not change profile or plan.

### Step 3 — CRUD endpoints

Logged-in only, same cookie auth as `/profile`:

- `GET /personal-foods` → the list
- `POST /personal-foods` → create, server assigns `id`
- `PUT /personal-foods/{id}` → replace that item
- `DELETE /personal-foods/{id}` → remove it

**Test:** guest is rejected; create then list shows the food; edit changes macros; delete removes only that id; bad id does not wipe the list.

### Step 4 — Prompt

In `agent/prompts.py`, append the library block only on the edit (and import-adapt) prompt, and only when the list is non-empty. Wording follows **Agent behavior** above.

`/chat` loads the list from `UserStore` for the logged-in user and passes it into the edit prompt builder. Guests pass nothing.

**Test:** edit prompt contains the pancake line and the do-not-prioritize rules; empty list leaves the prompt unchanged; create prompt has no library section. Existing prompt tests still pass.

### Step 5 — My foods screen

In `src/app/templates/onboarding.html`, add a logged-in **My foods** entry in the header and a screen that lists, searches, adds, edits, and deletes via the endpoints in Step 3. Show the serving + macro line from the spec.

**Check:** log in, add Special Pancake, search “pan”, edit fat from 4 to 5, delete it. Log out and confirm the control is gone. Meal plan pane is unchanged by those actions.

### Step 6 — Manual agent check

1. Save the pancake. Open chat and say “I want my pancakes tomorrow.” The edited plan may include them, with quantity and macros scaled from one serving, and the rest of the day still near the targets.
2. “What can I eat instead of breakfast?” Pancakes may appear as one option, not the only option.
3. “I have my pancakes ready.” The meal uses them and still aims at the targets.
4. A normal tweak (“make lunch lighter”) does not pull in the pancake.
5. Delete every personal food and repeat (4). Behavior matches a user who never had a library.

**Done when:** the acceptance criteria above can be ticked.
