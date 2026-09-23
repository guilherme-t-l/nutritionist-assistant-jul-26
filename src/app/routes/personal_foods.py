# Personal food library — list, add, edit, delete.
#
# Logged-in only, same cookie as PUT /profile. Guests get 401.
# Each write replaces personal_foods_json and nothing else.

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, Request, Response

from agent.schemas import PersonalFood, PersonalFoodDraft
from agent.users import UserStore
from src.app.dependencies import get_user_store
from src.app.routes.auth import get_optional_username


router = APIRouter()


def _require_username(request: Request, user_store: UserStore) -> str:
    username = get_optional_username(request)
    if not username or user_store.get_user(username) is None:
        raise HTTPException(status_code=401, detail="Not authenticated")
    return username


def _find(foods: list[PersonalFood], food_id: str) -> int:
    for index, food in enumerate(foods):
        if food.id == food_id:
            return index
    raise HTTPException(status_code=404, detail="Personal food not found")


@router.get("/personal-foods", response_model=list[PersonalFood])
def list_personal_foods(
    request: Request,
    user_store: UserStore = Depends(get_user_store),
) -> list[PersonalFood]:
    username = _require_username(request, user_store)
    return user_store.get_personal_foods(username)


@router.post("/personal-foods", response_model=PersonalFood, status_code=201)
def create_personal_food(
    body: PersonalFoodDraft,
    request: Request,
    user_store: UserStore = Depends(get_user_store),
) -> PersonalFood:
    username = _require_username(request, user_store)
    foods = user_store.get_personal_foods(username)
    # Server assigns the id. The client cannot choose or overwrite one.
    created = PersonalFood(id=uuid.uuid4().hex, **body.model_dump())
    foods.append(created)
    user_store.save_personal_foods(username, foods)
    return created


@router.put("/personal-foods/{food_id}", response_model=PersonalFood)
def update_personal_food(
    food_id: str,
    body: PersonalFoodDraft,
    request: Request,
    user_store: UserStore = Depends(get_user_store),
) -> PersonalFood:
    username = _require_username(request, user_store)
    foods = user_store.get_personal_foods(username)
    index = _find(foods, food_id)
    updated = PersonalFood(id=food_id, **body.model_dump())
    foods[index] = updated
    user_store.save_personal_foods(username, foods)
    return updated


@router.delete("/personal-foods/{food_id}", status_code=204)
def delete_personal_food(
    food_id: str,
    request: Request,
    user_store: UserStore = Depends(get_user_store),
) -> Response:
    username = _require_username(request, user_store)
    foods = user_store.get_personal_foods(username)
    index = _find(foods, food_id)
    del foods[index]
    user_store.save_personal_foods(username, foods)
    return Response(status_code=204)
