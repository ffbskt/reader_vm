# -*- coding: utf-8 -*-
"""Daily translation quota + concurrent-job + range guards."""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import pytest
from fastapi.testclient import TestClient
from api import db, limits
from api.main import app
from core import pipeline

client = TestClient(app)

BOOK = ("<<<PAGE 1>>>\n" + "El perro grande come pan en la casa vieja. " * 4
        + "\n<<<PAGE 2>>>\n" + "La sombra camina por el bosque oscuro. " * 4
        + "\n").encode()

@pytest.fixture(autouse=True)
def temp_site(tmp_path, monkeypatch):
    monkeypatch.setattr(pipeline, "SITE", str(tmp_path))
    pipeline.set_user(1)

def make_book():
    return client.post("/books?name=quota_book.txt",
                       content=BOOK).json()["slug"]

def test_daily_page_quota_blocks(monkeypatch):
    monkeypatch.setattr(limits, "DAILY_PAGES", 1)
    slug = make_book()
    db.add_usage(1, 1)                      # already used today's 1 page
    r = client.post(f"/books/{slug}/translate",
                    json={"level": 0, "from": 1, "to": 2})
    assert r.status_code == 429
    assert "daily translation limit" in r.json()["detail"].lower()

def test_range_guard(monkeypatch):
    monkeypatch.setattr(limits, "MAX_RANGE", 1)
    slug = make_book()
    r = client.post(f"/books/{slug}/translate",
                    json={"level": 0, "from": 1, "to": 2})
    assert r.status_code == 400
    assert "range too large" in r.json()["detail"].lower()

def test_concurrent_helper():
    # no running jobs for a fresh user
    assert db.running_jobs_for_user(99999) == 0

def test_usage_counter_roundtrip():
    db.add_usage(555, 3)
    db.add_usage(555, 2)
    assert db.usage_today(555) == 5
