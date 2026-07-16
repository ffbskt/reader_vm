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
    from api.worker import ensure_worker
    ensure_worker()

@app.get("/health", tags=["system"])
def health():
    """Liveness probe for monitors and load balancers."""
    return {"status": "ok", "version": VERSION}

@app.get("/me", tags=["auth"])
def me(user: dict = Depends(get_current_user)):
    """The authenticated user's profile and tier (quota usage joins in 2b)."""
    return {"user": user}

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
