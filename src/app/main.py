# FastAPI application entry point.
#
# Run locally with:
#     uv run uvicorn src.app.main:app --reload
#
# What this file does:
#   - Loads `.env` (so GEMINI_API_KEY is available to agent/llm.py).
#   - Creates the FastAPI `app` object that Uvicorn serves.
#   - Registers the route modules (/plan, /chat) onto the app.
#   - Serves the onboarding HTML at GET /.

from __future__ import annotations

from pathlib import Path

from dotenv import load_dotenv
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from src.app.routes import auth as auth_routes
from src.app.routes import chat as chat_routes
from src.app.routes import plan as plan_routes
from src.app.routes import profile as profile_routes


# Reads .env into os.environ BEFORE the first request triggers get_llm(),
# so the Gemini API key is available when we actually need it.
load_dotenv()


# `FastAPI(...)` builds the app object. `title` + `version` show up in the
# auto-generated docs at /docs (Swagger UI) and /redoc.
app = FastAPI(title="Nutri Assistant", version="0.1.0")

# `__file__` is the path to THIS file. `.parent` walks up one directory.
# `/ "templates"` uses pathlib's overloaded `/` operator to join paths —
# cross-platform (Windows / macOS / Linux) without string-concat headaches.
_TEMPLATES_DIR = Path(__file__).parent / "templates"
templates = Jinja2Templates(directory=_TEMPLATES_DIR)


# Handler for GET /health. Called by FastAPI whenever anything (humans, uptime
# monitors, deployment platforms) hits /health. Returns a tiny JSON ping that
# just proves the process is up — no dependencies, no I/O.
@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


# Handler for GET /. Called by FastAPI when a browser opens the site root.
# Renders onboarding.html (the form + chat UI) via Jinja2 and returns it as HTML.
@app.get("/", response_class=HTMLResponse)
def onboarding(request: Request) -> HTMLResponse:
    return templates.TemplateResponse(request, "onboarding.html")


# Routes live in separate files for cleanliness; `include_router` stitches them
# back into the main app. Same URL space, different source files.
app.include_router(auth_routes.router)
app.include_router(profile_routes.router)
app.include_router(plan_routes.router)
app.include_router(chat_routes.router)
