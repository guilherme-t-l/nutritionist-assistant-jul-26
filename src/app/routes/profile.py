# PUT /profile — update preferences without touching active_plan.

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from agent.schemas import UserProfile
from agent.session import SessionStore
from agent.users import UserStore
from src.app.dependencies import get_session_store, get_user_store
from src.app.routes.auth import get_optional_username


router = APIRouter()


class ProfileUpdateRequest(UserProfile):
    """UserProfile fields plus optional session_id to sync in-memory session."""

    session_id: str | None = Field(default=None)


class ProfileUpdateResponse(BaseModel):
    profile: UserProfile


@router.put("/profile", response_model=ProfileUpdateResponse)
def update_profile(
    request: Request,
    body: ProfileUpdateRequest,
    user_store: UserStore = Depends(get_user_store),
    store: SessionStore = Depends(get_session_store),
) -> ProfileUpdateResponse:
    username = get_optional_username(request)
    if not username:
        raise HTTPException(status_code=401, detail="Not authenticated")

    user = user_store.get_user(username)
    if user is None:
        raise HTTPException(status_code=401, detail="Not authenticated")

    # Strip session_id so we persist a clean UserProfile.
    profile = UserProfile.model_validate(
        body.model_dump(exclude={"session_id"})
    )
    # Profile column only — must not write active_plan.
    user_store.save_profile(username, profile)

    if body.session_id:
        session = store.get(body.session_id)
        if session is not None:
            session.profile = profile
            store.save(body.session_id, session)

    return ProfileUpdateResponse(profile=profile)
