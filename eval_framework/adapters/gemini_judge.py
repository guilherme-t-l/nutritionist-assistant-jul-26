"""Default JudgeLLM — Gemini via google-genai.

Reuses GEMINI_API_KEY from .env (same key as the host app) but does NOT
import agent/llm.py — portability: the framework stays copy-pasteable.
"""

from __future__ import annotations

import os

from google import genai
from google.genai import types

# Generous on purpose: judge prompts include full meal-plan transcripts, and
# gemini-2.5-flash can think for a while. Typical success is ~5–15s; 180s is
# a safety net so one hung HTTPS call cannot freeze an entire batch.
DEFAULT_TIMEOUT_S = 180.0


class GeminiJudge:
    """JudgeLLM implementation: one prompt in, raw text out."""

    DEFAULT_MODEL = "gemini-2.5-flash"

    def __init__(
        self,
        api_key: str | None = None,
        model: str = DEFAULT_MODEL,
        *,
        timeout_s: float = DEFAULT_TIMEOUT_S,
    ) -> None:
        key = api_key or os.environ.get("GEMINI_API_KEY")
        if not key:
            raise RuntimeError(
                "GEMINI_API_KEY is not set — needed for the LLM-as-a-judge."
            )
        # google-genai HttpOptions.timeout is milliseconds.
        self._client = genai.Client(
            api_key=key,
            http_options=types.HttpOptions(timeout=int(timeout_s * 1000)),
        )
        self._model = model
        self._timeout_s = timeout_s

    @property
    def model_name(self) -> str:
        return self._model

    def complete(self, prompt: str) -> str:
        """Send `prompt` to Gemini; return the model's text reply."""
        try:
            response = self._client.models.generate_content(
                model=self._model,
                contents=prompt,
                config=types.GenerateContentConfig(
                    # Soft constraint: ask for JSON. Hard parse happens in judge.py.
                    response_mime_type="application/json",
                    temperature=0.0,
                ),
            )
        except Exception as exc:
            # Surface the timeout budget in the message so batch error logs
            # are actionable (judge_batch collects these and continues).
            raise RuntimeError(
                f"GeminiJudge call failed (timeout={self._timeout_s:.0f}s): {exc}"
            ) from exc
        text = (response.text or "").strip()
        if not text:
            raise RuntimeError("GeminiJudge returned an empty response")
        return text


__all__ = ["DEFAULT_TIMEOUT_S", "GeminiJudge"]
