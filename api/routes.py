# -*- coding: utf-8 -*-
"""
Site endpoints (ARCHITECTURE.md §4), thin wrappers over core.pipeline.
Uploads are raw request bodies (the web client sends the File object
directly; the Telegram bot forwards the downloaded document the same way).
"""
import json, os, subprocess, sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import Response
from pydantic import BaseModel, Field

from api import db, limits, worker
from api.auth import get_current_user
from core import pipeline
from core.vocab import MODES

# 50 MB: plenty for book PDFs, and the whole body is held in RAM on a
# 1 GB machine
MAX_UPLOAD = 50 * 1024 * 1024

router = APIRouter()

async def read_upload(request: Request) -> bytes:
    length = int(request.headers.get("content-length", 0))
    if not 0 < length <= MAX_UPLOAD:
        raise HTTPException(400, "bad upload size")
    used = pipeline.storage_used()
    if used + length > pipeline.STORAGE_LIMIT:
        mb = pipeline.STORAGE_LIMIT // (1024 * 1024)
        raise HTTPException(413, f"storage limit reached ({mb} MB per user). "
                                 f"Delete a book or word list to free space.")
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
        # PDF parsing is CPU-heavy: threadpool, NEVER the event loop
        info = await run_in_threadpool(pipeline.add_known_source, name, blob)
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
        # PDF parsing is CPU-heavy: threadpool, NEVER the event loop
        return await run_in_threadpool(pipeline.add_book, name, blob)
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
    if p_to - p_from + 1 > limits.MAX_RANGE:
        raise HTTPException(400, f"range too large (max {limits.MAX_RANGE} "
                                 "pages per request)")
    if db.active_job_for(slug, req.level, user["id"]) is not None:
        raise HTTPException(409, "a job for this book+level is already "
                                 "queued or running")
    if db.running_jobs_for_user(user["id"]) >= limits.MAX_CONCURRENT_JOBS:
        raise HTTPException(429, "you already have a translation running — "
                                 "wait for it to finish")
    # only pages not already cached will cost a Gemini call and count to quota
    cached = set(pipeline.cached_pages(slug, req.level))
    new_pages = [p for p in range(p_from, p_to + 1) if p not in cached]
    used = db.usage_today(user["id"])
    if used + len(new_pages) > limits.DAILY_PAGES:
        left = max(0, limits.DAILY_PAGES - used)
        raise HTTPException(429,
            f"daily translation limit reached ({limits.DAILY_PAGES} new "
            f"pages/day). {left} left today; {len(new_pages)} needed. "
            "Cached pages are always free.")
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

# ---------------- reader + PDF ----------------

@router.get("/books/{slug}/reader", tags=["reader"])
def reader_data(slug: str, level: int = 0,
                user: dict = Depends(get_current_user)):
    """Simplified pages at one level + the merged hover dictionary."""
    try:
        return pipeline.reader_payload(slug, level)
    except FileNotFoundError:
        raise HTTPException(404, f"book {slug!r} not found")

@router.get("/books/{slug}/pdf", tags=["reader"])
def build_pdf(slug: str, level: int = 0, mode: str = "repeat",
              user: dict = Depends(get_current_user)):
    """Learner PDF from the translated pages, in one of the 4 vocabulary
    modes. Pure local work — no API calls."""
    if mode not in MODES:
        raise HTTPException(400, f"mode must be one of {list(MODES)}")
    done = pipeline.cached_pages(slug, level)
    if not done:
        raise HTTPException(400, "no translated pages at this level yet")
    bdir = pipeline.book_dir(slug)
    meta = json.load(open(os.path.join(bdir, "meta.json"), encoding="utf-8"))
    out = os.path.join(bdir, f"{meta['slug']}_L{level}_{mode}.pdf")
    r = subprocess.run(
        [sys.executable, os.path.join(ROOT, "build_pdf.py"),
         "--from", str(done[0]), "--to", str(done[-1]),
         "--mode", mode, "--out", out,
         "--dir", os.path.join(bdir, "simplified"),
         "--pattern", f"page{{n}}_L{level}.json",
         "--title", meta["title"], "--author", "",
         "--known-note", "al vocabulario del estudiante"],
        capture_output=True, text=True, encoding="utf-8",
        cwd=ROOT, timeout=300)
    if r.returncode != 0 or not os.path.exists(out):
        raise HTTPException(
            500, (r.stderr or r.stdout or "build failed")[-400:])
    body = open(out, "rb").read()
    fname = os.path.basename(out)
    return Response(content=body, media_type="application/pdf", headers={
        "Content-Disposition": f'attachment; filename="{fname}"',
        "Cache-Control": "no-store"})
