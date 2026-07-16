# -*- coding: utf-8 -*-
"""2b.2: per-user storage isolation + 100 MB quota."""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import pytest
from fastapi.testclient import TestClient
from api import auth, db
from api.main import app
from core import pipeline

client = TestClient(app)

@pytest.fixture(autouse=True)
def temp_site(tmp_path, monkeypatch):
    monkeypatch.setattr(pipeline, "SITE", str(tmp_path))
    pipeline.set_user(1)

def token_for(sub, email):
    def fake(_):
        return {"sub": sub, "email": email, "name": email}
    import unittest.mock as m
    with m.patch.object(auth, "verify_google_id_token", fake):
        return client.post("/auth/google",
                           json={"id_token": "x"}).json()["token"]

BOOK = b"<<<PAGE 1>>>\nEl perro grande come en la casa vieja del bosque.\n"

def uid(h):
    return client.get("/me", headers=h).json()["user"]["id"]

def test_two_users_libraries_dont_mix():
    a = token_for("ga", "a@example.com")
    b = token_for("gb", "b@example.com")
    ha = {"Authorization": "Bearer " + a}
    hb = {"Authorization": "Bearer " + b}
    assert uid(ha) != uid(hb)

    r = client.post("/books?name=alice_book.txt", content=BOOK, headers=ha)
    assert r.status_code == 200
    # Alice sees her book; Bob sees none
    assert len(client.get("/books", headers=ha).json()["books"]) == 1
    assert client.get("/books", headers=hb).json()["books"] == []
    # Bob cannot read Alice's book
    assert client.get("/books/alice_book/stats",
                      headers=hb).status_code == 404
    # files physically live under Alice's user root, not Bob's
    assert os.path.isdir(os.path.join(pipeline.user_root(uid(ha)), "books"))
    assert not os.path.exists(os.path.join(pipeline.user_root(uid(hb)),
                                           "books", "alice_book"))

def test_storage_limit_enforced(monkeypatch):
    monkeypatch.setattr(pipeline, "STORAGE_LIMIT", 2000)   # tiny for the test
    a = token_for("gc", "c@example.com")
    ha = {"Authorization": "Bearer " + a}
    small = client.post("/books?name=s.txt", content=BOOK, headers=ha)
    assert small.status_code == 200
    big = client.post("/books?name=big.txt", content=b"x" * 5000, headers=ha)
    assert big.status_code == 413
    assert "storage limit" in big.json()["detail"].lower()

def test_me_reports_storage():
    a = token_for("gd", "d@example.com")
    ha = {"Authorization": "Bearer " + a}
    client.post("/books?name=x.txt", content=BOOK, headers=ha)
    s = client.get("/me", headers=ha).json()["storage"]
    assert s["used"] > 0 and s["limit"] == pipeline.STORAGE_LIMIT
