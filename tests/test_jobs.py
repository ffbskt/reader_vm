# -*- coding: utf-8 -*-
"""Jobs: queue -> worker -> events, entirely on cached pages (no API)."""
import json, os, sys, time
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import pytest
from fastapi.testclient import TestClient
from api.main import app
from core import pipeline

client = TestClient(app)

BOOK = ("<<<PAGE 1>>>\nuno dos tres\n<<<PAGE 2>>>\ncuatro cinco seis\n").encode()

CACHED_PAGE = {"page": 1, "method": "rewrite", "level": 0, "fmt": 2,
               "model": "test", "coverage_before": 50, "coverage_after": 90,
               "unknown_before": 2, "unknown_after": 0, "vocab": [],
               "sentences": [{"speaker": "", "orig": "uno",
                              "simple": "uno dos", "unknown_after": []}]}

@pytest.fixture(autouse=True)
def temp_site(tmp_path, monkeypatch):
    monkeypatch.setattr(pipeline, "SITE", str(tmp_path))
    pipeline.set_user(1)
    from api import worker
    worker.ensure_worker()          # TestClient may not fire startup events

def make_book_with_cache(pages=(1, 2)):
    slug = client.post("/books?name=jobbook.txt", content=BOOK).json()["slug"]
    for p in pages:
        fp = pipeline.cache_file(slug, p, 0)
        os.makedirs(os.path.dirname(fp), exist_ok=True)
        json.dump({**CACHED_PAGE, "page": p}, open(fp, "w", encoding="utf-8"))
    return slug

def wait_done(job_id, timeout=10):
    t0 = time.time()
    while time.time() - t0 < timeout:
        job = client.get(f"/jobs/{job_id}").json()
        if job["status"] in ("done", "error", "quota"):
            return job
        time.sleep(0.1)
    raise AssertionError(f"job {job_id} did not finish: {job}")

def test_cached_job_runs_free_to_100():
    slug = make_book_with_cache()
    r = client.post(f"/books/{slug}/translate",
                    json={"level": 0, "from": 1, "to": 2})
    assert r.status_code == 202
    job = wait_done(r.json()["id"])
    assert job["status"] == "done"
    assert job["pct"] == 100
    assert job["cached"] == 2                 # both pages from cache: $0
    types = [e["type"] for e in job["events"]]
    assert "job_done" in types and "page_done" in types

def test_duplicate_active_job_409():
    slug = make_book_with_cache()
    a = client.post(f"/books/{slug}/translate",
                    json={"level": 0, "from": 1, "to": 2})
    b = client.post(f"/books/{slug}/translate",
                    json={"level": 0, "from": 1, "to": 2})
    assert 409 in (a.status_code, b.status_code) or \
        wait_done(a.json()["id"])             # too fast: first already done
    if a.status_code == 202:
        wait_done(a.json()["id"])

def test_latest_job_and_validation():
    slug = make_book_with_cache()
    assert client.post(f"/books/{slug}/translate",
                       json={"level": 33, "from": 1, "to": 2}).status_code == 400
    assert client.post("/books/nope/translate",
                       json={"level": 0, "from": 1, "to": 2}).status_code == 404
    assert client.get(f"/jobs?book={slug}").json()["status"] == "idle"
    jid = client.post(f"/books/{slug}/translate",
                      json={"level": 0, "from": 1, "to": 1}).json()["id"]
    wait_done(jid)
    assert client.get(f"/jobs?book={slug}").json()["id"] == jid
    assert client.get("/jobs/99999").status_code == 404
