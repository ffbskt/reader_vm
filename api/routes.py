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

@router.delete("/books/{slug}", tags=["books"])
def delete_book(slug: str, user: dict = Depends(get_current_user)):
    """Remove this user's book. Shared library content is freed only if no
    other user still references it (their reading is never touched)."""
    return pipeline.delete_book(slug)

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
    baseline: bool = False        # True = generic CEFR simplify, no vocabulary

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
    cached = set(pipeline.cached_pages(slug, req.level, req.baseline))
    new_pages = [p for p in range(p_from, p_to + 1) if p not in cached]
    if new_pages and not req.baseline and not pipeline.known_set():
        raise HTTPException(400, "no known vocabulary yet — add one in step 1, "
                                 "or tick 'generic simplification'")
    used = db.usage_today(user["id"])
    if used + len(new_pages) > limits.DAILY_PAGES:
        left = max(0, limits.DAILY_PAGES - used)
        raise HTTPException(429,
            f"daily translation limit reached ({limits.DAILY_PAGES} new "
            f"pages/day). {left} left today; {len(new_pages)} needed. "
            "Cached pages are always free.")
    job_id = db.create_job(user["id"], slug, req.level, p_from, p_to,
                           req.baseline)
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
def reader_data(slug: str, level: int = 0, baseline: bool = False,
                langs: str = "en,ru",
                user: dict = Depends(get_current_user)):
    """Simplified pages + hover dictionary in the requested help languages
    (comma-separated, e.g. langs=fr or langs=en,ru)."""
    want = [l.strip() for l in langs.split(",") if l.strip()][:2] or ["en"]
    try:
        return pipeline.reader_payload(slug, level, baseline, want)
    except FileNotFoundError:
        raise HTTPException(404, f"book {slug!r} not found")

# ---------------- personal vocabulary (2g) ----------------

class VocabReq(BaseModel):
    lang: str
    words: list = []
    state: str = "learning"

@router.get("/vocab", tags=["vocab"])
def get_vocab(lang: str, user: dict = Depends(get_current_user)):
    """Counts + the learning list, and the known set (so the reader can mark
    each word by the user's own state)."""
    return {"lang": lang, "counts": db.vocab_counts(user["id"], lang),
            "learning": db.vocab_words(user["id"], lang, "learning"),
            "known": db.vocab_words(user["id"], lang, "known")}

@router.post("/vocab", tags=["vocab"])
def add_vocab(req: VocabReq, user: dict = Depends(get_current_user)):
    """Add words at a state (tap-to-learn -> learning; bulk import -> known)."""
    if req.state not in ("learning", "known"):
        raise HTTPException(400, "state must be learning or known")
    n = db.vocab_add(user["id"], req.lang, req.words[:2000], req.state)
    return {"changed": n, "counts": db.vocab_counts(user["id"], req.lang)}

@router.post("/vocab/promote", tags=["vocab"])
def promote_vocab(req: VocabReq, user: dict = Depends(get_current_user)):
    """Mark words as known (passed the review game)."""
    n = db.vocab_add(user["id"], req.lang, req.words[:2000], "known")
    return {"promoted": n, "counts": db.vocab_counts(user["id"], req.lang)}

class PosReq(BaseModel):
    level: int = 0
    baseline: bool = False
    page: int

@router.post("/books/{slug}/position", tags=["reader"])
def save_position(slug: str, req: PosReq,
                  user: dict = Depends(get_current_user)):
    """Remember the last page the user read at this level/mode."""
    db.save_position(user["id"], slug, req.level, req.baseline, req.page)
    return {"ok": True}

@router.get("/books/{slug}/position", tags=["reader"])
def get_positions(slug: str, level: int = None, baseline: bool = False,
                  user: dict = Depends(get_current_user)):
    """One saved page (with level+baseline) or all positions for the book."""
    if level is not None:
        return {"page": db.position(user["id"], slug, level, baseline)}
    return {"positions": db.positions(user["id"], slug)}

@router.get("/books/{slug}/map", tags=["reader"])
def book_map(slug: str, user: dict = Depends(get_current_user)):
    """Translation map: for each level, which pages are translated (guided +
    baseline), plus the user's saved reading positions."""
    levels = {}
    for lv in pipeline.LEVELS:
        levels[lv] = {"guided": pipeline.cached_pages(slug, lv, False),
                      "base": pipeline.cached_pages(slug, lv, True)}
    meta = pipeline._ensure_book_stats(pipeline.book_dir(slug))
    return {"pages": meta.get("pages", 0), "levels": levels,
            "positions": db.positions(user["id"], slug)}

@router.get("/books/{slug}/vocabgap", tags=["vocab"])
def vocab_gap(slug: str, user: dict = Depends(get_current_user)):
    """How many of this book's word types the user already knows vs. how
    many are NEW to learn."""
    from core.vocab import fold
    meta = pipeline._ensure_book_stats(pipeline.book_dir(slug))
    lang = meta.get("lang", "en")
    types = pipeline.book_word_types(slug)
    have = {fold(w) for w in db.vocab_words(user["id"], lang, "known")}
    known = len(types & have)
    return {"lang": lang, "types": len(types), "known": known,
            "new": len(types) - known}

