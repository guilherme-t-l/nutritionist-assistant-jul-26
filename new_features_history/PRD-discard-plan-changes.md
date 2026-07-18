# PRD — Discard Unsaved Plan Changes

## Goal

After refining a meal plan in chat, let the user **discard** those working changes and restore the last saved plan — without needing to leave the page or start a new chat.

## Problem today

Chat updates the on-screen / session plan but not `active_plan` until **Save plan**. If the user regrets a swap, the only recovery paths are reload (may keep dirty sessionStorage) or navigation confirms that abandon the session — there is no explicit revert control next to Save.

## Design

Mirror **Save plan** with a **Discard** control:

1. Logged-in only; visible with Save in the plan pane
2. Enabled only when the plan is dirty (same gate as Save)
3. New endpoint `POST /plan/discard` with `{ session_id }`:
   - Auth required
   - Load session + user’s `active_plan`
   - Set `session.current_plan = active_plan`
   - Clear `session.history` so chat no longer refers to discarded edits
   - **Do not** write `users.db`
   - Return `{ plan }` (the restored saved plan)
4. UI: click Discard → call discard (no confirm) → replace plan pane, clear chat thread, clear dirty state
5. Guest flow unchanged (no Discard control)

## Out of scope

- Undo stack / multi-step revert
- Keeping chat history while restoring the plan
- Changing Save or leave-page confirm behavior beyond sharing dirty state

## Acceptance criteria

- [ ] After chat without Save, **Discard** restores the on-screen plan to `active_plan`
- [ ] DB `active_plan` is unchanged by Discard
- [ ] Session history is cleared so the next `/chat` is based on the restored plan
- [ ] Discard disabled when clean / while saving / while waiting on chat
- [ ] Guest: no Discard control
- [ ] Unknown / expired `session_id` returns an error; no partial write

---

## Dev plan (simple)

### Step 1 — `POST /plan/discard`

Auth + session + stored plan checks; restore `current_plan`; clear history; return plan.

**Test:** chat changes working plan; discard returns saved notes; DB unchanged; further chat still does not auto-save.

### Step 2 — UI

**Discard** button next to Save; call endpoint on click (no confirm); update plan + clear messages + `markPlanClean`.

### Step 3 — Manual check

1. Logged-in: chat a swap → Discard → UI shows original plan; reload / resume still original
2. Same flow but Save instead → reload shows swapped plan
3. Guest: no Discard

**Done when:** acceptance criteria above can be ticked.
