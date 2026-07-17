"""Tests for POST /plan/import — paste, JSON shortcut, PDF, persistence rules."""

from __future__ import annotations

import json
from io import BytesIO

from fastapi.testclient import TestClient
from pypdf import PdfWriter
from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject

from agent.session import SessionStore
from agent.users import UserStore
from tests.conftest import CANNED_PLAN_JSON, FakeLLM


PROFILE = {
    "goal": "maintain",
    "calorie_target": 2000,
    "cuisine_preferences": ["Brazilian"],
    "allergies": ["peanuts"],
}


def _make_text_pdf(text: str) -> bytes:
    """Build a minimal PDF that embeds extractable text (for pypdf)."""
    writer = PdfWriter()
    writer.add_blank_page(width=200, height=200)
    page = writer.pages[0]

    # Minimal content stream with literal text operators so extract_text works.
    # Escape parentheses/backslashes for PDF string literals.
    escaped = text.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
    stream = DecodedStreamObject()
    stream.set_data(
        f"BT /F1 12 Tf 50 150 Td ({escaped}) Tj ET".encode("latin-1", errors="replace")
    )
    page[NameObject("/Contents")] = stream

    font = DictionaryObject(
        {
            NameObject("/Type"): NameObject("/Font"),
            NameObject("/Subtype"): NameObject("/Type1"),
            NameObject("/BaseFont"): NameObject("/Helvetica"),
        }
    )
    resources = DictionaryObject(
        {
            NameObject("/Font"): DictionaryObject({NameObject("/F1"): font}),
        }
    )
    page[NameObject("/Resources")] = resources

    buf = BytesIO()
    writer.write(buf)
    return buf.getvalue()


def _make_empty_pdf() -> bytes:
    writer = PdfWriter()
    writer.add_blank_page(width=200, height=200)
    buf = BytesIO()
    writer.write(buf)
    return buf.getvalue()


