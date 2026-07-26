"""Target adapter for the nutri-assistant host app.

Talks to the live FastAPI service over HTTP — zero imports from `agent/`
or `src/`. That is the whole portability bet: to evaluate a different
product, write a different ~30-line adapter and leave the rest alone.

Flow for one starter:
  1. POST /plan  with `starter.context` as the profile body
     (records turns[0] as the user message in the transcript)
  2. POST /chat  for each remaining turn, with the session_id from /plan

The assistant side of the transcript stores the returned plan JSON
(not the short history note the host app keeps internally), so a human
or judge can see allergens, calories, and meal structure.
"""

from __future__ import annotations

import json
import time
from typing import Any

import httpx

from eval_framework.adapters.base import Conversation, ConversationStarter

# Meal-plan generation can take tens of seconds on a cold Gemini call.
DEFAULT_TIMEOUT_S = 90.0
DEFAULT_BASE_URL = "http://127.0.0.1:8000"


class NutriHttpAgent:
    """TargetAgent implementation that drives the nutri-assistant over HTTP."""

    def __init__(
        self,
        base_url: str = DEFAULT_BASE_URL,
        *,
        timeout_s: float = DEFAULT_TIMEOUT_S,
        client: httpx.Client | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        # If the caller passes a client (tests do), we don't own its lifecycle.
        self._owns_client = client is None
        self._client = client or httpx.Client(
            base_url=self.base_url,
            timeout=timeout_s,
        )

    def close(self) -> None:
        if self._owns_client:
            self._client.close()

    def __enter__(self) -> NutriHttpAgent:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def run_conversation(self, starter: ConversationStarter) -> Conversation:
        if not starter.turns:
            raise ValueError(
                f"Starter {starter.id!r} has no turns — need at least one user message."
            )

        transcript: list[dict[str, str]] = []
        meta: dict[str, Any] = {
            "base_url": self.base_url,
            "starter_id": starter.id,
            "latencies_ms": [],
            "errors": [],
            "session_id": None,
        }

        # --- turn 0: create the plan -----------------------------------------
        first_user = starter.turns[0]
        transcript.append({"role": "user", "content": first_user})
        plan_body, session_id = self._post_plan(starter.context, meta)
        if plan_body is None:
            # /plan failed — record the error as the assistant turn so the
            # review screen still has something to show, then stop.
            transcript.append(
                {
                    "role": "assistant",
                    "content": _error_content(meta["errors"][-1]),
                }
            )
            return Conversation(transcript=transcript, agent_meta=meta)

        meta["session_id"] = session_id
        transcript.append(
            {"role": "assistant", "content": json.dumps(plan_body, ensure_ascii=False)}
        )

        # --- remaining turns: refine via /chat -------------------------------
        for user_text in starter.turns[1:]:
            transcript.append({"role": "user", "content": user_text})
            plan_body = self._post_chat(session_id, user_text, meta)
            if plan_body is None:
                transcript.append(
                    {
                        "role": "assistant",
                        "content": _error_content(meta["errors"][-1]),
                    }
                )
                break
            transcript.append(
                {
                    "role": "assistant",
                    "content": json.dumps(plan_body, ensure_ascii=False),
                }
            )

        return Conversation(transcript=transcript, agent_meta=meta)

    def _post_plan(
        self, context: dict[str, Any], meta: dict[str, Any]
    ) -> tuple[dict[str, Any] | None, str | None]:
        started = time.perf_counter()
        try:
            response = self._client.post("/plan", json=context)
            latency_ms = int((time.perf_counter() - started) * 1000)
            meta["latencies_ms"].append({"step": "plan", "ms": latency_ms})
            if response.status_code >= 400:
                meta["errors"].append(
                    {
                        "step": "plan",
                        "status_code": response.status_code,
                        "detail": _safe_detail(response),
                    }
                )
                return None, None
            data = response.json()
            return data["plan"], data["session_id"]
        except (httpx.HTTPError, KeyError, ValueError) as exc:
            latency_ms = int((time.perf_counter() - started) * 1000)
            meta["latencies_ms"].append({"step": "plan", "ms": latency_ms})
            meta["errors"].append({"step": "plan", "detail": str(exc)})
            return None, None

    def _post_chat(
        self, session_id: str, message: str, meta: dict[str, Any]
    ) -> dict[str, Any] | None:
        started = time.perf_counter()
        try:
            response = self._client.post(
                "/chat",
                json={"session_id": session_id, "message": message},
            )
            latency_ms = int((time.perf_counter() - started) * 1000)
            meta["latencies_ms"].append({"step": "chat", "ms": latency_ms})
            if response.status_code >= 400:
                meta["errors"].append(
                    {
                        "step": "chat",
                        "status_code": response.status_code,
                        "detail": _safe_detail(response),
                    }
                )
                return None
            return response.json()["plan"]
        except (httpx.HTTPError, KeyError, ValueError) as exc:
            latency_ms = int((time.perf_counter() - started) * 1000)
            meta["latencies_ms"].append({"step": "chat", "ms": latency_ms})
            meta["errors"].append({"step": "chat", "detail": str(exc)})
            return None


def _safe_detail(response: httpx.Response) -> str:
    """Best-effort error body for agent_meta — never raises."""
    try:
        payload = response.json()
        detail = payload.get("detail", payload)
        return json.dumps(detail, ensure_ascii=False) if not isinstance(detail, str) else detail
    except (ValueError, json.JSONDecodeError):
        return response.text[:500]


def _error_content(error: dict[str, Any]) -> str:
    return json.dumps({"error": error}, ensure_ascii=False)


__all__ = ["NutriHttpAgent", "DEFAULT_BASE_URL", "DEFAULT_TIMEOUT_S"]
