# Eval Framework

A **standalone, portable** evaluation module for measuring:

1. **The agent** — Pass/Fail rate over batches of conversations (you rate, or an LLM-as-a-judge rates).
2. **The judge** — Alignment rate: does the LLM judge agree with your verdicts?

It lives in this folder and **never imports the host app** (`agent/`, `src/`, …). The only seam is a small **Target Adapter** that runs scripted conversations against whatever system you are evaluating.

Product spec: [`../new_features_history/PRD-evals.md`](../new_features_history/PRD-evals.md).

---

## Run it (this project)

Two processes:

```bash
# Terminal 1 — host nutri-assistant (already have ./local_deployment_script)
./local_deployment_script
# → http://127.0.0.1:8000/

# Terminal 2 — eval UI
uv run python -m uvicorn eval_framework.app:app --reload --port 8100
# → http://127.0.0.1:8100/
```

Needs `GEMINI_API_KEY` in `.env` (judge + host agent). SQLite data lands in `eval_framework/data/evals.db` (gitignored).

| Workflow | What to click |
|---|---|
| Manual eval (you rate the agent) | **New batch → Manual eval** → **Review** → `P` / `F` |
| Alignment check (measure the judge) | Fully human-rate a batch → **Run alignment check** |
| Judge-only eval (scale agent scoring) | **New batch → Judge eval** |
| Dashboard | **Dashboard** — Overview / Agent vs Human / Judge vs Agent / Judge vs Human |

Tests (no real LLM calls):

```bash
uv run pytest eval_framework/tests/ -v
```

---

## Port this to another project

Copy the whole `eval_framework/` folder. Then do three things:

### 1. Write a Target Adapter (~30 lines)

Implement anything with this shape (a [Protocol](adapters/base.py) — no inheritance required):

```python
def run_conversation(self, starter: ConversationStarter) -> Conversation:
    """starter.context + starter.turns → full transcript [{role, content}, ...]"""
```

In this repo the adapter is [`adapters/nutri_http.py`](adapters/nutri_http.py) (`POST /plan`, then `POST /chat`). Point `app.state.agent_factory` at your class (see `app.py` lifespan defaults).

`starter.context` is **opaque** — your adapter decides what it means (profile JSON, ticket id, …).

### 2. Point the judge at your API key / model

Default: [`adapters/gemini_judge.py`](adapters/gemini_judge.py) reads `GEMINI_API_KEY`. Swap by setting `app.state.judge_factory` to any object with `complete(prompt: str) -> str`.

Edit fail/pass rules in [`judge_prompts/v1.md`](judge_prompts/v1.md). Bump the filename to `v2.md` when you change criteria — verdicts store the version for provenance.

### 3. Add starter files

Drop JSON under [`starters/`](starters/):

```json
{
  "name": "my-batch",
  "description": "optional",
  "starters": [
    {
      "id": "case-01",
      "category": "Safety",
      "context": { "...": "whatever your adapter needs" },
      "turns": ["First user message", "Optional follow-up"]
    }
  ]
}
```

That is the whole port. Storage, review UI, judge loop, and dashboard stay unchanged.

---

## Layout

```
eval_framework/
  app.py                 FastAPI UI + API (:8100)
  db.py                  SQLite: batches / conversations / verdicts
  generation.py          starters → conversations via TargetAgent
  judge.py               LLM-as-a-judge (blind to human verdicts)
  metrics.py             pass rate / alignment (pure functions)
  dashboard.py           aggregates for charts
  adapters/
    base.py              TargetAgent + JudgeLLM Protocols
    nutri_http.py        THIS project's adapter
    gemini_judge.py      default judge LLM
  judge_prompts/v1.md
  starters/example.json
  templates/             Jinja2 (batches, review, dashboard)
  data/evals.db          local artefact (gitignored)
  tests/                 including import-isolation (test_portability.py)
```

---

## Design rules (do not break)

- **No host imports** — enforced by `tests/test_portability.py`.
- **Blind judge** — prompts are built from context + transcript only; human verdicts never go in.
- **Latest verdict wins** — re-rating appends a row; reads take the newest per rater.
- **Binary Pass/Fail only** (MVP) — no 1–5 scores in this module.
- **Chart.js via CDN** — no frontend build step, so the folder stays copy-pasteable.
