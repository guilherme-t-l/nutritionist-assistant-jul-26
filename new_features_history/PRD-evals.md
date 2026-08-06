# PRD — Eval Framework (portable module)

## Goal

Build a **standalone, reusable evaluation framework** that measures **two systems at once**:

1. **The agent** — is the nutrition assistant producing conversations a domain expert would approve? Measured as a pass rate over batches of generated conversations, rated by you (ground truth) and by an LLM-as-a-judge (scalable proxy).
2. **The judge** — does the LLM-as-a-judge issue the same verdicts you would? Measured as an alignment rate: judge verdicts vs your verdicts on the same conversations.

The two measurements feed each other in a loop:

```
        measure the AGENT                      measure the JUDGE
  ┌──────────────────────────┐          ┌──────────────────────────────┐
  │ you rate a batch          │          │ judge rates the same batch    │
  │ → agent pass rate         │─────────▶│ → alignment vs your verdicts  │
  │   (human ground truth)    │          │   (is the judge trustworthy?) │
  └──────────────────────────┘          └──────────────┬───────────────┘
                                                        │ alignment good enough
                                                        ▼
                                        ┌──────────────────────────────┐
                                        │ judge rates NEW batches alone │
                                        │ → agent pass rate at scale    │
                                        │   (cheap, repeatable)         │
                                        └──────────────────────────────┘
```

Concretely, three activities:

1. **Manual evals** — you rate agent conversations Pass / Fail on a review screen → **agent quality**, human ground truth.
2. **Alignment checks** — the judge rates the *same* conversations you already rated → **judge quality**.
3. **LLM-as-a-judge evals** — the judge rates fresh batches on its own → **agent quality** at scale, trustworthy in proportion to the alignment measured in (2).

A dashboard (modeled on the reference mockups) visualizes both: agent pass rates (by human and by judge) and human↔judge alignment, filtered by batch and date range.

## Portability constraint (the most important design decision)

The framework lives in its own top-level folder (`eval_framework/`) and **never imports the host app's code directly**. It talks to the agent being evaluated through one small interface — the **Target Adapter**:

```python
class TargetAgent(Protocol):
    def run_conversation(self, starter: ConversationStarter) -> Conversation:
        """Given a starter (one or more scripted user turns),
        return the full conversation transcript."""
```

Each project that adopts the framework writes one adapter file (~30 lines). For this project, the adapter calls the nutri-assistant over HTTP (`POST /plan`, `POST /chat` on localhost), which keeps zero code coupling. To port the framework elsewhere, you copy the folder and write a new adapter.

> Python concept: a `Protocol` is a structural interface — any class with a matching `run_conversation` method satisfies it, no inheritance needed. See "Protocols & duck typing" in `Python_ A Mini-Book for the PM Who Reads Code.md` if covered there.

Everything else — storage, review UI, judge, dashboard — is project-agnostic.

## Vocabulary

| Term | Meaning |
|---|---|
| **Starter file** | A JSON file you write with 10–50 conversation starters |
| **Starter** | One scripted scenario: an initial profile/context + one or more user turns |
| **Batch** | One generation run over a starter file → N stored conversations |
| **Conversation** | Full transcript (user + assistant turns) produced by the target agent |
| **Verdict** | Binary Pass / Fail on one conversation, from a **human** or a **judge** |
| **Comparison type** | Which pair is being compared: Agent vs Human, Judge vs Agent, Judge vs Human |

## The three workflows

### Workflow 1 — Manual eval (`run manual eval`)

**Measures: the agent** (with your verdicts as ground truth).

```
starters.json ──▶ [Generate batch] ──▶ conversations stored (status: awaiting review)
                                              │
                                              ▼
                              Review screen: you rate each Pass/Fail
                                              │
                                              ▼
                              Batch marked "human-rated" → appears on dashboard
                              (dashboard view: Agent vs Human)
```

### Workflow 2 — Alignment check (`run llm-as-a-judge alignment check`)

**Measures: the judge** (against your verdicts from Workflow 1).

