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

import contextlib, json, sqlite3, time

from core import pipeline

_initialized = set()      # db paths whose schema has been created

SCHEMA = """
CREATE TABLE IF NOT EXISTS users(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  created_at REAL NOT NULL,
  tier TEXT NOT NULL DEFAULT 'free',
  email TEXT,
  google_sub TEXT UNIQUE,
  tg_id INTEGER UNIQUE,
  name TEXT
);
INSERT OR IGNORE INTO users(id, created_at, tier, name)
  VALUES(1, 0, 'free', 'local');
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
  baseline INTEGER NOT NULL DEFAULT 0,
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
CREATE TABLE IF NOT EXISTS usage(
  user_id INTEGER NOT NULL,
  day TEXT NOT NULL,
  pages INTEGER NOT NULL DEFAULT 0,
  PRIMARY KEY(user_id, day)
);
CREATE TABLE IF NOT EXISTS user_vocab(
  user_id INTEGER NOT NULL,
  lang TEXT NOT NULL,
  word TEXT NOT NULL,
  state TEXT NOT NULL DEFAULT 'learning',   -- learning | known
  ts REAL NOT NULL,
  PRIMARY KEY(user_id, lang, word)
);
CREATE INDEX IF NOT EXISTS idx_vocab_user ON user_vocab(user_id, lang, state);
"""

def get_db():
    os.makedirs(pipeline.SITE, exist_ok=True)
    path = os.path.join(pipeline.SITE, "app.db")
    con = sqlite3.connect(path, timeout=30)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("PRAGMA busy_timeout=30000")
    if path not in _initialized:
        con.executescript(SCHEMA)
        # additive migrations for DBs created before a column existed
        for col, ddl in [("baseline",
                          "ALTER TABLE jobs ADD COLUMN baseline "
                          "INTEGER NOT NULL DEFAULT 0"),
                         ("anon",
                          "ALTER TABLE usage ADD COLUMN anon "
                          "INTEGER NOT NULL DEFAULT 0")]:
            try:
                con.execute(ddl)
            except sqlite3.OperationalError:
                pass                 # already present
        _initialized.add(path)
    return con

def create_job(user_id, book_slug, level, page_from, page_to, baseline=False):
    with contextlib.closing(get_db()) as con, con:
        cur = con.execute(
            "INSERT INTO jobs(user_id, book_slug, level, page_from, page_to,"
            " total, baseline, created_at) VALUES(?,?,?,?,?,?,?,?)",
            (user_id, book_slug, level, page_from, page_to,
             page_to - page_from + 1, 1 if baseline else 0, time.time()))
        return cur.lastrowid

def claim_next_job():
    """Atomically take the oldest queued job (the input queue, FIFO)."""
    with contextlib.closing(get_db()) as con, con:
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
    with contextlib.closing(get_db()) as con, con:
        con.execute(f"UPDATE jobs SET {keys} WHERE id=?",
                    (*fields.values(), job_id))

def add_event(job_id, etype, payload=None):
    with contextlib.closing(get_db()) as con, con:
        con.execute("INSERT INTO job_events(job_id, ts, type, payload) "
                    "VALUES(?,?,?,?)",
                    (job_id, time.time(), etype,
                     json.dumps(payload, ensure_ascii=False)
                     if payload is not None else None))

def get_job(job_id, events_limit=20):
    with contextlib.closing(get_db()) as con, con:
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
    with contextlib.closing(get_db()) as con, con:
        row = con.execute(
            "SELECT id FROM jobs WHERE book_slug=? AND user_id=? "
            "ORDER BY id DESC LIMIT 1", (book_slug, user_id)).fetchone()
        return get_job(row["id"]) if row else None

def get_user(user_id):
    with contextlib.closing(get_db()) as con, con:
        row = con.execute("SELECT * FROM users WHERE id=?",
                          (user_id,)).fetchone()
        return dict(row) if row else None

def user_by_google_sub(sub):
    with contextlib.closing(get_db()) as con, con:
        row = con.execute("SELECT * FROM users WHERE google_sub=?",
                          (sub,)).fetchone()
        return dict(row) if row else None

def attach_google(user_id, sub, email, name):
    with contextlib.closing(get_db()) as con, con:
        con.execute("UPDATE users SET google_sub=?, email=?, name=? "
                    "WHERE id=?", (sub, email, name, user_id))
    return get_user(user_id)

def user_by_tg_id(tg_id):
    with contextlib.closing(get_db()) as con, con:
        row = con.execute("SELECT * FROM users WHERE tg_id=?",
                          (tg_id,)).fetchone()
        return dict(row) if row else None

