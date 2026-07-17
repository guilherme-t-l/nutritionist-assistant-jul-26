# PRD — Upload or Paste an Existing Meal Plan

## Goal

Let the user bring an existing meal plan into the app (paste text or upload a file) so chat refinements and persistence work the same as for a generated plan — without forcing them through `POST /plan` generation.

## Problem today

Onboarding only offers **Build my meal plan**. Users who already have a plan (from a nutritionist, spreadsheet, notes app, etc.) must regenerate from scratch. The login PRD explicitly left import out of scope; this PRD covers it.

## UX recommendation — how both buttons feel intuitive

**One form, two plan-entry actions. Both labels start with Save so preferences always feel committed.**

Do **not** put “upload plan” as a third path that skips preferences. The form answers *who you are*; the two buttons only answer *how you get a plan*.

```
┌─────────────────────────────────────────────┐
│  Onboarding (same fields as today)          │
│  goal, calories, macros, meals/day, …       │
│                                             │
│  [ Save & Build New Plan ]       ← primary  │
│  [ Save & Upload Existing Plan ] ← secondary│
└─────────────────────────────────────────────┘
```

| Control | What the user thinks | What the app does |
|---|---|---|
| **Save & Build New Plan** | “Save my prefs and generate a plan” | Validate prefs → `POST /plan` → save profile + generated plan (logged-in) |
| **Save & Upload Existing Plan** | “Save my prefs and bring my own plan” | Validate prefs → show import panel → normalize → save profile + imported plan (logged-in) |

### Why this feels right

1. **“Save & …” on both buttons.** The shared prefix makes the prefs commit obvious; only the second half differs (build vs upload).
2. **Shared form = shared commitment.** Both exits leave the same fields, so prefs never look like they only apply to one path.
3. **Primary vs secondary hierarchy.** Generation stays the main CTA; import is clearly an alternative, not a competing product mode.
4. **Import is step 2 of the same flow**, not a separate welcome option. After **Save & Upload Existing Plan**, expand (or advance to) a single import surface:

```
┌─────────────────────────────────────────────┐
│  Preferences will be saved with this plan.  │
│  Paste your plan or choose a file.          │
│                                             │
│  [  paste area / drop zone  ]               │
│                                             │
│  [ Use this plan ]   [ Back ]               │
└─────────────────────────────────────────────┘
```

5. **Same success landing.** Both paths end in the split chat + plan UI with the same assistant note style (“Here’s your meal plan…”) so import doesn’t feel like a different product.
6. **“Save preferences” stays alone.** In prefs-only mode (`PUT /profile`), keep a single **Save preferences** button — no upload. Import belongs only to first plan / **New plan**.

### Copy (canonical)

- Primary button: **Save & Build New Plan**
- Secondary button: **Save & Upload Existing Plan**
- Import panel title: **Bring your plan**
- Import submit: **Use this plan**
- Helper: *Your preferences will be saved together with this plan.*

### Rejected alternatives

| Idea | Why not |
|---|---|
| Upload from welcome, prefs later | Chat needs profile (allergies, targets) immediately; easy to forget prefs |
| Two equal primary buttons | Unclear that prefs apply to both; noisy first viewport |
| Import without saving prefs | Breaks continuity; logged-in users would get a plan with a stale/empty profile |
| Auto-save prefs on every field blur | Surprising; both CTAs should be the explicit commit |

## Design

### Flow

1. User fills onboarding (or **New plan** form) as today.
2. Chooses **Save & Build New Plan** → existing `POST /plan` path.
3. Or chooses **Save & Upload Existing Plan** → import UI (prefs kept in memory / form state; not discarded).
4. User pastes freeform text and/or uploads `.txt` / `.md` / `.json` (MVP: text content only; no OCR/PDF required).
5. `POST /plan/import` (name flexible):
   - Body: `{ profile: UserProfile, source_text: string }` (file contents read client-side into `source_text`)
   - Server normalizes into `MealPlan` (see below)
   - Creates session like `/plan` (profile + `current_plan` + short history note)
   - Logged-in: `save_profile_and_plan` — **same write rule as successful `/plan`**
6. Frontend enters split view with returned `session_id` + plan.

### Normalization (MVP)

Prefer a small, reliable pipeline:

1. If `source_text` parses as JSON matching `MealPlan` → use it (no LLM).
2. Else → one LLM call: “convert this user meal plan into our `MealPlan` schema” with the user’s profile in the system prompt (allergies / meals_per_day as constraints, not a full regenerate).
3. Validate with Pydantic; 422/502 with a clear message if unusable.

Guest vs logged-in: same as `/plan` — guests get session-only; logged-in persist profile + plan on success.

### Relation to other PRDs

| PRD | Interaction |
|---|---|
| Login & persistent meal plan | Fills the gap that was out of scope (“Uploading or pasting an external meal plan”) |
| Only update after **Save plan** | Import (like initial `/plan`) **may** write `active_plan` on first success; chat auto-save rules stay as in that PRD |

### DB write rules (additions)

| Action | Writes `profile`? | Writes `active_plan`? |
|---|---|---|
| `POST /plan/import` succeeds | yes (logged-in) | yes (logged-in) |
| Import fails | no | no |

## Out of scope

- PDF / image OCR
- Multi-day week planners beyond current `MealPlan` (one day of meals)
- Syncing from MyFitnessPal / Google Sheets APIs
- Import from prefs-only mode
- Changing guest vs logged-in persistence model

## Acceptance criteria

- [ ] Onboarding / New plan shows **Save & Build New Plan** (primary) and **Save & Upload Existing Plan** (secondary)
- [ ] Prefs-only mode still shows only **Save preferences**
- [ ] Paste and file → text both reach the same import endpoint
- [ ] Successful import lands in split UI with a valid `MealPlan` and working `/chat`
- [ ] Logged-in import persists **both** profile and `active_plan`; guest does not touch `users.db`
- [ ] Invalid / empty source returns a clear error; no partial DB write
- [ ] Valid JSON `MealPlan` import works without requiring the LLM

---

## Dev plan (simple)

### Step 1 — `POST /plan/import`

Accept `profile` + `source_text`. Normalize (JSON path + LLM fallback) → session + optional `save_profile_and_plan`. Mirror `/plan` response shape `{ session_id, plan }`.

**Test:** JSON MealPlan body → 200, session has plan; logged-in row has profile + plan. Bad text → error, DB unchanged.

### Step 2 — Onboarding UI

Secondary button → import panel; **Use this plan** calls `/plan/import` with form profile + pasted/file text. **Back** returns to the form without clearing fields.

### Step 3 — Manual check

1. Fill prefs → **Save & Build New Plan** → same as today  
2. Fill prefs → **Save & Upload Existing Plan** → paste a simple day plan → split UI + chat tweak works  
3. Logged-in: reload → imported plan + prefs still there  
4. Prefs-only path: still no upload control  

**Done when:** acceptance criteria above can be ticked.
