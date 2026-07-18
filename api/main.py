# -*- coding: utf-8 -*-
"""
Reader API — run locally with:

  python -m uvicorn api.main:app --port 8100

Interactive docs: http://localhost:8100/docs (auto-generated OpenAPI).
Endpoint contract: docs/ARCHITECTURE.md §4. Ported from server.py in 1.4+.
"""
import os, sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from fastapi import Depends, FastAPI
from fastapi.responses import FileResponse

from api.auth import get_current_user
from api.routes import router as site_router

VERSION = "0.1.0"

app = FastAPI(
    title="Reader API",
    version=VERSION,
    description="Leveled book simplifier: known vocabulary + target book "
                "-> simplified text at levels 0/25/50/75, online reader "
                "with hover translations, PDF in 4 vocabulary modes.")

@app.on_event("startup")
def start_worker():
    from core import pipeline
    pipeline.migrate_legacy_to_user1()      # one-time: flat data -> users/1/
    pipeline.migrate_books_to_library()     # one-time: books -> shared library
    from api.worker import ensure_worker
    ensure_worker()

@app.get("/health", tags=["system"])
def health():
    """Liveness probe for monitors and load balancers."""
    return {"status": "ok", "version": VERSION}

@app.get("/me", tags=["auth"])
def me(user: dict = Depends(get_current_user)):
    """The authenticated user's profile, tier, storage, and daily quota."""
    from api.auth import public_user
    from api import db, limits
    from core import pipeline
    used = pipeline.storage_used()
    pages = db.usage_today(user["id"])
    return {"user": public_user(user),
            "storage": {"used": used, "limit": pipeline.STORAGE_LIMIT,
                        "pct": round(used / pipeline.STORAGE_LIMIT * 100, 1)},
            "quota": {"pages_today": pages, "pages_limit": limits.DAILY_PAGES}}

@app.get("/auth/config", tags=["auth"])
def auth_config():
    """What the SPA needs to render the login buttons (both public)."""
    from api.auth import GOOGLE_CLIENT_ID, TELEGRAM_BOT_USERNAME, REQUIRE_AUTH
    return {"google_client_id": GOOGLE_CLIENT_ID,
            "telegram_bot": TELEGRAM_BOT_USERNAME,
            "require_auth": REQUIRE_AUTH}

@app.post("/auth/telegram-widget", tags=["auth"])
def auth_telegram_widget(body: dict):
    """Web 'Log in with Telegram' — verify the widget hash, issue a JWT."""
    from api.auth import login_telegram_widget
    try:
        return login_telegram_widget(body)
    except (ValueError, KeyError) as e:
        from fastapi import HTTPException
        raise HTTPException(401, f"Telegram login rejected: {e}")

@app.post("/auth/telegram", tags=["auth"])
def auth_telegram(body: dict):
    """Bot-only: exchange a Telegram user id for a session JWT."""
    from api.auth import login_telegram
    try:
        return login_telegram(int(body.get("tg_id", 0)),
                              str(body.get("name", "")),
                              str(body.get("bot_secret", "")))
    except ValueError as e:
        from fastapi import HTTPException
        raise HTTPException(401, str(e))

@app.post("/auth/google", tags=["auth"])
def auth_google(body: dict):
    """Exchange a verified Google ID token for our session JWT."""
    from api.auth import login_google
    try:
        return login_google(body.get("id_token", ""))
    except ValueError as e:
        from fastapi import HTTPException
        raise HTTPException(401, f"Google token rejected: {e}")

app.include_router(site_router)

# the two SPA pages, served explicitly (never expose the whole repo dir —
# it contains the API key file)
@app.get("/", include_in_schema=False)
@app.get("/app.html", include_in_schema=False)
def spa():
    return FileResponse(os.path.join(ROOT, "app.html"))

@app.get("/reader_site.html", include_in_schema=False)
def reader_page():
    return FileResponse(os.path.join(ROOT, "reader_site.html"))
