"""Default JudgeLLM — Gemini via google-genai.

Reuses GEMINI_API_KEY from .env (same key as the host app) but does NOT
import agent/llm.py — portability: the framework stays copy-pasteable.
"""

from __future__ import annotations

import os

from google import genai
from google.genai import types


class GeminiJudge:
    """JudgeLLM implementation: one prompt in, raw text out."""

    DEFAULT_MODEL = "gemini-2.5-flash"

    def __init__(self, api_key: str | None = None, model: str = DEFAULT_MODEL) -> None:
        key = api_key or os.environ.get("GEMINI_API_KEY")
        if not key:
            raise RuntimeError(
                "GEMINI_API_KEY is not set — needed for the LLM-as-a-judge."
            )
        self._client = genai.Client(api_key=key)
        self._model = model

    @property
    def model_name(self) -> str:
        return self._model

    def complete(self, prompt: str) -> str:
        """Send `prompt` to Gemini; return the model's text reply."""
        response = self._client.models.generate_content(
            model=self._model,
            contents=prompt,
            config=types.GenerateContentConfig(
                # Soft constraint: ask for JSON. Hard parse happens in judge.py.
                response_mime_type="application/json",
                temperature=0.0,
            ),
        )
        text = (response.text or "").strip()
        if not text:
            raise RuntimeError("GeminiJudge returned an empty response")
        return text


__all__ = ["GeminiJudge"]
