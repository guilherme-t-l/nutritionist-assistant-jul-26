# PRD — LLM Context Layout

## Goal
Make each LLM call clearer and cheaper: one place for the current meal plan, full user chat history, no repeated full plan JSONs.

## Problem today
Every `/plan` and `/chat` call sends:

- A system prompt with persona + profile + chat-edit rules (even on the first turn)
- A synthetic first user message that repeats calories / meals / cuisine already in the system
- Full conversation history where **every model turn is a full MealPlan JSON**

That means:

1. Constraints are partly duplicated and partly incomplete across system vs user message
2. The “current” plan is just “whatever JSON came last,” buried among older plans
3. Tokens grow fast as the user refines (N full plans in history)

## Design

**Both the user profile and the meal plan can change over the life of a session.**  
The system prompt is rebuilt on every call from the **latest** stored versions — never from a stale snapshot taken at onboarding.

Examples:

- Profile update: user changes calorie target from 2000 → 1800, or adds an allergy → next call’s system prompt uses the new profile
- Plan update: user asks to change lunch → `current_plan` is replaced with the new MealPlan → next call’s system prompt carries that plan

Each LLM call is built from three parts:

### 1. System prompt (every call)
- Nutritionist persona
- **Latest** user profile (calories, macros, goal, cuisines, flavors, allergies, dislikes, meals/day)
- **Latest** meal plan (validated MealPlan JSON) — **only after the first plan exists**

### 2. User message
- Whatever the user typed
- On the first `/plan` call only: a short task line such as `Generate my meal plan for today.` (no repeated profile fields)

### 3. Conversation history
- Prior **user** messages (kept in full — these often carry important preferences)
- Prior **assistant** turns as short notes (what changed / `notes`), **not** full MealPlan JSON
- Source of truth for profile and plan is session state → system prompt, not history

## Call shapes

### First call (`POST /plan`)
```
system:  persona + profile
messages:
  [user] Generate my meal plan for today.
```

### Later calls (`POST /chat`)
```
system:  persona + profile + current meal plan (latest JSON)
messages:
  [user]  Generate my meal plan for today.
  [model] <short note from first plan>
  [user]  Please change the lunch, I didn't like it that much.
  ...
  [user]  <new typed message>
```

## Session storage (backend)
For each session, keep mutable state:

| Field | Purpose | Updated when |
|---|---|---|
| `profile` | Latest constraints — rebuilt into system every call | Onboarding, and whenever the user changes profile fields |
| `current_plan` | Latest MealPlan — injected into system once it exists | After every successful plan/chat LLM reply |
| `history` | User texts + short assistant notes for continuity | After every user turn + assistant note |

On every LLM call: build system from **current** `profile` (+ `current_plan` if set).  
After every successful LLM reply: replace `current_plan`, append the user turn and a short assistant note to `history`.

## Out of scope
- Changing the MealPlan JSON schema
- UI redesign
- Asking clarifying questions when the user is vague (“I didn’t like lunch”)
- Prompt wording polish beyond what’s needed to implement this layout
- Summarizing / truncating very long chats (can come later)

## Acceptance criteria
- [ ] First call system prompt has persona + profile and **no** current plan
- [ ] First user message does **not** repeat calorie / cuisine / meal-count fields
- [ ] Chat calls put the **latest** plan in the system prompt
- [ ] After a successful chat reply, `current_plan` is replaced (not appended as another full JSON in history)
- [ ] If `profile` is updated mid-session, the next LLM call’s system prompt reflects the new profile
- [ ] History never stores full MealPlan JSON after this change (assistant side is short notes)
- [ ] History still preserves full user-typed messages across turns
- [ ] `/plan` and `/chat` still return a full validated `MealPlan`
- [ ] Existing prompt unit tests updated to match the new builders

---

## Dev plan (simple, one step at a time)

Do these in order. After each step, stop and review the diff before moving on.
You do **not** need to change `agent/llm.py` — it already accepts `system` + `messages`.

### Step 0 — Read the current path (no code yet)
Open these files and note what they do today:

