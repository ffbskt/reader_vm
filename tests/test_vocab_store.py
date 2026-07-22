# -*- coding: utf-8 -*-
"""2g.1: personal vocabulary store — add, promote, per-user/lang isolation."""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import pytest, unittest.mock as m
from fastapi.testclient import TestClient
from api import auth
from api.main import app
from core import pipeline

client = TestClient(app)

@pytest.fixture(autouse=True)
def temp_site(tmp_path, monkeypatch):
    monkeypatch.setattr(pipeline, "SITE", str(tmp_path))

def tok(sub):
    with m.patch.object(auth, "verify_google_id_token",
                        lambda _: {"sub": sub, "email": sub + "@x", "name": sub}):
        return {"Authorization": "Bearer " +
                client.post("/auth/google", json={"id_token": "x"}).json()["token"]}

def test_add_learning_then_promote():
    h = tok("a")
    client.post("/vocab", json={"lang": "es", "words": ["casa", "perro", "gato"]},
                headers=h)
    g = client.get("/vocab?lang=es", headers=h).json()
    assert g["counts"] == {"known": 0, "learning": 3}
    assert set(g["learning"]) == {"casa", "perro", "gato"}
    # promote two -> known; learning drops
    client.post("/vocab/promote", json={"lang": "es", "words": ["casa", "perro"]},
                headers=h)
    c = client.get("/vocab?lang=es", headers=h).json()["counts"]
    assert c == {"known": 2, "learning": 1}

def test_known_is_sticky():
    h = tok("b")
    client.post("/vocab", json={"lang": "es", "words": ["sol"], "state": "known"},
                headers=h)
    # adding the same word as learning must NOT demote it
    client.post("/vocab", json={"lang": "es", "words": ["sol"]}, headers=h)
    assert client.get("/vocab?lang=es", headers=h).json()["counts"]["known"] == 1

def test_per_user_and_per_language_isolation():
    ha, hb = tok("c"), tok("d")
    client.post("/vocab", json={"lang": "es", "words": ["luna"]}, headers=ha)
    client.post("/vocab", json={"lang": "en", "words": ["moon"]}, headers=ha)
    assert client.get("/vocab?lang=es", headers=hb).json()["counts"]["learning"] == 0
    assert client.get("/vocab?lang=en", headers=ha).json()["counts"]["learning"] == 1
    assert client.get("/vocab?lang=es", headers=ha).json()["counts"]["learning"] == 1

def test_bad_state_rejected():
    assert client.post("/vocab", json={"lang": "es", "words": ["x"],
                       "state": "mastered"}, headers=tok("e")).status_code == 400
