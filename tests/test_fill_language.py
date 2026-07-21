# -*- coding: utf-8 -*-
"""2e.2b: lazy multilingual gap-fill — word selection, caching, no re-spend."""
import json, os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import pytest
from core import pipeline

PAGE_GUIDED = {"page": 1, "level": 0, "vocab": [],
               "sentences": [{"simple": "el perro come pan",
                              "unknown_after": ["perro", "pan"]}]}
PAGE_BASELINE = {"page": 2, "level": 0, "method": "baseline", "vocab": [],
                 "sentences": [{"simple": "la casa vieja tiene ventanas"}]}

@pytest.fixture
def book(tmp_path, monkeypatch):
    monkeypatch.setattr(pipeline, "SITE", str(tmp_path))
    pipeline.set_user(1)
    lib = tmp_path / "library" / "h1" / "simplified"
    lib.mkdir(parents=True)
    (tmp_path / "library" / "h1" / "meta.json").write_text(
        '{"hash":"h1","title":"T","pages":2}', encoding="utf-8")
    json.dump(PAGE_GUIDED, open(lib / "page1_L0.json", "w", encoding="utf-8"))
    json.dump(PAGE_BASELINE, open(lib / "page2_L0_base.json", "w",
                                  encoding="utf-8"))
    bd = tmp_path / "users" / "1" / "books" / "b"
    bd.mkdir(parents=True)
    (bd / "ref.json").write_text('{"hash":"h1","slug":"b","title":"T"}',
                                 encoding="utf-8")
    return "b"

def test_words_from_guided_and_baseline_pages(book):
    words = pipeline.words_needing_language(book, "fr")
    # guided page contributes its unknown_after; baseline page its own words
    assert "perro" in words and "pan" in words
    assert "ventanas" in words or "vieja" in words

def test_fill_language_caches_and_skips_existing(book, monkeypatch):
    calls = []
    def fake(prompt, timeout=120):
        calls.append(prompt)
        return [{"w": "perro", "t": "chien"}, {"w": "pan", "t": "pain"}]
    monkeypatch.setattr(pipeline, "_gemini_json", fake)
    monkeypatch.setattr(pipeline.time, "sleep", lambda s: None)

    r = pipeline.fill_language(book, "fr", max_batches=1)
    assert r["added"] == 2 and r["requests"] == 1
    wd = json.load(open(os.path.join(pipeline.book_dir(book),
                                     "word_dict.json"), encoding="utf-8"))
    assert wd["perro"]["fr"] == "chien"

    # those two words are now covered -> not requested again
    assert "perro" not in pipeline.words_needing_language(book, "fr")
    # a different language is still missing
    assert "perro" in pipeline.words_needing_language(book, "ru")

def test_unsupported_language_rejected(book):
    with pytest.raises(ValueError):
        pipeline.fill_language(book, "xx")

def test_book_languages_counts(book, monkeypatch):
    monkeypatch.setattr(pipeline, "_gemini_json",
                        lambda p, timeout=120: [{"w": "perro", "t": "chien"}])
    monkeypatch.setattr(pipeline.time, "sleep", lambda s: None)
    pipeline.fill_language(book, "fr", max_batches=1)
    assert pipeline.book_languages(book).get("fr") == 1
