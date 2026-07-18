# -*- coding: utf-8 -*-
"""
The job worker: one daemon thread consumes the jobs table FIFO (input
queue) and emits job_events (output queue — consumed by clients polling,
later by the Telegram bot and monitoring). Sequential Gemini calls with a
pause; cached pages are free; QuotaError parks the job as 'quota'.
Same behavior as core.pipeline._run_job, with DB state instead of job.json.
"""
import threading, time

from api import db
from core import pipeline
from core.pipeline import (API_GAP_S, QuotaError, fill_missing_translations,
                           simplify_book_page, simplify_page_baseline)

_wake = threading.Event()
_started = False
_start_lock = threading.Lock()

def ensure_worker():
    global _started
    with _start_lock:
        if not _started:
            threading.Thread(target=_loop, daemon=True,
                             name="job-worker").start()
            _started = True

def wake():
    _wake.set()

def _loop():
    while True:
        job = None
        try:
            job = db.claim_next_job()
        except Exception:
            pass
        if job is None:
            _wake.wait(timeout=2.0)
            _wake.clear()
            continue
        try:
            _run(job)
        except Exception as e:                    # never kill the worker
            db.update_job(job["id"], status="error", error=str(e)[:400],
                          finished_at=time.time())
            db.add_event(job["id"], "job_error", {"error": str(e)[:400]})

def _run(job):
    jid, slug, level = job["id"], job["book_slug"], job["level"]
    baseline = bool(job["baseline"])
    pipeline.set_user(job["user_id"])       # scope library paths to the owner
    done = cached = api_calls = 0
    api_time = 0.0
    errors = []
    status = "running"

    for page in range(job["page_from"], job["page_to"] + 1):
        db.update_job(jid, current_page=page)
        ok = False
        try:
            t0 = time.time()
            if baseline:
                _, was_cached = simplify_page_baseline(slug, page, level)
            else:
                _, was_cached = simplify_book_page(slug, page, level)
            ok = True
            if was_cached:
                cached += 1
            else:
                api_calls += 1
                db.add_usage(job["user_id"], 1)   # count toward daily quota
                api_time += time.time() - t0
                time.sleep(API_GAP_S)
        except QuotaError as e:
            errors.append(f"page {page}: {e}")
            db.add_event(jid, "job_quota", {"page": page, "error": str(e)})
            status = "quota"                # stop: retrying won't help today
            break
        except Exception as e:
            errors.append(f"page {page}: {e}")
            db.add_event(jid, "page_error", {"page": page,
                                             "error": str(e)[:200]})
        done += 1
        eta = None
        if api_calls:
            per = api_time / api_calls + API_GAP_S
            eta = round((job["total"] - done) * per)
        db.update_job(jid, done=done, cached=cached, eta_s=eta,
                      error="; ".join(errors)[:400] or None)
        if ok:
            db.add_event(jid, "page_done", {"page": page,
                                            "cached": was_cached})

    if status == "running":
        try:
            gap = fill_missing_translations(slug)
            if gap.get("added"):
                db.add_event(jid, "gap_fill", gap)
        except Exception as e:
            errors.append(f"gap-fill: {e}")
            db.update_job(jid, error="; ".join(errors)[:400])
        status = "done"
        db.add_event(jid, "job_done", {"done": done, "cached": cached})
    db.update_job(jid, status=status, current_page=None, eta_s=0,
                  finished_at=time.time())
