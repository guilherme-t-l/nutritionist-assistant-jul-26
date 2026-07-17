# Normalize a pasted / uploaded meal plan into a MealPlan.
#
# Modes:
#   as_is  — keep the plan as written. Valid MealPlan JSON skips the LLM;
#            freeform only gets structured into the schema (no preference edits).
#   adapt  — one LLM call that rewrites the plan to match the user's profile.
#
# PDF extraction is separate: bytes → text, then the same normalize path.

from __future__ import annotations

from io import BytesIO
from typing import Literal

from fastapi import HTTPException
from pydantic import ValidationError
from pypdf import PdfReader

from agent.llm import LLM, Message
from agent.prompts import (
    build_import_adapt_user_message,
    build_import_user_message,
    build_system_prompt,
)
from agent.schemas import MealPlan, UserProfile

ImportMode = Literal["as_is", "adapt"]

# Below this many non-whitespace chars we treat a PDF as unreadable
# (scanned / image-only / empty). OCR is out of scope.
_MIN_PDF_TEXT_CHARS = 40

_AS_IS_SYSTEM_PROMPT = (
    "You convert meal plans into a structured MealPlan JSON schema. "
    "Preserve the user's meals, ingredients, portions, and macros as faithfully "
    "as possible. Do NOT rewrite the plan to hit calorie or macro targets, "
    "change cuisines, or invent new meals. Only structure what they gave you. "
    "Estimate missing macros conservatively when a food is listed without numbers."
)

_ADAPT_SYSTEM_SUFFIX = (
    "\n\nThe user is importing an existing meal plan and wants you to EDIT it "
    "to match their preferences above (calorie/macro targets, meals per day, "
    "cuisines, flavors, allergies, dislikes). Start from their plan — keep what "
    "already fits, change what doesn't. Explain key changes briefly in `notes`."
)


def extract_pdf_text(data: bytes) -> str:
    """Extract plain text from a PDF. Raises HTTPException if unreadable."""
    try:
        reader = PdfReader(BytesIO(data))
    except Exception as exc:
        raise HTTPException(
            status_code=422,
            detail="Couldn't read this PDF. Try a text-based PDF, or paste the plan as text.",
        ) from exc

    parts: list[str] = []
    for page in reader.pages:
        try:
            text = page.extract_text() or ""
        except Exception:
            text = ""
        if text.strip():
            parts.append(text)

    joined = "\n".join(parts).strip()
    if len(joined) < _MIN_PDF_TEXT_CHARS:
        raise HTTPException(
            status_code=422,
            detail=(
                "Couldn't read text from this PDF. "
                "Scanned or image-only PDFs aren't supported — paste the plan as text instead."
            ),
        )
    return joined


def try_parse_meal_plan(source_text: str) -> MealPlan | None:
    """Return a MealPlan if source_text is valid MealPlan JSON, else None."""
    text = source_text.strip()
    if not text:
        return None
    try:
        return MealPlan.model_validate_json(text)
    except ValidationError:
        return None


def normalize_meal_plan(
    source_text: str,
    profile: UserProfile,
    llm: LLM,
    *,
    mode: ImportMode = "as_is",
) -> MealPlan:
    """Turn freeform / JSON source text into a validated MealPlan.

    as_is: JSON shortcut when possible; else structure-only LLM call.
    adapt: always one LLM call that edits the plan to match the profile.
    """
    text = source_text.strip()
    if not text:
        raise HTTPException(
            status_code=422,
            detail="No plan text provided. Paste your plan or upload a file.",
        )

    if mode == "as_is":
        direct = try_parse_meal_plan(text)
        if direct is not None:
            return direct
        system_prompt = _AS_IS_SYSTEM_PROMPT
        user_message = Message(role="user", content=build_import_user_message(text))
    else:
        system_prompt = build_system_prompt(profile) + _ADAPT_SYSTEM_SUFFIX
        user_message = Message(
            role="user", content=build_import_adapt_user_message(text)
        )

    raw_reply = llm.chat(
        messages=[user_message],
        system=system_prompt,
        response_schema=MealPlan,
    )

    try:
        return MealPlan.model_validate_json(raw_reply)
    except ValidationError as exc:
        raise HTTPException(
            status_code=502,
            detail=f"Could not normalize the imported plan into a MealPlan: {exc}",
        ) from exc
