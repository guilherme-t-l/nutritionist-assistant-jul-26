# POST /plan — one-shot meal plan generation.
#
# This is the very first request any user makes: they fill in the onboarding
# form, submit it, and this handler returns the initial meal plan plus a
# `session_id` they'll reuse in /chat for follow-up refinements.
#
# Keep this file thin: no calorie math, no prompt strings, no LLM details.
# All of that lives in the agent/ package.

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, ValidationError

from agent.llm import LLM, Message
from agent.prompts import (
    build_assistant_note,
    build_initial_user_message,
    build_system_prompt,
)
from agent.schemas import MealPlan, UserProfile
from agent.session import SessionStore
from agent.users import UserStore
from src.app.dependencies import get_llm, get_session_store, get_user_store
from src.app.routes.auth import get_optional_username


# An `APIRouter` is a mini-FastAPI-app — you attach routes here, and
# `app.include_router(router)` in main.py merges them into the real app.
router = APIRouter()


# The JSON shape /plan returns: the session id the frontend must remember
# for future /chat calls, plus the initial meal plan.
class PlanResponse(BaseModel):
    session_id: str
    plan: MealPlan


# Handler for POST /plan. Called by FastAPI when the frontend submits the
# onboarding form (the first request any new user makes).
#
# Flow, in order:
#   1. FastAPI parses the request body into a UserProfile (Pydantic validates
#      types and bounds — invalid inputs get a 422 before we ever run).
#   2. FastAPI injects `llm` and `store` via Depends(...). See dependencies.py.
#   3. We create a new session in the store -> gives us a fresh session_id.
#   4. We build the system prompt (persona + profile, no plan yet) + the
#      short first user task message.
#   5. We call llm.chat(...) — the ONLY outbound network call in this flow.
#   6. We re-validate the LLM's JSON reply into a MealPlan; 502 if malformed.
#   7. We store the plan on the session and append user turn + short assistant
#      note to history (not the full MealPlan JSON — that lives in current_plan).
#   8. We return { session_id, plan } as JSON.
#
# `response_model=PlanResponse` makes FastAPI validate + serialize the returned
# value against this schema; extra fields are stripped, missing ones are a 500.
@router.post("/plan", response_model=PlanResponse)
def create_plan(
    # `profile: UserProfile` tells FastAPI "parse the JSON request body into a
    # UserProfile". Validation happens for free (422 on bad input).
    profile: UserProfile,
    request: Request,
    # `Depends(get_llm)` is FastAPI's dependency injection: before calling
    # `create_plan`, FastAPI calls `get_llm()` and passes the result in.
    # Tests swap `get_llm` for a fake via `app.dependency_overrides`.
    llm: LLM = Depends(get_llm),
    store: SessionStore = Depends(get_session_store),
    user_store: UserStore = Depends(get_user_store),
) -> PlanResponse:
    # Tuple unpacking: `store.create(profile)` returns `(id, session)` —
    # the two names on the left get bound to the two elements on the right.
    session_id, session = store.create(profile)

    # First call: no current_plan yet — system is persona + profile only.
    system_prompt = build_system_prompt(profile)
    first_user_message = Message(role="user", content=build_initial_user_message())

    raw_reply = llm.chat(
        messages=[first_user_message],
        system=system_prompt,
        response_schema=MealPlan,
    )

    try:
        # Pydantic v2 parses a JSON string straight into a typed object.
        # Shortcut for `json.loads()` + `MealPlan(**data)` with validation.
        plan = MealPlan.model_validate_json(raw_reply)
    except ValidationError as exc:
        # 502 = Bad Gateway — the correct code when an UPSTREAM service
        # (Gemini) misbehaved. NOT 400 (would blame the user) or 500
        # (would blame ourselves).
        # `raise ... from exc` preserves the original traceback, so logs show
        # "during handling of ValidationError, HTTPException raised".
        raise HTTPException(
            status_code=502,
            detail=f"LLM returned an invalid MealPlan: {exc}",
        ) from exc

    # Keep the full plan on the session, so it can be used in the next turn's system prompt (/chat will inject it into the system prompt on the next turn)
    session.current_plan = plan
    # History stays cheap: the user task + a short note (not raw_reply JSON with all the meal plan history details).
    session.history.append(first_user_message)
    session.history.append(Message(role="model", content=build_assistant_note(plan)))

    # Logged-in only: persist after a successful plan is ready for the UI.
    # Guests (no cookie) leave users.db untouched. Failures above never reach here.
    username = get_optional_username(request)
    if username:
        user_store.save_profile_and_plan(username, profile, plan)

    return PlanResponse(session_id=session_id, plan=plan)


class SavePlanRequest(BaseModel):
    session_id: str


class SavePlanResponse(BaseModel):
    ok: bool = True


# Explicit persist for logged-in users. /chat only updates the in-memory
# session; this endpoint writes that working plan to active_plan.
@router.post("/plan/save", response_model=SavePlanResponse)
def save_plan(
    body: SavePlanRequest,
    request: Request,
    store: SessionStore = Depends(get_session_store),
    user_store: UserStore = Depends(get_user_store),
) -> SavePlanResponse:
    username = get_optional_username(request)
    if not username:
        raise HTTPException(status_code=401, detail="Not authenticated")

    user = user_store.get_user(username)
    if user is None:
        raise HTTPException(status_code=401, detail="Not authenticated")

    session = store.get(body.session_id)
    if session is None:
        raise HTTPException(
            status_code=404,
            detail="Unknown session_id. Call /plan first.",
        )
    if session.current_plan is None:
        raise HTTPException(status_code=400, detail="No plan in session to save.")

    user_store.save_plan(username, session.current_plan)
    return SavePlanResponse(ok=True)
