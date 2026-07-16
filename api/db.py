# -*- coding: utf-8 -*-
"""
SQLite state: jobs + job_events (ARCHITECTURE.md §3). The DB lives next to
the user data (data/site/app.db) and its path follows core.pipeline.SITE so
tests run against a temp dir. Each call opens its own connection —
worker thread and request handlers never share one.
"""
import os, sys

if os.name == "nt":                      # Anaconda quirk: sqlite3.dll lives
    _lib = os.path.join(sys.exec_prefix, "Library", "bin")   # here, off the
    if os.path.isdir(_lib):                                  # DLL path when
        os.environ["PATH"] = _lib + os.pathsep + \
            os.environ.get("PATH", "")                       # not activated

import json, sqlite3, time

from core import pipeline

SCHEMA = """
CREATE TABLE IF NOT EXISTS jobs(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  user_id INTEGER NOT NULL,
  book_slug TEXT NOT NULL,
  level INTEGER NOT NULL,
  page_from INTEGER NOT NULL,
  page_to INTEGER NOT NULL,
  status TEXT NOT NULL DEFAULT 'queued',
  done INTEGER NOT NULL DEFAULT 0,
  total INTEGER NOT NULL,
  cached INTEGER NOT NULL DEFAULT 0,
  current_page INTEGER,
  eta_s INTEGER,
  error TEXT,
  created_at REAL NOT NULL,
  started_at REAL,
  finished_at REAL
);
CREATE TABLE IF NOT EXISTS job_events(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  job_id INTEGER NOT NULL,
  ts REAL NOT NULL,
  type TEXT NOT NULL,
  payload TEXT
);
CREATE INDEX IF NOT EXISTS idx_events_job ON job_events(job_id);
"""

def get_db():
    os.makedirs(pipeline.SITE, exist_ok=True)
    con = sqlite3.connect(os.path.join(pipeline.SITE, "app.db"), timeout=30)
    con.row_factory = sqlite3.Row
    con.executescript(SCHEMA)
    return con

def create_job(user_id, book_slug, level, page_from, page_to):
    with get_db() as con:
        cur = con.execute(
            "INSERT INTO jobs(user_id, book_slug, level, page_from, page_to,"
            " total, created_at) VALUES(?,?,?,?,?,?,?)",
            (user_id, book_slug, level, page_from, page_to,
             page_to - page_from + 1, time.time()))
        return cur.lastrowid

def claim_next_job():
    """Atomically take the oldest queued job (the input queue, FIFO)."""
    with get_db() as con:
        row = con.execute("SELECT * FROM jobs WHERE status='queued' "
                          "ORDER BY id LIMIT 1").fetchone()
        if row is None:
            return None
        hit = con.execute(
            "UPDATE jobs SET status='running', started_at=? "
            "WHERE id=? AND status='queued'", (time.time(), row["id"]))
        return dict(row) if hit.rowcount else None

def update_job(job_id, **fields):
    keys = ", ".join(f"{k}=?" for k in fields)
    with get_db() as con:
        con.execute(f"UPDATE jobs SET {keys} WHERE id=?",
                    (*fields.values(), job_id))

def add_event(job_id, etype, payload=None):
    with get_db() as con:
        con.execute("INSERT INTO job_events(job_id, ts, type, payload) "
                    "VALUES(?,?,?,?)",
                    (job_id, time.time(), etype,
                     json.dumps(payload, ensure_ascii=False)
                     if payload is not None else None))

def get_job(job_id, events_limit=20):
    with get_db() as con:
        row = con.execute("SELECT * FROM jobs WHERE id=?",
                          (job_id,)).fetchone()
        if row is None:
            return None
        job = dict(row)
        job["pct"] = round(job["done"] / job["total"] * 100) \
            if job["total"] else 0
        job["events"] = [
            {"ts": e["ts"], "type": e["type"],
             "payload": json.loads(e["payload"]) if e["payload"] else None}
            for e in con.execute(
                "SELECT * FROM job_events WHERE job_id=? "
                "ORDER BY id DESC LIMIT ?", (job_id, events_limit))]
        return job

def latest_job_for(book_slug, user_id):
    with get_db() as con:
        row = con.execute(
            "SELECT id FROM jobs WHERE book_slug=? AND user_id=? "
            "ORDER BY id DESC LIMIT 1", (book_slug, user_id)).fetchone()
        return get_job(row["id"]) if row else None

def active_job_for(book_slug, level, user_id):
    with get_db() as con:
        row = con.execute(
            "SELECT id FROM jobs WHERE book_slug=? AND level=? AND user_id=?"
            " AND status IN ('queued','running') LIMIT 1",
            (book_slug, level, user_id)).fetchone()
        return row["id"] if row else None
