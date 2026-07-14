# PRD — Prompt Inspector

## Goal
See exactly what we send to the LLM on each `/plan` and `/chat` call — system prompt and messages — so we can verify persona, profile, current plan, and history without guessing.

## Problem today
After the LLM-context layout change, the payload is assembled in code (`build_system_prompt` + `session.history`) but never shown. Production routes don’t write to `traces.db`. To inspect a live call you have to temporarily add `print`s or rebuild prompts by hand in a REPL.

## Design
One choke point: log (or record) the final `system` + `messages` right before `GeminiLLM.chat` calls the API.

What to show per call:

| Part | Where it lives |
|---|---|
| Persona + profile | Start of `system` (always) |
| Current plan | `system` after `Current meal plan:` (chat only) |
| History + new user turn | `messages` list (user texts + short assistant notes) |

Keep it local/dev-only: gate behind an env flag (e.g. `PROMPT_INSPECTOR=1`). Off by default so normal runs stay quiet.

**Preferred shape:** print a clear block to the uvicorn terminal (enough for learning). Reusing `traces.db` is optional, not required for v1.

## Out of scope
- Frontend / browser UI for prompts
- Token counting or cost dashboards
- Changing prompt text or session layout
- Always-on production tracing

## Acceptance criteria
- [ ] With the flag on, each `/plan` and `/chat` LLM call prints (or records) `system` + `messages`
- [ ] With the flag off, behavior is unchanged (no extra noise)
- [ ] First call shows system **without** a current-plan block; chat calls show it when `current_plan` exists
- [ ] History lines in the dump are short notes on the model side, not full MealPlan JSON
- [ ] One small test (or manual checklist) proves the flag gates the inspector

---

## Dev plan (simple)

Do these in order. Stop and review after each step.

### Step 1 — Helper that formats the dump
**File:** e.g. `agent/prompt_inspector.py` (new, tiny)

- `dump_llm_input(system: str, messages: list[Message]) -> None`
- Pretty-print labeled sections to stdout (SYSTEM / MESSAGES)
- No I/O elsewhere; pure side-effect print for now

**Test:** call the helper in a unit test and assert it includes a known substring (capture stdout), or skip automated test and do a manual check in Step 3.

### Step 2 — Call it from `GeminiLLM.chat`
**File:** `agent/llm.py`

- If `os.environ.get("PROMPT_INSPECTOR") == "1"`, call `dump_llm_input(system, messages)` before `generate_content`
- FakeLLM / tests unaffected unless they set the flag

**Review:** one `if` at the choke point — every real LLM call is covered.

### Step 3 — Manual check
1. `PROMPT_INSPECTOR=1` in `.env` (or export) and restart the app
2. Submit onboarding → confirm terminal dump: persona + profile, no current plan, short task message
3. Send a chat refinement → confirm dump: plan in system, history notes short, new user message last
4. Turn the flag off → confirm quiet again

**Done when:** acceptance criteria above can be ticked.