Pre-condition: a batch that is already human-rated. You pick it; the judge rates the **same conversations** (blind — it never sees your verdicts). We then compute agreement.

```
pick human-rated batch ──▶ judge rates each conversation ──▶ alignment computed
                                                        (dashboard view: Judge vs Human)
```

### Workflow 3 — Judge-only eval (`run llm-as-a-judge evals`)

**Measures: the agent** (with the judge as a scalable stand-in for you).

Same as Workflow 1, but the judge rates instead of you. Its numbers are only as trustworthy as the alignment rate measured in Workflow 2 — the dashboard shows the two side by side so this caveat stays visible.

```
starters.json ──▶ [Generate batch] ──▶ judge rates each ──▶ dashboard
                                              (dashboard view: Judge vs Agent)
```

Any batch can accumulate **both** human and judge verdicts — that is exactly what makes an alignment check possible.

## Starter file format

One JSON file per batch, checked into `eval_framework/starters/`:

```json
{
  "name": "q3-clinical-audit",
  "description": "Allergy edge cases + macro accuracy scenarios",
  "starters": [
    {
      "id": "allergy-peanut-01",
      "category": "Safety",
      "context": { "goal": "lose_weight", "allergies": ["peanuts"], "calorie_target": 1800 },
      "turns": [
        "Create my meal plan",
        "Add a pad thai dinner"
      ]
    }
  ]
}
```

- `context` is passed opaquely to the adapter (for nutri-assistant it becomes the onboarding profile; another project may use it differently — this opacity is what keeps the format portable).
- `turns` is an ordered list of user messages. One turn = single-shot; several = a scripted multi-turn conversation.
- `category` is free-form and powers the per-category breakdown on the dashboard (like "Meal Planning / Macro Accuracy / Calorie Counting" in the mockups).

## Data model (SQLite — `eval_framework/data/evals.db`)

SQLite over Supabase because the framework must be portable and self-contained — no external service, no credentials, copy the folder and it works. (Same trade-off as `traces.db`.)

```sql
batches(
  batch_id TEXT PK,          -- e.g. "q3-clinical-audit-2026-07-24"
  name TEXT,
  starter_file TEXT,          -- filename snapshot for provenance
  created_at TEXT,            -- ISO timestamp; powers date filters
  status TEXT                 -- generating | ready | error
)

conversations(
  conversation_id TEXT PK,
  batch_id TEXT FK,
  starter_id TEXT,
  category TEXT,
  context_json TEXT,           -- what was sent to the adapter
  transcript_json TEXT,        -- [{role, content}, ...] full turns
  agent_meta_json TEXT,        -- latency, model name, errors (from adapter)
  created_at TEXT
)

verdicts(
  verdict_id TEXT PK,
  conversation_id TEXT FK,
  rater_type TEXT,             -- 'human' | 'judge'
  verdict TEXT,                -- 'pass' | 'fail'
  reasoning TEXT,              -- judge rationale, or your optional note
  judge_prompt_version TEXT,   -- which LLM-as-a-judge prompt version was used (null for human)
  judge_model TEXT,            -- e.g. 'gemini-2.5-flash' (null for human)
  created_at TEXT
)
```

Key property: verdicts hang off conversations, so one conversation can hold a human verdict *and* a judge verdict. Alignment = join the two on `conversation_id`.

## The judge

- One **LLM-as-a-judge prompt** stored as a versioned file: `eval_framework/judge_prompts/v1.md`. The `judge_prompt_version` on each verdict records which prompt produced it — when you edit the prompt, past verdicts stay attributable.
- Judge sees: the LLM-as-a-judge prompt + the conversation transcript + the starter context. Never sees human verdicts (blind rating).
- Output is schema-constrained JSON: `{ "verdict": "pass" | "fail", "reasoning": "..." }`.
- LLM access goes through the same `Protocol` pattern as the target adapter (`JudgeLLM.complete(prompt) -> str`), so the judge model is swappable per project. Default implementation: Gemini via `google-genai`, reusing the `GEMINI_API_KEY` already in `.env`.

