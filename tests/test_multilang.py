# -*- coding: utf-8 -*-
"""2e.1: multilingual dictionary model — per-language merge + lookup filter."""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from core.vocab import load_dictionary, lookup

def test_load_merges_per_language(tmp_path, monkeypatch):
    import core.vocab as v
    # word_dict with 3 languages; page vocab only en/ru for the same word
    (tmp_path / "data").mkdir()
    (tmp_path / "data" / "word_dict.json").write_text(
        '{"casa": {"en": "house", "ru": "дом", "fr": "maison"}}',
        encoding="utf-8")
    monkeypatch.setattr(v, "ROOT", str(tmp_path))
    pages = [{"vocab": [{"es": "casa", "en": "home", "ru": "дом"}]}]
    d = load_dictionary(pages)
    # page-vocab en wins (set first), fr comes from word_dict -> all 3 present
    assert d["casa"]["en"] == "home"
    assert d["casa"]["fr"] == "maison"
    assert set(d["casa"]) >= {"en", "ru", "fr"}

def test_lookup_langs_filter():
    d = {"casa": {"en": "house", "ru": "dom", "fr": "maison"}}
    assert set(lookup(d, "casa")) == {"en", "ru", "fr"}
    assert lookup(d, "casa", ["fr"]) == {"fr": "maison"}
    assert set(lookup(d, "casa", ["en", "ru"])) == {"en", "ru"}
    # a language the entry lacks -> None (nothing to show)
    assert lookup(d, "casa", ["de"]) is None

def test_lookup_morphology_still_works():
    d = {"casa": {"en": "house", "fr": "maison"}}
    assert lookup(d, "casas", ["fr"]) == {"fr": "maison"}   # plural -> maison
