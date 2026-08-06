"""Target adapter for the nutri-assistant host app.

Talks to the live FastAPI service over HTTP — zero imports from `agent/`
or `src/`. That is the whole portability bet: to evaluate a different
product, write a different ~30-line adapter and leave the rest alone.

Two modes, chosen by `starter.context`:

  A) Resume an existing user (edit-focused evals) — when context has
     `resume_as` (e.g. "demo5"):
       1. POST /login  (password from DEMO_USER_PASSWORDS env)
       2. POST /session/resume  → existing plan + session_id
       3. Seed transcript with that plan as the baseline
       4. POST /chat  for every starter turn (edits only)

  B) Create a fresh plan (legacy) — no `resume_as`:
       1. POST /plan  with context as the profile body
          (records turns[0] as the user message)
       2. POST /chat  for each remaining turn

Reviewers never log in — this auth is only the adapter "pretending"
to be the demo user so the host loads their saved plan. Chat does not
write active_plan, so eval edits stay in the session only.
"""

from __future__ import annotations

import json
import os
import time
from typing import Any

import httpx

from eval_framework.adapters.base import Conversation, ConversationStarter

# Meal-plan generation can take tens of seconds on a cold Gemini call.
DEFAULT_TIMEOUT_S = 90.0
DEFAULT_BASE_URL = "http://127.0.0.1:8000"

# Context key that switches the adapter into resume/edit mode.
RESUME_AS_KEY = "resume_as"


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
            "mode": None,
        }

        resume_as = starter.context.get(RESUME_AS_KEY)
        if resume_as:
            meta["mode"] = "resume"
            return self._run_resume(
                starter, username=str(resume_as), transcript=transcript, meta=meta
            )

        meta["mode"] = "plan"
        return self._run_plan(starter, transcript=transcript, meta=meta)

    # --- mode A: login → resume → chat edits ---------------------------------

    def _run_resume(
        self,
        starter: ConversationStarter,
        *,
        username: str,
        transcript: list[dict[str, str]],
        meta: dict[str, Any],
    ) -> Conversation:
        """Load a saved plan as the demo user, then apply edit turns via /chat."""
        password = _demo_password(username)
        if password is None:
            meta["errors"].append(
                {
                    "step": "login",
                    "detail": (
                        f"No password for {username!r} in DEMO_USER_PASSWORDS. "
                        "Check your .env."
                    ),
                }
            )
            transcript.append(
                {"role": "user", "content": starter.turns[0]},
            )
            transcript.append(
                {
                    "role": "assistant",
                    "content": _error_content(meta["errors"][-1]),
                }
            )
            return Conversation(transcript=transcript, agent_meta=meta)

        if not self._post_login(username, password, meta):
            transcript.append({"role": "user", "content": starter.turns[0]})
            transcript.append(
                {
                    "role": "assistant",
                    "content": _error_content(meta["errors"][-1]),
                }
            )
            return Conversation(transcript=transcript, agent_meta=meta)

        plan_body, session_id = self._post_resume(meta)
        if plan_body is None or session_id is None:
            transcript.append({"role": "user", "content": starter.turns[0]})
            transcript.append(
                {
                    "role": "assistant",
                    "content": _error_content(meta["errors"][-1]),
                }
            )
            return Conversation(transcript=transcript, agent_meta=meta)

        meta["session_id"] = session_id
        meta["resume_as"] = username

        # Baseline so the review screen / judge can see the plan being edited.
        # Not a real user utterance — adapter bookkeeping only.
        transcript.append(
            {"role": "user", "content": "(existing meal plan loaded)"},
        )
        transcript.append(
            {
                "role": "assistant",
                "content": json.dumps(plan_body, ensure_ascii=False),
            }
        )

        for user_text in starter.turns:
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

    # --- mode B: /plan then /chat (create-focused) ---------------------------

    def _run_plan(
        self,
        starter: ConversationStarter,
        *,
        transcript: list[dict[str, str]],
        meta: dict[str, Any],
    ) -> Conversation:
        first_user = starter.turns[0]
        transcript.append({"role": "user", "content": first_user})
        # Strip resume_as if somehow present; /plan expects a UserProfile body.
        plan_context = {
            k: v for k, v in starter.context.items() if k != RESUME_AS_KEY
        }
        plan_body, session_id = self._post_plan(plan_context, meta)
        if plan_body is None:
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

    # --- HTTP helpers --------------------------------------------------------

    def _post_login(
        self, username: str, password: str, meta: dict[str, Any]
    ) -> bool:
        started = time.perf_counter()
        try:
            response = self._client.post(
                "/login",
                json={"username": username, "password": password},
            )
            latency_ms = int((time.perf_counter() - started) * 1000)
            meta["latencies_ms"].append({"step": "login", "ms": latency_ms})
            if response.status_code >= 400:
                meta["errors"].append(
                    {
                        "step": "login",
                        "status_code": response.status_code,
                        "detail": _safe_detail(response),
                    }
                )
                return False
            return True
        except httpx.HTTPError as exc:
            latency_ms = int((time.perf_counter() - started) * 1000)
            meta["latencies_ms"].append({"step": "login", "ms": latency_ms})
            meta["errors"].append({"step": "login", "detail": str(exc)})
            return False

    def _post_resume(
        self, meta: dict[str, Any]
    ) -> tuple[dict[str, Any] | None, str | None]:
        started = time.perf_counter()
        try:
            response = self._client.post("/session/resume")
            latency_ms = int((time.perf_counter() - started) * 1000)
            meta["latencies_ms"].append({"step": "resume", "ms": latency_ms})
            if response.status_code >= 400:
                meta["errors"].append(
                    {
                        "step": "resume",
                        "status_code": response.status_code,
                        "detail": _safe_detail(response),
                    }
                )
                return None, None
            data = response.json()
            return data["plan"], data["session_id"]
        except (httpx.HTTPError, KeyError, ValueError) as exc:
            latency_ms = int((time.perf_counter() - started) * 1000)
            meta["latencies_ms"].append({"step": "resume", "ms": latency_ms})
            meta["errors"].append({"step": "resume", "detail": str(exc)})
            return None, None

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


def _demo_password(username: str) -> str | None:
    """Read password for a demo user from DEMO_USER_PASSWORDS (JSON in env)."""
    raw = os.environ.get("DEMO_USER_PASSWORDS", "").strip()
    if not raw:
        return None
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return None
    if not isinstance(data, dict):
        return None
    value = data.get(username)
    return str(value) if value is not None else None


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


__all__ = [
    "NutriHttpAgent",
    "DEFAULT_BASE_URL",
    "DEFAULT_TIMEOUT_S",
    "RESUME_AS_KEY",
]