The starting LLM-as-a-judge prompt for nutri-assistant (v1): fail if any of — allergen present in plan, calorie total off target by >10%, response ignores the user's request, plan JSON malformed/incoherent. Otherwise pass. (Refine after the first alignment check shows where you and the judge disagree.)

## Screens

The framework runs as its **own small FastAPI app** (separate port, e.g. `:8100`), completely independent from the host app. Plain HTML + Jinja2, same stack you already know from `onboarding.html`.

### Screen 1 — Batches (home)

```
┌────────────────────────────────────────────────────────┐
│  Eval Framework                        [ New batch ▾ ] │
│                                        · manual eval    │
│                                        · judge eval     │
│  BATCH                DATE      CONVS  HUMAN  JUDGE     │
│  q3-clinical-audit    Jul 24    32     32/32  32/32  ─▶ │
│  starter-smoke-test   Jul 20    10     10/10  —      ─▶ │
│                                 [Run alignment check]   │
└────────────────────────────────────────────────────────┘
```

- **New batch** → pick a starter file → generation runs with a progress indicator → batch becomes reviewable.
- **Run alignment check** appears on any human-rated batch without judge verdicts.

### Screen 2 — Review (manual rating)

One conversation at a time, keyboard-friendly (rating 10–50 conversations must be fast):

```
┌────────────────────────────────────────────────────────┐
│  Batch: q3-clinical-audit         Conversation 4 of 32 │
│  Category: Safety · Starter: allergy-peanut-01          │
│                                                          │
│  ┌ context ─────────────────────────────────────────┐   │
│  │ goal: lose_weight · allergies: peanuts · 1800kcal│   │
│  └──────────────────────────────────────────────────┘   │
│  USER  Create my meal plan                               │
│  AGENT { meal plan ... }                                 │
│  USER  Add a pad thai dinner                             │
│  AGENT { updated plan ... }                              │
│                                                          │
│  Note (optional): [_____________________________]        │
│                                                          │
│        [ ✓ Pass (P) ]        [ ✗ Fail (F) ]              │
│  ◀ Prev                                        Next ▶    │
└────────────────────────────────────────────────────────┘
```

- P / F keys rate and auto-advance. Verdicts are re-editable (latest human verdict wins).
- If judge verdicts already exist for this batch, they are **hidden by default** on this screen so your rating stays blind too; a toggle reveals them after you've rated.

### Screen 3 — Dashboard

Mirrors the reference mockups. Global filters at the top: **batch selector** (one / all) and **date range**. Four views in a sidebar:

| View | Measures | What it shows | Mockup |
|---|---|---|---|
| **Overview** | Both | Total conversations reviewed, overall pass rate donut, pass-rate trend, table of comparison types with sample size + pass rate | Image 1 |
| **Agent vs Human** | Agent | Pass/Fail distribution from *your* verdicts, pass rate by category (bars), audit log table (conversation, category, verdict, timestamp → click through to transcript) | Image 4 |
| **Judge vs Agent** | Agent | Judge-issued pass rate, total passes/fails, list of failed conversations with the judge's reasoning expanded — plus the current alignment rate shown as a trust caveat | Image 2 |
| **Judge vs Human** | Judge | The alignment view: 2×2 agreement grid (Both Pass / Both Fail / Human Pass–Judge Fail / Human Fail–Judge Pass), **alignment rate**, disagreement table (case, judge verdict, your verdict, false-positive vs false-negative) → click through to transcript | Image 3 |

Metric definitions (so the numbers are unambiguous):

- **Pass rate** = passes ÷ rated conversations, per rater type.
- **Alignment rate** = conversations where human and judge verdicts match ÷ conversations with both verdicts.
- **False positive** (from your point of view) = judge Pass, you Fail — the dangerous kind: the judge would wave through something you'd reject.
- **False negative** = judge Fail, you Pass — noisy but safe.

Charts: server-rendered data + a lightweight client chart lib (Chart.js via CDN — no build step, keeps the module copy-pasteable).

