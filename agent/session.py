# In-memory conversation store, keyed by session ID.
#
# Deliberately primitive: one Python dict wrapped in a class. Two consequences
# we're accepting for Phase 1:
#   1. A server restart wipes every conversation.
#   2. It does not scale beyond a single process.
# Both are fine right now — we're building a learning tool, not a product.
# When it stops being fine, this file is the only one that changes.

from __future__ import annotations

import uuid
from dataclasses import dataclass, field

from agent.llm import Message
from agent.schemas import MealPlan, UserProfile


# Everything we need to remember for ONE guest user:
#   - their profile (hard constraints like allergies, calories)
#   - the latest meal plan (None until /plan succeeds)
#   - the running conversation history (every user turn + every model turn).
# `@dataclass` wires up __init__, __repr__, __eq__ from the declared fields.
@dataclass
class Session:
    profile: UserProfile
    # Set after the first successful /plan (and replaced on each /chat).
    # Nothing reads this yet — Step 1 only adds the field.
    current_plan: MealPlan | None = None
    # `field(default_factory=list)` = give each Session its own fresh list.
    # If we wrote `= []` here, every Session would share the same list —
    # classic Python foot-gun (mutable default arguments).
    history: list[Message] = field(default_factory=list)


# Thin wrapper around a dict so we can swap it for Redis / SQLite later.
# A single instance is shared across all requests (see dependencies.py's
# `@lru_cache` on get_session_store).
class SessionStore:
    # Called exactly once, by get_session_store() the first time FastAPI
    # needs the store. Every subsequent request reuses the same instance.
    def __init__(self) -> None:
        # `dict[str, Session]` on the RHS is a type hint for the empty dict —
        # it tells the type checker "string keys, Session values".
        self._sessions: dict[str, Session] = {}

    # Called by POST /plan at the start of a new user's journey.
    # Generates a fresh 32-char hex session_id, attaches a new Session
    # carrying the user's profile + an empty history, and returns both so the
    # caller can save the id to the response and keep appending to the session.
    def create(self, profile: UserProfile) -> tuple[str, Session]:
        # `.hex` gives the UUID as a 32-char hex string (no dashes) — cleaner
        # in URLs and headers than the default "ab12-34cd-..." form.
        session_id = uuid.uuid4().hex
        session = Session(profile=profile)
        self._sessions[session_id] = session
        # Returning two values = returning a tuple. The caller unpacks with
        # `session_id, session = store.create(profile)`.
        return session_id, session

    # Called by POST /chat on every refinement request to load the
    # conversation that /plan originally created. Returns None (NOT a
    # KeyError) when the id is unknown — /chat turns that None into a 404.
    #
    # `Session | None` (PEP 604) = "either a Session or None". Same as
    # `Optional[Session]` but newer syntax — works thanks to the
    # `from __future__ import annotations` at the top of the file.
    def get(self, session_id: str) -> Session | None:
        return self._sessions.get(session_id)
