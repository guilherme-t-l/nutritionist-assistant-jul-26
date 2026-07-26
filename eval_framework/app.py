"""Eval Framework FastAPI app — separate process from the nutri-assistant.

Run (with the host app already on :8000):
    uv run python -m uvicorn eval_framework.app:app --reload --port 8100

Then open http://127.0.0.1:8100/

This app owns the review UI and SQLite store. It talks to the host agent
only through NutriHttpAgent (HTTP). Zero imports from agent/ or src/.
"""

from __future__ import annotations

import json
import logging
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, AsyncIterator, Callable, Literal

from dotenv import load_dotenv
from fastapi import BackgroundTasks, FastAPI, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, Field

from eval_framework import db
from eval_framework.adapters.base import JudgeLLM, TargetAgent
from eval_framework.adapters.gemini_judge import GeminiJudge
from eval_framework.adapters.nutri_http import DEFAULT_BASE_URL, NutriHttpAgent
from eval_framework.generation import (
    STARTERS_DIR,
    list_starter_files,
    load_starter_file,
    populate_batch,
)
from eval_framework.dashboard import build_dashboard_data
from eval_framework.judge import judge_batch

load_dotenv()

logger = logging.getLogger("eval_framework")

_TEMPLATES_DIR = Path(__file__).resolve().parent / "templates"
_STATIC_DIR = Path(__file__).resolve().parent / "static"
templates = Jinja2Templates(directory=_TEMPLATES_DIR)

# Swappable in tests: app.state.agent_factory / judge_factory.
# `state` is FastAPI's per-app bag for configuration that isn't settings.
AgentFactory = Callable[[], TargetAgent]
JudgeFactory = Callable[[], JudgeLLM]


def _default_agent_factory() -> TargetAgent:
    return NutriHttpAgent(base_url=DEFAULT_BASE_URL)


def _default_judge_factory() -> JudgeLLM:
    return GeminiJudge()