## API surface (eval app, port 8100)

| Method | Path | Purpose |
|---|---|---|
| `GET` | `/` | Batches screen |
| `POST` | `/batches` | Body: `{starter_file, mode: "manual" \| "judge"}` → generate batch (judge mode auto-rates after generation) |
| `GET` | `/batches/{id}/review` | Review screen |
| `POST` | `/conversations/{id}/verdict` | Body: `{rater_type: "human", verdict, note}` |
| `POST` | `/batches/{id}/judge` | Run alignment check / judge a batch |
| `GET` | `/dashboard` | Dashboard shell (`?view=overview&batch=…&from=…&to=…`) |
| `GET` | `/api/dashboard-data` | JSON metrics for charts, honoring the same filters |

Batch generation for 10–50 conversations takes minutes (sequential LLM calls). MVP approach: run generation in a background task, show a progress state on the batches screen, poll for status. No queues or workers.

## File layout

```
eval_framework/
  README.md                   ← how to port to a new project (write the adapter, done)
                                (this PRD lives in new_features_history/PRD-evals.md)
  app.py                      ← FastAPI app (screens + API)
  db.py                       ← SQLite store (batches / conversations / verdicts)
  generation.py               ← starter file → batch of conversations (via adapter)
  judge.py                    ← LLM-as-a-judge prompt loading + judge LLM call + verdict writing
  metrics.py                  ← pass rate / alignment / quadrants (pure functions, unit-testable)
  adapters/
    base.py                   ← TargetAgent + JudgeLLM Protocols
    nutri_http.py             ← THIS project's adapter (HTTP → localhost:8000)
    gemini_judge.py           ← default judge LLM implementation
  judge_prompts/
    v1.md
  starters/
    example.json
  templates/                  ← Jinja2 (batches / review / dashboard)
  data/                       ← evals.db lives here (gitignored)
  tests/                      ← metrics + db + judge-parsing tests (FakeLLM style, no real API)
```

Nothing under `eval_framework/` imports from `agent/` or `src/` — enforced by a test that scans imports.

## Out of scope (MVP)

- Multiple human raters / inter-rater agreement
- Graded scores (1–5) — binary Pass/Fail only, per the goal
- Auto-generated conversation starters (you write the files by hand)
- Judging live production traffic / sampling from Supabase sessions
- CI integration (run evals on every commit) — natural later phase
- Auth on the eval app (local tool, single user)
- Statistical significance testing on alignment (just show the counts honestly)

## Dev plan (one step at a time, review the diff after each)

1. **Skeleton + storage** — folder layout, `db.py` with the three tables, `metrics.py` pure functions. Tests for both. *(No LLM, no UI — verify the math first.)*
2. **Adapter + generation** — `base.py` Protocols, `nutri_http.py`, `generation.py`, `starters/example.json` with ~10 starters. Test with a fake adapter; one manual smoke run against the live local app.
3. **Batches + review screens** — `app.py`, batch creation with background generation, review screen with keyboard rating. End of this step = **Workflow 1 works end-to-end**.
4. **Judge** — `judge_prompts/v1.md`, `gemini_judge.py`, `judge.py`, the two judge endpoints. End of this step = **Workflows 2 and 3 work**.
5. **Dashboard** — four views, batch/date filters, Chart.js. End of this step = full PRD delivered.
6. **Portability proof + README** — import-isolation test, README with the "port this" recipe.

## Acceptance checklist

- [ ] Generate a batch from a 10-starter file; conversations persist and survive restart
- [ ] Rate a full batch on the review screen using only the keyboard
- [ ] Run an alignment check on that batch; judge never sees human verdicts
- [ ] Dashboard shows the 2×2 alignment grid and disagreement list, filtered by batch and date
- [ ] Run a judge-only eval on a fresh batch
- [ ] Every judge verdict stores reasoning + LLM-as-a-judge prompt version + model name
- [ ] `eval_framework/` has zero imports from `agent/` or `src/` (test-enforced)
- [ ] Tests pass without any real LLM calls
