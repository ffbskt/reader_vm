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

def test_telegram_login_owner_and_secret(monkeypatch):
    monkeypatch.setattr(auth, "TELEGRAM_BOT_SECRET", "s3cr3t")
    monkeypatch.setattr(auth, "OWNER_TG_ID", 318973541)
    bad = client.post("/auth/telegram", json={
        "tg_id": 1, "name": "x", "bot_secret": "wrong"})
    assert bad.status_code == 401
    own = client.post("/auth/telegram", json={
        "tg_id": 318973541, "name": "D", "bot_secret": "s3cr3t"}).json()
    assert own["user"]["id"] == 1
    other = client.post("/auth/telegram", json={
        "tg_id": 999, "name": "f", "bot_secret": "s3cr3t"}).json()
    assert other["user"]["id"] != 1

def test_telegram_widget_hash(monkeypatch):
    import hashlib, hmac, time as _t
    monkeypatch.setattr(auth, "TELEGRAM_TOKEN", "123:TESTTOKEN")
    data = {"id": "555", "first_name": "Tg", "username": "tguser",
            "auth_date": str(int(_t.time()))}
    check = "\n".join(f"{k}={data[k]}" for k in sorted(data))
    secret = hashlib.sha256(b"123:TESTTOKEN").digest()
    data["hash"] = hmac.new(secret, check.encode(), hashlib.sha256).hexdigest()
    r = client.post("/auth/telegram-widget", json=data).json()
    assert r["user"]["id"]                        # logged in
    bad = client.post("/auth/telegram-widget",
                      json={**data, "hash": "deadbeef"})
    assert bad.status_code == 401

def test_bad_google_token_is_401(monkeypatch):
    def boom(tok):
        raise ValueError("bad token")
    monkeypatch.setattr(auth, "verify_google_id_token", boom)
    assert client.post("/auth/google",
                       json={"id_token": "x"}).status_code == 401
