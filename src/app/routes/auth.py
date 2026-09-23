# Auth routes: login / logout / me.
#
# Cookie value is the username (MVP). No JWT. Login does not mutate profile
# or active_plan — those only change after successful /plan, /plan/save, or PUT /profile.
#
# max_age makes this a persistent cookie. Without it the browser treats the
# cookie as a session cookie and drops it when the app/tab is closed — which
# on a phone feels like being logged out. 15 days in seconds.

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, Field

from agent.schemas import MealPlan, PersonalFood, UserProfile
from agent.session import SessionStore
from agent.users import UserStore
from src.app.dependencies import get_session_store, get_user_store


router = APIRouter()

COOKIE_NAME = "nutri_user"
# 15 days. Browsers store this until it expires, or until logout deletes it.
AUTH_COOKIE_MAX_AGE_SECONDS = 15 * 24 * 60 * 60


class LoginRequest(BaseModel):
    username: str = Field(min_length=1)
    password: str = Field(min_length=1)


class LoginResponse(BaseModel):
    username: str
    has_plan: bool


class MeResponse(BaseModel):
    username: str
    has_plan: bool
    profile: UserProfile | None = None
    plan: MealPlan | None = None


def get_optional_username(request: Request) -> str | None:
    """Read the auth cookie if present; does not validate against the DB."""
    return request.cookies.get(COOKIE_NAME)


def personal_foods_for_request(request: Request, user_store: UserStore) -> list[PersonalFood]:
    """Library for this cookie, read now. Guests and unknown users get [].

    Called on each /chat (and import-adapt) so a food saved a moment ago is
    what the agent sees — not a copy frozen on the session.
    """
    username = get_optional_username(request)
    if not username:
        return []
    return user_store.get_personal_foods(username)


def _set_auth_cookie(response: Response, username: str) -> None:
    response.set_cookie(
        key=COOKIE_NAME,
        value=username,
        httponly=True,
        samesite="lax",
        path="/",
        max_age=AUTH_COOKIE_MAX_AGE_SECONDS,
    )


def _clear_auth_cookie(response: Response) -> None:
    response.delete_cookie(key=COOKIE_NAME, path="/")


@router.post("/login", response_model=LoginResponse)
def login(
    body: LoginRequest,
    response: Response,
    user_store: UserStore = Depends(get_user_store),
) -> LoginResponse:
    if not user_store.verify_credentials(body.username, body.password):
        raise HTTPException(
            status_code=401,
            detail="Invalid username or password",
        )
    user = user_store.get_user(body.username)
    # Should always exist after a successful verify, but be defensive.
    if user is None:
        raise HTTPException(status_code=401, detail="Invalid username or password")

    _set_auth_cookie(response, body.username)
    return LoginResponse(
        username=body.username,
        has_plan=user.active_plan is not None,
    )


@router.post("/logout")
def logout(response: Response) -> dict[str, bool]:
    _clear_auth_cookie(response)
    return {"ok": True}


@router.get("/auth/me", response_model=MeResponse)
def auth_me(
    request: Request,
    user_store: UserStore = Depends(get_user_store),
) -> MeResponse:
    username = get_optional_username(request)
    if not username:
        raise HTTPException(status_code=401, detail="Not authenticated")

    user = user_store.get_user(username)
    if user is None:
        raise HTTPException(status_code=401, detail="Not authenticated")

    return MeResponse(
        username=user.username,
        has_plan=user.active_plan is not None,
        profile=user.profile,
        plan=user.active_plan,
    )


class ResumeResponse(BaseModel):
    session_id: str
    profile: UserProfile
    plan: MealPlan


@router.post("/session/resume", response_model=ResumeResponse)
def resume_session(
    request: Request,
    user_store: UserStore = Depends(get_user_store),
    store: SessionStore = Depends(get_session_store),
) -> ResumeResponse:
    """Seed a new conversation session from DB. Read-only on the users table."""
    username = get_optional_username(request)
    if not username:
        raise HTTPException(status_code=401, detail="Not authenticated")

    user = user_store.get_user(username)
    if user is None:
        raise HTTPException(status_code=401, detail="Not authenticated")
    if user.profile is None or user.active_plan is None:
        raise HTTPException(
            status_code=400,
            detail="No stored plan to resume. Generate a plan first.",
        )

    # Memory only — must not call any UserStore save method.
    session_id, _session = store.create(
        profile=user.profile,
        current_plan=user.active_plan,
    )
    return ResumeResponse(
        session_id=session_id,
        profile=user.profile,
        plan=user.active_plan,
    )