def attach_tg(user_id, tg_id, name):
    with contextlib.closing(get_db()) as con, con:
        con.execute("UPDATE users SET tg_id=?, name=COALESCE(name,?) "
                    "WHERE id=?", (tg_id, name, user_id))
    return get_user(user_id)

def create_tg_user(tg_id, name):
    with contextlib.closing(get_db()) as con, con:
        cur = con.execute("INSERT INTO users(created_at, tg_id, name) "
                          "VALUES(?,?,?)", (time.time(), tg_id, name))
        uid = cur.lastrowid
    return get_user(uid)     # outside the write txn: no self-deadlock

def create_google_user(sub, email, name):
    with contextlib.closing(get_db()) as con, con:
        cur = con.execute(
            "INSERT INTO users(created_at, email, google_sub, name) "
            "VALUES(?,?,?,?)", (time.time(), email, sub, name))
        uid = cur.lastrowid
    return get_user(uid)     # outside the write txn: no self-deadlock

def active_job_for(book_slug, level, user_id):
    with contextlib.closing(get_db()) as con, con:
        row = con.execute(
            "SELECT id FROM jobs WHERE book_slug=? AND level=? AND user_id=?"
            " AND status IN ('queued','running') LIMIT 1",
            (book_slug, level, user_id)).fetchone()
        return row["id"] if row else None

def running_jobs_for_user(user_id):
    with contextlib.closing(get_db()) as con, con:
        return con.execute(
            "SELECT COUNT(*) c FROM jobs WHERE user_id=? AND "
            "status IN ('queued','running')", (user_id,)).fetchone()["c"]

def usage_today(user_id):
    day = time.strftime("%Y-%m-%d")
    with contextlib.closing(get_db()) as con, con:
        row = con.execute("SELECT pages FROM usage WHERE user_id=? AND day=?",
                          (user_id, day)).fetchone()
        return row["pages"] if row else 0

def vocab_add(user_id, lang, words, state="learning"):
    """Add words in a language at a state. 'known' always wins over
    'learning' (promotion is one-way); returns how many rows changed."""
    now = time.time()
    n = 0
    with contextlib.closing(get_db()) as con, con:
        for w in words:
            w = w.strip().lower()
            if not w:
                continue
            cur = con.execute(
                "INSERT INTO user_vocab(user_id, lang, word, state, ts) "
                "VALUES(?,?,?,?,?) ON CONFLICT(user_id, lang, word) DO UPDATE "
                "SET state=CASE WHEN user_vocab.state='known' THEN 'known' "
                "ELSE excluded.state END",
                (user_id, lang, w, state, now))
            n += cur.rowcount
    return n

def vocab_counts(user_id, lang):
    with contextlib.closing(get_db()) as con, con:
        rows = con.execute(
            "SELECT state, COUNT(*) c FROM user_vocab WHERE user_id=? AND "
            "lang=? GROUP BY state", (user_id, lang)).fetchall()
    d = {r["state"]: r["c"] for r in rows}
    return {"known": d.get("known", 0), "learning": d.get("learning", 0)}

def vocab_words(user_id, lang, state=None, limit=5000):
    q = "SELECT word FROM user_vocab WHERE user_id=? AND lang=?"
    args = [user_id, lang]
    if state:
        q += " AND state=?"
        args.append(state)
    q += " ORDER BY ts DESC LIMIT ?"
    args.append(limit)
    with contextlib.closing(get_db()) as con, con:
        return [r["word"] for r in con.execute(q, args)]

def add_usage(user_id, pages):
    day = time.strftime("%Y-%m-%d")
    with contextlib.closing(get_db()) as con, con:
        con.execute(
            "INSERT INTO usage(user_id, day, pages) VALUES(?,?,?) "
            "ON CONFLICT(user_id, day) DO UPDATE SET pages = pages + ?",
            (user_id, day, pages, pages))

def anon_today(user_id):
    day = time.strftime("%Y-%m-%d")
    with contextlib.closing(get_db()) as con, con:
        row = con.execute("SELECT anon FROM usage WHERE user_id=? AND day=?",
                          (user_id, day)).fetchone()
        return row["anon"] if row else 0

def add_anon_usage(user_id, n):
    day = time.strftime("%Y-%m-%d")
    with contextlib.closing(get_db()) as con, con:
        con.execute(
            "INSERT INTO usage(user_id, day, anon) VALUES(?,?,?) "
            "ON CONFLICT(user_id, day) DO UPDATE SET anon = anon + ?",
            (user_id, day, n, n))