@router.get("/vocab/quiz", tags=["vocab"])
def vocab_quiz(lang: str, book: str = "", n: int = 5,
               user: dict = Depends(get_current_user)):
    """A review round: up to N of the user's `learning` words + their
    translations (into `help`), shuffled, for a match-the-pairs game."""
    import random
    learning = db.vocab_words(user["id"], lang, "learning")
    if not learning:
        return {"pairs": [], "reason": "no words to review yet — tap words "
                                       "while reading to add them"}
    # translations come from a book's shared dictionary if given, else any
    from core import pipeline
    dictionary = {}
    slugs = [book] if book else [b["slug"] for b in pipeline.list_books()
                                 if b.get("lang") == lang]
    from core.vocab import lookup
    help_lang = "en" if lang != "en" else "ru"
    random.shuffle(learning)
    pairs = []
    for slug in slugs:
        if len(pairs) >= n:
            break
        try:
            d = pipeline.reader_payload(slug, 0, False, [help_lang])
        except Exception:
            continue
        for w in learning:
            if len(pairs) >= n:
                break
            if any(p["word"] == w for p in pairs):
                continue
            tr = lookup(d["dictionary"], w, [help_lang])
            if tr and tr.get(help_lang):
                pairs.append({"word": w, "translation": tr[help_lang]})
    return {"lang": lang, "help": help_lang, "pairs": pairs,
            "shuffled": random.sample([p["translation"] for p in pairs],
                                      len(pairs))}

@router.get("/vocab/starter", tags=["vocab"])
def starter_info(lang: str, user: dict = Depends(get_current_user)):
    """How big the frequency starter set is for a language."""
    return {"lang": lang, "available": len(pipeline.starter_words(lang))}

@router.post("/vocab/starter", tags=["vocab"])
def adopt_starter(lang: str, user: dict = Depends(get_current_user)):
    """One-tap: adopt the ~1500 most common words of a language as known —
    a beginner's baseline vocabulary."""
    words = pipeline.starter_words(lang)
    if not words:
        raise HTTPException(404, f"no starter set for {lang!r} yet")
    n = db.vocab_add(user["id"], lang, words, "known")
    return {"adopted": len(words), "changed": n,
            "counts": db.vocab_counts(user["id"], lang)}

@router.get("/books/{slug}/languages", tags=["reader"])
def book_languages(slug: str, user: dict = Depends(get_current_user)):
    """Help languages this book's shared dictionary already covers."""
    return {"languages": pipeline.book_languages(slug),
            "supported": list(pipeline.LANG_NAMES)}

@router.post("/books/{slug}/languages/{lang}", tags=["reader"])
def fill_book_language(slug: str, lang: str, batches: int = 3,
                       user: dict = Depends(get_current_user)):
    """Generate the hover dictionary for a help language this book lacks.
    Bounded per call (so the UI can show progress and call again); results
    are cached in the SHARED book dictionary, so the next reader pays $0."""
    if lang not in pipeline.LANG_NAMES:
        raise HTTPException(400, f"unsupported language {lang!r}")
    pending = len(pipeline.words_needing_language(slug, lang))
    if not pending:
        return {"done": True, "added": 0, "remaining": 0}
    used = db.usage_today(user["id"])
    if used >= limits.DAILY_PAGES:
        raise HTTPException(429, "daily limit reached — try again tomorrow")
    batches = max(1, min(int(batches), 5))
    try:
        res = pipeline.fill_language(slug, lang, max_batches=batches)
    except pipeline.QuotaError as e:
        raise HTTPException(429, f"Gemini quota: {e}")
    if res["requests"]:
        db.add_usage(user["id"], res["requests"])   # meter it like a page
    res["done"] = res["remaining"] == 0
    return res

@router.get("/books/{slug}/pdf", tags=["reader"])
def build_pdf(slug: str, level: int = 0, mode: str = "repeat",
              baseline: bool = False,
              user: dict = Depends(get_current_user)):
    """Learner PDF from the translated pages, in one of the 4 vocabulary
    modes. Pure local work — no API calls."""
    if mode not in MODES:
        raise HTTPException(400, f"mode must be one of {list(MODES)}")
    done = pipeline.cached_pages(slug, level, baseline)
    if not done:
        raise HTTPException(400, "no translated pages at this level yet")
    bdir = pipeline.book_dir(slug)
    meta = json.load(open(os.path.join(bdir, "meta.json"), encoding="utf-8"))
    suf = "_base" if baseline else ""
    # PDF depends only on (content, level, mode) -> shareable name in the lib
    out = os.path.join(bdir, f"pdf_L{level}{suf}_{mode}.pdf")
    r = subprocess.run(
        [sys.executable, os.path.join(ROOT, "build_pdf.py"),
         "--from", str(done[0]), "--to", str(done[-1]),
         "--mode", mode, "--out", out,
         "--dir", os.path.join(bdir, "simplified"),
         "--pattern", f"page{{n}}_L{level}{suf}.json",
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
