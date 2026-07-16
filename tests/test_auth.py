# -*- coding: utf-8 -*-
"""2b.1 auth: JWT roundtrip, Google login flow (verifier mocked), owner rule."""
import os, sys, time
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

def fake_verify(payload):
    return lambda tok: dict(payload)

def test_jwt_roundtrip_and_tamper():
    tok = auth.mint_jwt(42)
    assert auth.verify_jwt(tok) == 42
    assert auth.verify_jwt(tok[:-3] + "abc") is None
    assert auth.verify_jwt("garbage") is None

def test_jwt_expiry():
    tok = auth.mint_jwt(7, days=-1)
    assert auth.verify_jwt(tok) is None

def test_owner_email_claims_user_1(monkeypatch):
    monkeypatch.setattr(auth, "verify_google_id_token", fake_verify(
        {"sub": "g-owner", "email": "ffbskt@gmail.com", "name": "Denis"}))
    r = client.post("/auth/google", json={"id_token": "x"})
    assert r.status_code == 200
    d = r.json()
    assert d["user"]["id"] == 1                    # claimed the legacy user
    me = client.get("/me", headers={"Authorization": "Bearer " + d["token"]})
    assert me.json()["user"]["email"] == "ffbskt@gmail.com"

def test_other_email_becomes_new_user_and_is_stable(monkeypatch):
    monkeypatch.setattr(auth, "verify_google_id_token", fake_verify(
        {"sub": "g-friend", "email": "friend@example.com", "name": "F"}))
    a = client.post("/auth/google", json={"id_token": "x"}).json()
    b = client.post("/auth/google", json={"id_token": "x"}).json()
    assert a["user"]["id"] == b["user"]["id"] != 1

def test_bad_google_token_is_401(monkeypatch):
    def boom(tok):
        raise ValueError("bad token")
    monkeypatch.setattr(auth, "verify_google_id_token", boom)
    assert client.post("/auth/google",
                       json={"id_token": "x"}).status_code == 401
