# -*- coding: utf-8 -*-
"""2c.2: shared content-addressed library + dedup + ownership."""
import json, os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import pytest, unittest.mock as m
from fastapi.testclient import TestClient
from api import auth
from api.main import app
from core import pipeline

client = TestClient(app)
BOOK = b"<<<PAGE 1>>>\nEl perro grande come en la casa vieja del bosque.\n"

@pytest.fixture(autouse=True)
def temp_site(tmp_path, monkeypatch):
    monkeypatch.setattr(pipeline, "SITE", str(tmp_path))
    pipeline.set_user(1)

def tok(sub, email):
    with m.patch.object(auth, "verify_google_id_token",
                        lambda _: {"sub": sub, "email": email, "name": email}):
        return client.post("/auth/google", json={"id_token": "x"}).json()["token"]

def uid(h):
    return client.get("/me", headers=h).json()["user"]["id"]

def test_same_book_dedup_and_reuse():
    ha = {"Authorization": "Bearer " + tok("a", "a@x.com")}
    hb = {"Authorization": "Bearer " + tok("b", "b@x.com")}

    ra = client.post("/books?name=celestina.txt", content=BOOK, headers=ha).json()
    assert ra["reused"] is False
    # simulate Alice having a cached translation for page 1 at level 0
    uid_a = client.get("/me", headers=ha).json()["user"]["id"]
    pipeline.set_user(uid_a)
    fp = pipeline.cache_file("celestina", 1, 0)
    os.makedirs(os.path.dirname(fp), exist_ok=True)
    json.dump({"page": 1, "level": 0, "sentences": [], "coverage_after": 90},
              open(fp, "w", encoding="utf-8"))

    # Bob uploads the identical text -> dedup, and sees Alice's translation
    rb = client.post("/books?name=otheredition.txt", content=BOOK,
                     headers=hb).json()
    assert rb["reused"] is True
    assert rb["existing_translations"] == 1        # Alice's page shows for Bob

    # exactly one physical copy in the library
    libs = os.listdir(os.path.join(pipeline.SITE, "library"))
    assert len(libs) == 1
    # each user still owns their own named reference
    assert len(client.get("/books", headers=ha).json()["books"]) == 1
    assert len(client.get("/books", headers=hb).json()["books"]) == 1

def test_delete_keeps_shared_content_for_other_owners():
    ha = {"Authorization": "Bearer " + tok("d", "d@x.com")}
    hb = {"Authorization": "Bearer " + tok("e", "e@x.com")}
    # both add the same book -> one shared library copy
    client.post("/books?name=shared.txt", content=BOOK, headers=ha)
    client.post("/books?name=shared.txt", content=BOOK, headers=hb)
    libdir = os.path.join(pipeline.SITE, "library")
    assert len(os.listdir(libdir)) == 1
    the_hash = os.listdir(libdir)[0]

    # give the shared book a translation (as if already produced)
    pipeline.set_user(uid(ha))
    fp = pipeline.cache_file("shared", 1, 0)
    os.makedirs(os.path.dirname(fp), exist_ok=True)
    json.dump({"page": 1, "level": 0, "sentences": [], "coverage_after": 90},
              open(fp, "w", encoding="utf-8"))

    # Alice deletes HER copy -> shared content must survive (Bob still owns it)
    r = client.delete("/books/shared", headers=ha).json()
    assert r["deleted"] is True and r["shared_removed"] is False
    assert os.path.isdir(os.path.join(libdir, the_hash))          # untouched
    # Bob still sees the book AND its translation
    assert len(client.get("/books", headers=hb).json()["books"]) == 1
    assert client.get("/books/shared/reader?level=0",
                      headers=hb).json()["pages"]                  # readable
    # Alice no longer has it
    assert client.get("/books", headers=ha).json()["books"] == []

    # Bob (the last owner) deletes -> shared content is finally freed
    r2 = client.delete("/books/shared", headers=hb).json()
    assert r2["shared_removed"] is True
    assert not os.path.exists(os.path.join(libdir, the_hash))

def test_different_text_not_deduped():
    ha = {"Authorization": "Bearer " + tok("c", "c@x.com")}
    client.post("/books?name=one.txt", content=BOOK, headers=ha)
    client.post("/books?name=two.txt",
                content=b"<<<PAGE 1>>>\nUn texto completamente distinto aqui.\n",
                headers=ha)
    assert len(os.listdir(os.path.join(pipeline.SITE, "library"))) == 2