| File | Look for |
|---|---|
| `agent/session.py` | `Session` only has `profile` + `history` |
| `agent/prompts.py` | `build_system_prompt`, `build_initial_user_message` |
| `src/app/routes/plan.py` | Appends full JSON plan into `history` |
| `src/app/routes/chat.py` | Rebuilds system from profile only; history = full turns |
| `tests/test_prompts.py` | Asserts on prompt text |
| `tests/test_chat.py` | Asserts Call 2 has 3 messages, model turn is in history |

**Done when:** you can explain in one sentence what each file owns.

---

### Step 1 — Session: add `current_plan`
**File:** `agent/session.py`

- Add optional field `current_plan: MealPlan | None = None` on `Session`
- Leave `profile` and `history` as they are for now

**Review:** only a data-shape change; nothing calls it yet.

**Test:** existing tests should still pass (`uv run pytest`).

---

### Step 2 — Prompts: new builders (pure functions)
**File:** `agent/prompts.py`

Change one thing at a time:

1. **`build_initial_user_message`** → return a short fixed task, e.g.  
   `"Generate my meal plan for today."`  
   (no calories / cuisine / meal count in this string)

2. **`build_system_prompt(profile, plan=None)`** → keep persona + profile as today;  
   if `plan` is not `None`, append a clear section with the plan JSON  
   (e.g. `"Current meal plan:\n" + plan.model_dump_json(...)`)

3. **Add a tiny helper** for history notes, e.g.  
   `build_assistant_note(plan) -> str`  
   that returns something short from `plan.notes`  
   (fallback: `"Updated the meal plan."` if notes empty)

4. **Remove** the “When the user asks for a change…” paragraph from the base system prompt  
   (edit rules are implied by sending the current plan in system on later calls)

**Review:** read the two example strings (with and without a plan) out loud — do they match the Call shapes above?

**Test:** update `tests/test_prompts.py` for the new initial message and optional plan section. Run those tests only:  
`uv run pytest tests/test_prompts.py -v`

---

### Step 3 — Wire `/plan`
**File:** `src/app/routes/plan.py`

After a valid MealPlan comes back:

1. Set `session.current_plan = plan`
2. Append to history:
   - user: initial task message
   - model: **short note** from `build_assistant_note(plan)` — **not** `raw_reply`
3. System for the LLM call: `build_system_prompt(profile)` with **no** plan yet

**Review:** after `/plan`, session has a plan object, and history’s model turn is a short string.

**Test:** `uv run pytest tests/test_plan.py -v`  
(adjust assertions if any check the fake LLM’s stored history shape)

---

### Step 4 — Wire `/chat`
**File:** `src/app/routes/chat.py`

On each chat request:

1. System: `build_system_prompt(session.profile, plan=session.current_plan)`
2. Messages: `session.history + [new user message]`  
   (history should already have short model notes, not full JSON)
3. After a valid reply:
   - replace `session.current_plan = plan`
   - append user message + short assistant note to history

**Review:** with FakeLLM, Call 2’s `system` must contain the plan JSON; Call 2’s messages must **not** contain a full MealPlan as the previous model turn.

**Test:** update `tests/test_chat.py`, then:  
`uv run pytest tests/test_chat.py -v`

---

### Step 5 — Fix anything else that builds prompts
**Files that import the prompt helpers:**  
`evals/runner.py` (and any test that assumed the old initial message)

- Pass the new function signatures
- Keep eval behavior the same (still generate a plan; just different prompt layout)

**Test:** `uv run pytest` (full suite)

---

### Step 6 — Manual check (optional but useful)
1. Start the app with your usual local script
2. Submit the onboarding form → first plan
3. Send: `Please change the lunch, I didn't like it that much.`
4. Confirm you still get a full updated plan in the UI

**Done when:** all acceptance criteria checkboxes above can be ticked  
(except mid-session profile API — that can stay “supported by storage, endpoint later” unless you add it now)

---

### Suggested order of review for you
1. `session.py` (smallest)  
2. `prompts.py` (most important to read carefully)  
3. `plan.py` then `chat.py` (how it gets called)  
4. tests last (prove the behavior)

### Intentionally later (not in this plan)
- Endpoint to update `profile` mid-session (storage already allows it after Step 1)
- Fancier assistant notes
- Trimming long chat history
