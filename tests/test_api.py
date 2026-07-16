# -*- coding: utf-8 -*-
"""api skeleton: health, auth stub, OpenAPI docs."""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from fastapi.testclient import TestClient
from api.main import app

client = TestClient(app)

def test_health():
    r = client.get("/health")
    assert r.status_code == 200
    assert r.json()["status"] == "ok"

def test_me_without_token_is_local_user():
    r = client.get("/me")
    assert r.status_code == 200
    assert r.json()["user"]["id"] == 1

def test_me_with_garbage_bearer_token_is_401():
    r = client.get("/me", headers={"Authorization": "Bearer x"})
    assert r.status_code == 401

def test_me_with_malformed_header_is_401():
    r = client.get("/me", headers={"Authorization": "whatever"})
    assert r.status_code == 401

def test_openapi_docs():
    assert client.get("/openapi.json").status_code == 200
    assert "swagger" in client.get("/docs").text.lower()
