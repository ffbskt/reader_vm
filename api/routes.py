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
