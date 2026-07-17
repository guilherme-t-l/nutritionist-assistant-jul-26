# PRD — Only Update Plan After “Save Plan”

## Goal

Let the user refine a meal plan in chat (e.g. substitute one food/meal for a day) without overwriting their saved plan until they explicitly click **Save plan**.

## Problem today

Every successful `POST /chat` for a logged-in user calls `save_plan` and replaces `active_plan` in the DB. Session UI and persisted plan stay in lockstep. Users who only want a one-off swap still permanently update their whole plan.

## Design

Separate **working plan** (session / UI) from **saved plan** (DB `active_plan`):

1. `/chat` still updates `session.current_plan` and returns the new plan for the split UI — **no** `save_plan` on each turn
2. Add a **Save plan** control in the plan pane (logged-in only; guests have nothing to persist)
3. New endpoint e.g. `POST /plan/save` (or `PUT`) with `{ session_id }` — writes `session.current_plan` to `user_store.save_plan`
4. UI: after a chat update that differs from the last saved snapshot, show Save as enabled / dirty; after a successful save, clear dirty state
5. Resume / login still loads `active_plan` — only what was last saved
6. Confirm before leaving if dirty.

## Out of scope

- Auto-save timers
- Saving mid-chat drafts without the user clicking Save
- Changing guest behavior (still session-only)

## Acceptance criteria

- [ ] Logged-in chat refinements update the on-screen plan but leave `active_plan` unchanged until Save
- [ ] Clicking **Save plan** persists the current session plan; reload / resume shows that plan
- [ ] Guest flow unchanged (no Save, no DB write)
- [ ] Save with unknown / expired `session_id` returns an error; no partial write
- [ ] Initial `/plan` (and import, if present) still may write on first success — this PRD only changes **chat** auto-persist

---

## Dev plan (simple)

### Step 1 — Stop auto-save on `/chat`

Remove `user_store.save_plan` from the `/chat` success path. Update persistence tests accordingly.

**Test:** logged-in `/chat` returns new plan; DB `active_plan` still the pre-chat value.

### Step 2 — `POST /plan/save`

Auth required. Load session → `save_plan(username, session.current_plan)` → `{ ok: true }` (or return the plan).

**Test:** after chat without save, DB unchanged; after save, DB matches session plan.

### Step 3 — UI

**Save plan** button in the plan pane (logged-in). Enable when plan dirty vs last saved; call `/plan/save` on click; disable / show saved on success.

### Step 4 — Manual check

1. Logged-in: build plan → chat a one-day swap → reload → old plan still there
2. Same flow but click Save → reload → swapped plan persists
3. Guest: chat still works; no Save control

**Done when:** acceptance criteria above can be ticked.