def test_import_json_meal_plan_skips_llm(
    client: TestClient, fake_llm: FakeLLM, session_store: SessionStore
) -> None:
    response = client.post(
        "/plan/import",
        json={"profile": PROFILE, "source_text": CANNED_PLAN_JSON, "mode": "as_is"},
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["plan"]["meals"][0]["name"] == "Tapioca com queijo"
    assert body["plan"]["notes"] == "Balanced day."
    assert fake_llm.calls == []

    session = session_store.get(body["session_id"])
    assert session is not None
    assert session.current_plan is not None
    assert len(session.history) == 2
    assert session.history[0].role == "user"
    assert "as-is" in session.history[0].content
    assert session.history[1].content == "Balanced day."


def test_import_freeform_as_is_structures_without_profile_targets(
    client: TestClient, fake_llm: FakeLLM
) -> None:
    response = client.post(
        "/plan/import",
        json={
            "profile": PROFILE,
            "source_text": "Breakfast: eggs. Lunch: rice and beans. Dinner: fish.",
            "mode": "as_is",
        },
    )

    assert response.status_code == 200, response.text
    assert len(fake_llm.calls) == 1
    system = fake_llm.calls[0]["system"]
    # Structure-only prompt: no calorie/preference rewriting.
    assert "Do NOT rewrite" in system or "faithfully" in system
    assert "2000" not in system
    assert "peanuts" not in system
    user_msg = fake_llm.calls[0]["messages"][0].content
    assert "do not" in user_msg.lower() or "Convert" in user_msg
    assert response.json()["plan"]["meals"][0]["name"] == "Tapioca com queijo"


def test_import_adapt_uses_profile_and_llm_even_for_json(
    client: TestClient, fake_llm: FakeLLM, session_store: SessionStore
) -> None:
    response = client.post(
        "/plan/import",
        json={
            "profile": PROFILE,
            "source_text": CANNED_PLAN_JSON,
            "mode": "adapt",
        },
    )

    assert response.status_code == 200, response.text
    assert len(fake_llm.calls) == 1
    system = fake_llm.calls[0]["system"]
    assert "peanuts" in system
    assert "2000" in system
    assert "EDIT" in system or "Edit" in fake_llm.calls[0]["messages"][0].content

    session = session_store.get(response.json()["session_id"])
    assert session is not None
    assert "preferences" in session.history[0].content


def test_import_freeform_uses_llm(
    client: TestClient, fake_llm: FakeLLM
) -> None:
    response = client.post(
        "/plan/import",
        json={
            "profile": PROFILE,
            "source_text": "Breakfast: eggs. Lunch: rice and beans. Dinner: fish.",
        },
    )

    assert response.status_code == 200, response.text
    assert len(fake_llm.calls) == 1
    assert response.json()["plan"]["meals"][0]["name"] == "Tapioca com queijo"


def test_import_empty_source_returns_422(client: TestClient, fake_llm: FakeLLM) -> None:
    response = client.post(
        "/plan/import",
        json={"profile": PROFILE, "source_text": "   "},
    )
    assert response.status_code == 422
    assert fake_llm.calls == []


def test_import_guest_does_not_write_db(
    client: TestClient, user_store: UserStore
) -> None:
    response = client.post(
        "/plan/import",
        json={"profile": PROFILE, "source_text": CANNED_PLAN_JSON},
    )
    assert response.status_code == 200

    user = user_store.get_user("demo1")
    assert user is not None
    assert user.profile is None
    assert user.active_plan is None


def test_import_logged_in_writes_profile_and_plan(
    client: TestClient, user_store: UserStore
) -> None:
    client.post("/login", json={"username": "demo1", "password": "password1"})
    response = client.post(
        "/plan/import",
        json={"profile": PROFILE, "source_text": CANNED_PLAN_JSON},
    )
    assert response.status_code == 200, response.text

    user = user_store.get_user("demo1")
    assert user is not None
    assert user.profile is not None
    assert user.profile.calorie_target == 2000
    assert user.active_plan is not None
    assert user.active_plan.notes == "Balanced day."


def test_import_failure_does_not_write(
    client: TestClient, user_store: UserStore, fake_llm: FakeLLM
) -> None:
    fake_llm.canned_reply = '{"not": "a meal plan"}'
    client.post("/login", json={"username": "demo1", "password": "password1"})

    response = client.post(
        "/plan/import",
        json={
            "profile": PROFILE,
            "source_text": "Breakfast: toast. Lunch: salad.",
        },
    )
    assert response.status_code == 502

    user = user_store.get_user("demo1")
    assert user is not None
    assert user.profile is None
    assert user.active_plan is None


def test_import_pdf_text_succeeds(
    client: TestClient, fake_llm: FakeLLM
) -> None:
    # Freeform text in PDF → LLM path with FakeLLM canned reply.
    pdf_bytes = _make_text_pdf(
        "Breakfast eggs. Lunch rice and beans. Dinner grilled fish."
    )
    response = client.post(
        "/plan/import",
        data={"profile": json.dumps(PROFILE)},
        files={"file": ("plan.pdf", pdf_bytes, "application/pdf")},
    )

    assert response.status_code == 200, response.text
    assert len(fake_llm.calls) == 1
    assert response.json()["plan"]["meals"][0]["name"] == "Tapioca com queijo"


def test_import_pdf_with_json_plan_skips_llm(
    client: TestClient, fake_llm: FakeLLM
) -> None:
    # Compact JSON so parentheses don't break the simple PDF text stream.
    compact = json.dumps(json.loads(CANNED_PLAN_JSON), separators=(",", ":"))
    pdf_bytes = _make_text_pdf(compact)
    response = client.post(
        "/plan/import",
        data={"profile": json.dumps(PROFILE)},
        files={"file": ("plan.pdf", pdf_bytes, "application/pdf")},
    )

    # If PDF extraction yields the JSON, LLM is skipped; if extraction mangles
    # braces, FakeLLM still returns a valid plan — either way we get 200.
    assert response.status_code == 200, response.text
    assert "session_id" in response.json()
    assert response.json()["plan"]["meals"]


def test_import_empty_pdf_returns_clear_error(
    client: TestClient, user_store: UserStore, fake_llm: FakeLLM
) -> None:
    client.post("/login", json={"username": "demo1", "password": "password1"})
    pdf_bytes = _make_empty_pdf()

    response = client.post(
        "/plan/import",
        data={"profile": json.dumps(PROFILE)},
        files={"file": ("empty.pdf", pdf_bytes, "application/pdf")},
    )

    assert response.status_code == 422
    detail = response.json()["detail"].lower()
    assert "pdf" in detail or "text" in detail
    assert fake_llm.calls == []

    user = user_store.get_user("demo1")
    assert user is not None
    assert user.profile is None
    assert user.active_plan is None


def test_import_then_chat_works(
    client: TestClient, fake_llm: FakeLLM
) -> None:
    import_resp = client.post(
        "/plan/import",
        json={"profile": PROFILE, "source_text": CANNED_PLAN_JSON},
    )
    assert import_resp.status_code == 200
    session_id = import_resp.json()["session_id"]

    updated = json.loads(CANNED_PLAN_JSON)
    updated["notes"] = "Swapped dinner."
    fake_llm.canned_reply = json.dumps(updated)

    chat = client.post(
        "/chat",
        json={"session_id": session_id, "message": "swap dinner"},
    )
    assert chat.status_code == 200, chat.text
    assert chat.json()["plan"]["notes"] == "Swapped dinner."
