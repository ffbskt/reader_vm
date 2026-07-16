# -*- coding: utf-8 -*-
"""core.vocab: morphology lookup + page vocabulary modes."""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from core.vocab import lookup, page_new_words, new_state, MODES

D = {"consolar": {"en": "to console", "ru": "утешать"},
     "matar":    {"en": "to kill",    "ru": "убивать"},
     "perder":   {"en": "to lose",    "ru": "терять"},
     "calentar": {"en": "to heat",    "ru": "греть"},
     "crecer":   {"en": "to grow",    "ru": "расти"},
     "cuerpo":   {"en": "body",       "ru": "тело"},
     "oigas":    {"en": "you hear",   "ru": "слышишь"}}

def test_exact_and_accents():
    assert lookup(D, "matar")["en"] == "to kill"
    assert lookup(D, "MATAR")["en"] == "to kill"

def test_attached_pronoun():
    assert lookup(D, "consolarle")["en"] == "to console"
    assert lookup(D, "matarse")["en"] == "to kill"

def test_conjugation_to_infinitive():
    assert lookup(D, "mataran")["en"] == "to kill"
    assert lookup(D, "crecen")["en"] == "to grow"

def test_stem_change():
    assert lookup(D, "pierda")["en"] == "to lose"     # ie -> e
    assert lookup(D, "calienta")["en"] == "to heat"   # ie -> e

def test_plural_and_exact_inflected_key():
    assert lookup(D, "cuerpos")["en"] == "body"
    assert lookup(D, "oigas")["en"] == "you hear"     # gap-fill keys as-is

def test_miss_returns_none():
    assert lookup(D, "zzz") is None
    assert lookup(D, "abc") is None

def page(words):
    return {"sentences": [{"unknown_after": words}]}

def test_vocab_alphabetical_and_repeat_flag():
    st = new_state()
    v1 = page_new_words(page(["matar", "crecer", "cuerpo"]), D, st,
                        mode="repeat", page_index=0)
    assert [e["es"] for e in v1] == ["crecer", "cuerpo", "matar"]
    v2 = page_new_words(page(["matar"]), D, st, mode="repeat", page_index=1)
    assert v2[0]["repeat"] is True

def test_norepeat_lists_word_once():
    st = new_state()
    page_new_words(page(["matar"]), D, st, mode="norepeat", page_index=0)
    v2 = page_new_words(page(["matar"]), D, st, mode="norepeat", page_index=1)
    assert v2 == []

def test_spaced_growing_gaps():
    st = new_state()
    assert page_new_words(page(["matar"]), D, st, "spaced", 0)   # shown
    assert not page_new_words(page(["matar"]), D, st, "spaced", 1)  # gap<2
    assert page_new_words(page(["matar"]), D, st, "spaced", 2)   # gap ok

def test_clean_mode_empty():
    assert page_new_words(page(["matar"]), D, new_state(), "clean", 0) == []
    assert set(MODES) == {"repeat", "norepeat", "spaced", "clean"}
