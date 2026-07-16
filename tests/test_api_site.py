# -*- coding: utf-8 -*-
"""API known/books/stats endpoints against an isolated temp data dir."""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import pytest
from fastapi.testclient import TestClient
from api.main import app
from core import pipeline

client = TestClient(app)

KNOWN_TXT = ("hola casa perro gato comer beber grande pequeno bueno malo\n"
             * 3).encode()
BOOK_TXT = ("<<<PAGE 1>>>\nEl perro grande come en la casa vieja. "
            "La sombra misteriosa aparece.\n"
            "<<<PAGE 2>>>\nEl gato pequeno bebe. La sombra desaparece "
            "lentamente entre arboles antiguos.\n").encode()

@pytest.fixture(autouse=True)
def temp_site(tmp_path, monkeypatch):
    monkeypatch.setattr(pipeline, "SITE", str(tmp_path))
    pipeline.set_user(1)

def test_known_upload_list_delete():
    r = client.post("/known?name=mywords.txt", content=KNOWN_TXT)
    assert r.status_code == 200
    assert r.json()["total_known"] >= 8
    slug = r.json()["sources"][0]["slug"] if "slug" in str(r.json()) else \
        pipeline.list_known()[0]["slug"]
    assert client.get("/known").json()["sources"]
    r = client.delete(f"/known/{slug}")
    assert r.json()["deleted"] is True
    assert r.json()["total_known"] == 0

def test_book_upload_and_stats():
    client.post("/known?name=mywords.txt", content=KNOWN_TXT)
    r = client.post("/books?name=mybook.txt", content=BOOK_TXT)
    assert r.status_code == 200
    slug = r.json()["slug"]
    assert r.json()["pages"] == 2
    st = client.get(f"/books/{slug}/stats").json()
    assert st["pages"] == 2
    assert st["unknown_types"] > 0
    assert [l["level"] for l in st["levels"]] == [0, 25, 50, 75]
    assert len(client.get("/books").json()["books"]) == 1

def test_stats_unknown_book_404():
    assert client.get("/books/nope/stats").status_code == 404

def test_upload_empty_body_400():
    assert client.post("/books?name=x.txt", content=b"").status_code == 400
