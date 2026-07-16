# -*- coding: utf-8 -*-
"""
Site endpoints (ARCHITECTURE.md §4), thin wrappers over core.pipeline.
Uploads are raw request bodies (the web client sends the File object
directly; the Telegram bot forwards the downloaded document the same way).
"""
import os, sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field

from api import db, worker
from api.auth import get_current_user
from core import pipeline

MAX_UPLOAD = 200 * 1024 * 1024

router = APIRouter()

async def read_upload(request: Request) -> bytes:
    length = int(request.headers.get("content-length", 0))
    if not 0 < length <= MAX_UPLOAD:
        raise HTTPException(400, "bad upload size")
    return await request.body()

def known_overview():
    return {"sources": pipeline.list_known(),
            "total_known": len(pipeline.known_set())}

# ---------------- known-vocab sources ----------------

@router.get("/known", tags=["known"])
def get_known(user: dict = Depends(get_current_user)):
    """Uploaded known-vocabulary sources and the merged word count."""
    return known_overview()

@router.post("/known", tags=["known"])
async def upload_known(request: Request, name: str = Query(...),
                       user: dict = Depends(get_current_user)):
    """Add a source: PDF/TXT of a finished book, or a plain word list."""
    blob = await read_upload(request)
    try:
        info = pipeline.add_known_source(name, blob)
    except ValueError as e:
        raise HTTPException(400, str(e))
    return {**info, **known_overview()}

@router.delete("/known/{slug}", tags=["known"])
def delete_known(slug: str, user: dict = Depends(get_current_user)):
    return {"deleted": pipeline.delete_known(slug), **known_overview()}

# ---------------- target books ----------------

@router.get("/books", tags=["books"])
def get_books(user: dict = Depends(get_current_user)):
    return {"books": pipeline.list_books()}

@router.post("/books", tags=["books"])
async def upload_book(request: Request, name: str = Query(...),
                      user: dict = Depends(get_current_user)):
    """Upload the book to simplify (PDF, or TXT with/without page markers)."""
    blob = await read_upload(request)
    try:
        return pipeline.add_book(name, blob)
    except ValueError as e:
        raise HTTPException(400, str(e))

@router.get("/books/{slug}/stats", tags=["books"])
def book_stats(slug: str, user: dict = Depends(get_current_user)):
    """Coverage vs the known vocabulary: totals, per-page %, sample of
    unknown words, and what each level would keep."""
    try:
        return pipeline.book_stats(slug)
    except FileNotFoundError:
        raise HTTPException(404, f"book {slug!r} not found")
    except ValueError as e:
        raise HTTPException(400, str(e))

# ---------------- translate jobs (input queue + event stream) ----------------

class TranslateReq(BaseModel):
    level: int
    page_from: int = Field(alias="from")
    page_to: int = Field(alias="to")

@router.post("/books/{slug}/translate", tags=["jobs"], status_code=202)
def translate(slug: str, req: TranslateReq,
              user: dict = Depends(get_current_user)):
    """Queue a translate job (the user's click = the Gemini spend command;
    cached pages are free). Poll GET /jobs/{id} for progress."""
    if req.level not in pipeline.LEVELS:
        raise HTTPException(400, f"level must be one of {pipeline.LEVELS}")
    try:
        pages = pipeline.book_pages(slug)
    except FileNotFoundError:
        raise HTTPException(404, f"book {slug!r} not found")
    p_from = max(1, req.page_from)
    p_to = min(max(pages), req.page_to)
    if p_from > p_to:
        raise HTTPException(400, "empty page range")
    if db.active_job_for(slug, req.level, user["id"]) is not None:
        raise HTTPException(409, "a job for this book+level is already "
                                 "queued or running")
    job_id = db.create_job(user["id"], slug, req.level, p_from, p_to)
    worker.wake()
    return db.get_job(job_id)

@router.get("/jobs/{job_id}", tags=["jobs"])
def job_progress(job_id: int, user: dict = Depends(get_current_user)):
    """Job state + recent events: status, done/total, pct, eta_s."""
    job = db.get_job(job_id)
    if job is None or job["user_id"] != user["id"]:
        raise HTTPException(404, "job not found")
    return job

@router.get("/jobs", tags=["jobs"])
def latest_job(book: str = Query(...),
               user: dict = Depends(get_current_user)):
    """The most recent job for a book (client convenience on reload)."""
    job = db.latest_job_for(book, user["id"])
    return job or {"status": "idle", "book_slug": book}
