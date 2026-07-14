"""End-to-end tests for POST /chat.

The point of these tests: prove that on the second turn, the LLM sees
(1) the latest plan in the system prompt, and (2) prior turns as short
notes — not a stack of full MealPlan JSONs in history.
"""

from __future__ import annotations

from fastapi.testclient import TestClient

from agent.session import SessionStore
from tests.conftest import FakeLLM


def test_chat_uses_session_and_forwards_history(
    client: TestClient, fake_llm: FakeLLM
) -> None:
    plan_response = client.post(
        "/plan",
        json={
            "goal": "maintain",
            "calorie_target": 2000,
            "cuisine_preferences": ["Brazilian"],
        },
    )
    session_id = plan_response.json()["session_id"]

    chat_response = client.post(
        "/chat",
        json={"session_id": session_id, "message": "make lunch lighter"},
    )

    assert chat_response.status_code == 200, chat_response.text

    second_call = fake_llm.calls[1]
    messages = second_call["messages"]
    system = second_call["system"]

    # We should see: initial user message, short model note, new user turn.
    assert len(messages) == 3
    assert messages[0].role == "user"
    assert messages[1].role == "model"
    assert messages[2].role == "user"
    assert messages[2].content == "make lunch lighter"

    # Plan lives in system on Call 2 — not as a full JSON model turn in history.
    assert "Current meal plan:" in system
    assert "Tapioca com queijo" in system
    assert messages[1].content == "Balanced day."
    assert "meals" not in messages[1].content


# After /chat succeeds: current_plan is replaced, and history grows by the
# new user message + another short assistant note.
def test_chat_replaces_current_plan_and_appends_short_note(
    client: TestClient, fake_llm: FakeLLM, session_store: SessionStore
) -> None:
    # Give Call 2 a different notes string so we can tell the plan was replaced.
    plan_response = client.post(
        "/plan",
        json={"goal": "maintain", "calorie_target": 2000},
    )
    session_id = plan_response.json()["session_id"]

    fake_llm.canned_reply = fake_llm.canned_reply.replace(
        '"notes": "Balanced day."',
        '"notes": "Made lunch lighter."',
    )

    chat_response = client.post(
        "/chat",
        json={"session_id": session_id, "message": "make lunch lighter"},
    )
    assert chat_response.status_code == 200, chat_response.text

    session = session_store.get(session_id)
    assert session is not None
    assert session.current_plan is not None
    assert session.current_plan.notes == "Made lunch lighter."

    # user task, first note, refinement, second note
    assert len(session.history) == 4
    assert session.history[2].role == "user"
    assert session.history[2].content == "make lunch lighter"
    assert session.history[3].role == "model"
    assert session.history[3].content == "Made lunch lighter."


def test_chat_rejects_unknown_session(client: TestClient) -> None:
    response = client.post(
        "/chat",
        json={"session_id": "does-not-exist", "message": "hi"},
    )

    assert response.status_code == 404


def test_chat_rejects_empty_message(client: TestClient) -> None:
    plan_response = client.post(
        "/plan",
        json={"goal": "maintain", "calorie_target": 2000},
    )
    session_id = plan_response.json()["session_id"]

    response = client.post(
        "/chat",
        json={"session_id": session_id, "message": ""},
    )

    assert response.status_code == 422
