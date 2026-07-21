# FastAPI dependency providers.
#
# Routes declare dependencies like `llm: LLM = Depends(get_llm)`. At request
# time FastAPI looks at the `Depends(...)` defaults, calls the referenced
# function, and injects the result into the handler. In tests we do:
#     app.dependency_overrides[get_llm] = lambda: fake_llm
# to swap in fakes without touching route code.
#
# Teaching note: this is FastAPI's answer to "constructor injection" in
# languages with classes-as-containers. Here it's just a function reference
# used as a dictionary key.

from __future__ import annotations

from functools import lru_cache

# `import X as Y` aliases the name locally. Here it's a rename-on-import so
# the public `get_llm` in THIS file doesn't collide with the real one from
# agent/llm.py — tests can override THIS wrapper, not the underlying.
from agent.llm import LLM, get_llm as _get_real_llm
from agent.session import SessionStore
from agent.users import UserStore


# Called by FastAPI on every /plan and /chat request (both routes declare
# `llm: LLM = Depends(get_llm)`). Returns the production GeminiLLM in real
# life, or a FakeLLM in tests (via `app.dependency_overrides[get_llm]`).
def get_llm() -> LLM:
    return _get_real_llm()


# SessionStore talks to Supabase over HTTPS; caching the client wrapper still
# avoids re-reading env + re-constructing the SDK on every request in one
# warm instance. Persistence itself is in the DB, not in this process.
@lru_cache(maxsize=1)
def get_session_store() -> SessionStore:
    return SessionStore()


# Same singleton pattern for the durable user store (Supabase `users` table).
# Tests override this with FakeUserStore via dependency_overrides.
@lru_cache(maxsize=1)
def get_user_store() -> UserStore:
    return UserStore()