@asynccontextmanager
async def _lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Create the SQLite file/tables and default app.state on boot."""
    if not hasattr(app.state, "db_path"):
        app.state.db_path = db.DEFAULT_DB_PATH
    if not hasattr(app.state, "agent_factory"):
        app.state.agent_factory = _default_agent_factory
    if not hasattr(app.state, "judge_factory"):
        app.state.judge_factory = _default_judge_factory
    if not hasattr(app.state, "starters_dir"):
        app.state.starters_dir = STARTERS_DIR
    db.init_db(app.state.db_path)
    yield


app = FastAPI(title="Eval Framework", version="0.1.0", lifespan=_lifespan)
app.mount("/static", StaticFiles(directory=_STATIC_DIR), name="static")


def _db_path() -> Path:
    return Path(app.state.db_path)


def _agent_factory() -> AgentFactory:
    return app.state.agent_factory


def _judge_factory() -> JudgeFactory:
    return app.state.judge_factory


# ---------- helpers ------------------------------------------------------------


def _batch_summary(batch_id: str) -> dict[str, Any]:
    """Counts the Batches table needs: convs + human/judge rated."""
    convs = db.list_conversations(batch_id, db_path=_db_path())
    human = db.latest_verdicts_for_batch(
        batch_id, rater_type="human", db_path=_db_path()
    )
    judge = db.latest_verdicts_for_batch(
        batch_id, rater_type="judge", db_path=_db_path()
    )
    total = len(convs)
    human_rated = len(human)
    judge_rated = len(judge)
    # Alignment check is available when you fully rated the batch and the
    # judge has not yet spoken on those conversations.
    can_align = (
        total > 0
        and human_rated == total
        and judge_rated == 0
    )
    return {
        "total": total,
        "human_rated": human_rated,
        "judge_rated": judge_rated,
        "can_align": can_align,
    }


def _format_list(values: Any) -> str:
    """Join a list field for the context chip, or 'none' if empty."""
    if not values:
        return "none"
    return ", ".join(str(v) for v in values)


def _format_context(context: dict[str, Any]) -> list[str]:
    """Multi-line labels for the review context chip.

    One field per line (macros share the calorie line) so reviewers can
    scan the full profile without reading a long run-on sentence.
    """
    lines: list[str] = []
    if goal := context.get("goal"):
        lines.append(f"goal: {goal}")
    if "allergies" in context:
        lines.append(f"allergies: {_format_list(context.get('allergies'))}")
    if "disliked_ingredients" in context:
        lines.append(f"dislikes: {_format_list(context.get('disliked_ingredients'))}")

    targets: list[str] = []
    if cal := context.get("calorie_target"):
        targets.append(f"{cal} kcal")
    if (protein := context.get("protein_g_target")) is not None:
        targets.append(f"P{protein}g")
    if (carbs := context.get("carbs_g_target")) is not None:
        targets.append(f"C{carbs}g")
    if (fat := context.get("fat_g_target")) is not None:
        targets.append(f"F{fat}g")
    if targets:
        lines.append("targets: " + " · ".join(targets))

    if "cuisine_preferences" in context:
        lines.append(f"cuisine: {_format_list(context.get('cuisine_preferences'))}")
    if "flavor_profiles" in context:
        lines.append(f"flavors: {_format_list(context.get('flavor_profiles'))}")
    if (meals := context.get("meals_per_day")) is not None:
        lines.append(f"meals: {meals}/day")

    if not lines:
        return [json.dumps(context, ensure_ascii=False)]
    return lines


# ---------- request bodies -----------------------------------------------------


class CreateBatchBody(BaseModel):
    starter_file: str = Field(..., description="Filename under starters/, e.g. example.json")
    mode: Literal["manual", "judge"] = "manual"


class VerdictBody(BaseModel):
    rater_type: Literal["human"] = "human"
    verdict: Literal["pass", "fail"]
    note: str | None = None


# ---------- screens ------------------------------------------------------------


@app.get("/", response_class=HTMLResponse)
def batches_home(request: Request) -> HTMLResponse:
    batches = db.list_batches(db_path=_db_path())
    rows = []
    for batch in batches:
        summary = _batch_summary(batch.batch_id)
        rows.append({"batch": batch, **summary})
    starter_files = [p.name for p in list_starter_files(app.state.starters_dir)]
    return templates.TemplateResponse(
        request,
        "batches.html",
        {
            "rows": rows,
            "starter_files": starter_files,
        },
    )


@app.get("/batches/{batch_id}/review", response_class=HTMLResponse)
def review_screen(
    request: Request,
    batch_id: str,
    i: int = Query(0, ge=0),
) -> HTMLResponse:
    batch = db.get_batch(batch_id, db_path=_db_path())
    if batch is None:
        raise HTTPException(status_code=404, detail="Unknown batch")
    if batch.status == "generating":
        return RedirectResponse(url="/", status_code=303)
    if batch.status == "error":
        raise HTTPException(status_code=409, detail="Batch failed during generation")

    conversations = db.list_conversations(batch_id, db_path=_db_path())
    if not conversations:
        raise HTTPException(status_code=404, detail="Batch has no conversations")

    index = min(i, len(conversations) - 1)
    conv = conversations[index]
    human = db.latest_verdicts_for_batch(
        batch_id, rater_type="human", db_path=_db_path()
    ).get(conv.conversation_id)
    judge = db.latest_verdicts_for_batch(
        batch_id, rater_type="judge", db_path=_db_path()
    ).get(conv.conversation_id)

    next_index = index + 1 if index < len(conversations) - 1 else None
    return templates.TemplateResponse(
        request,
        "review.html",
        {
            "batch": batch,
            "conversation": conv,
            # Client renders chat + plan like the product UI (not raw JSON).
            "transcript": conv.transcript,
            "context": conv.context,
            "context_lines": _format_context(conv.context),
            "index": index,
            "total": len(conversations),
            "human_verdict": human,
            "judge_verdict": judge,
            "prev_index": index - 1 if index > 0 else None,
            "next_index": next_index,
            "next_url": (
                f"/batches/{batch_id}/review?i={next_index}"
                if next_index is not None
                else None
            ),
        },
    )


@app.get("/dashboard", response_class=HTMLResponse)
def dashboard_screen(
    request: Request,
    view: str = Query("overview"),
    batch: str | None = Query(None),
    date_from: str | None = Query(None, alias="from"),
    date_to: str | None = Query(None, alias="to"),
) -> HTMLResponse:
    allowed = {"overview", "agent_vs_human", "judge_vs_agent", "judge_vs_human"}
    if view not in allowed:
        view = "overview"
    batch_id = None if not batch or batch == "all" else batch
    batches = [
        {"batch_id": b.batch_id, "name": b.name, "created_at": b.created_at}
        for b in db.list_batches(db_path=_db_path())
    ]
    return templates.TemplateResponse(
        request,
        "dashboard.html",
        {
            "view": view,
            "batch_id": batch_id,
            "date_from": date_from,
            "date_to": date_to,
            "batches": batches,
        },
    )


@app.get("/api/dashboard-data")
def api_dashboard_data(
    batch: str | None = Query(None),
    date_from: str | None = Query(None, alias="from"),
    date_to: str | None = Query(None, alias="to"),
) -> dict[str, Any]:
    batch_id = None if not batch or batch == "all" else batch
    if batch_id and db.get_batch(batch_id, db_path=_db_path()) is None:
        raise HTTPException(status_code=404, detail="Unknown batch")
    return build_dashboard_data(
        batch_id=batch_id,
        date_from=date_from,
        date_to=date_to,
        db_path=_db_path(),
    )


# ---------- API ----------------------------------------------------------------


@app.post("/batches")
def create_batch(
    body: CreateBatchBody,
    background_tasks: BackgroundTasks,
) -> dict[str, Any]:
    """Kick off batch generation in the background; return batch_id immediately."""
    starter_path = Path(app.state.starters_dir) / body.starter_file
    if not starter_path.is_file():
        raise HTTPException(
            status_code=400,
            detail=f"Unknown starter file: {body.starter_file}",
        )
    try:
        loaded = load_starter_file(starter_path)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    batch = db.create_batch(
        name=loaded.name,
        starter_file=starter_path.name,
        status="generating",
        db_path=_db_path(),
    )

    # Capture primitives (not request-scoped objects) for the background task.
    batch_id = batch.batch_id
    mode = body.mode
    db_path = str(_db_path())
    agent_factory = _agent_factory()
    judge_factory = _judge_factory()

    def _run() -> None:
        agent = agent_factory()
        try:
            populate_batch(
                batch_id,
                agent,
                starter_path,
                mode=mode,
                db_path=db_path,
            )
            # Workflow 3: judge-only eval auto-rates after generation.
            if mode == "judge":
                judge_batch(batch_id, judge_factory(), db_path=db_path)
        except Exception:
            logger.exception("Batch %s generation failed", batch_id)
        finally:
            close = getattr(agent, "close", None)
            if callable(close):
                close()

    background_tasks.add_task(_run)
    return {
        "batch_id": batch.batch_id,
        "status": batch.status,
        "expected_total": len(loaded.starters),
        "mode": mode,
    }


@app.post("/batches/{batch_id}/judge")
def run_judge(
    batch_id: str,
    background_tasks: BackgroundTasks,
) -> dict[str, Any]:
    """Workflow 2 (alignment) or re-judge: rate conversations with the LLM judge.

    Runs in a background task. Poll GET /api/batches/{id} until
    judge_rated == total. The judge never sees human verdicts.
    """
    batch = db.get_batch(batch_id, db_path=_db_path())
    if batch is None:
        raise HTTPException(status_code=404, detail="Unknown batch")
    if batch.status != "ready":
        raise HTTPException(
            status_code=409,
            detail=f"Batch must be ready before judging (status={batch.status})",
        )
    conversations = db.list_conversations(batch_id, db_path=_db_path())
    if not conversations:
        raise HTTPException(status_code=400, detail="Batch has no conversations")

    db_path = str(_db_path())
    judge_factory = _judge_factory()

    def _run() -> None:
        try:
            result = judge_batch(batch_id, judge_factory(), db_path=db_path)
            if result.errors:
                logger.warning(
                    "Judge batch %s finished with %d errors: %s",
                    batch_id,
                    len(result.errors),
                    result.errors[:3],
                )
        except Exception:
            logger.exception("Judge batch %s failed", batch_id)

    background_tasks.add_task(_run)
    return {
        "batch_id": batch_id,
        "status": "judging",
        "total": len(conversations),
    }


@app.get("/api/batches")
def api_list_batches() -> list[dict[str, Any]]:
    out = []
    for batch in db.list_batches(db_path=_db_path()):
        summary = _batch_summary(batch.batch_id)
        out.append(
            {
                "batch_id": batch.batch_id,
                "name": batch.name,
                "starter_file": batch.starter_file,
                "created_at": batch.created_at,
                "status": batch.status,
                **summary,
            }
        )
    return out


@app.get("/api/batches/{batch_id}")
def api_batch_status(batch_id: str) -> dict[str, Any]:
    batch = db.get_batch(batch_id, db_path=_db_path())
    if batch is None:
        raise HTTPException(status_code=404, detail="Unknown batch")
    summary = _batch_summary(batch_id)
    return {
        "batch_id": batch.batch_id,
        "name": batch.name,
        "starter_file": batch.starter_file,
        "created_at": batch.created_at,
        "status": batch.status,
        **summary,
    }


@app.post("/conversations/{conversation_id}/verdict")
def post_verdict(conversation_id: str, body: VerdictBody) -> dict[str, Any]:
    conv = db.get_conversation(conversation_id, db_path=_db_path())
    if conv is None:
        raise HTTPException(status_code=404, detail="Unknown conversation")
    verdict = db.insert_verdict(
        conversation_id=conversation_id,
        rater_type=body.rater_type,
        verdict=body.verdict,
        reasoning=body.note,
        db_path=_db_path(),
    )
    return {
        "verdict_id": verdict.verdict_id,
        "conversation_id": conversation_id,
        "verdict": verdict.verdict,
        "batch_id": conv.batch_id,
    }


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}
