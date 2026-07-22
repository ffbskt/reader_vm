# -*- coding: utf-8 -*-
"""2f.1: Cyrillic + broader-Latin tokenizer, Spanish/English unchanged."""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from analyze import (WORD_RE, fold, counted_words, tokenize,
                     classify_language, is_counted)

RU = ("Я человек больной. Я злой человек. Непривлекательный я человек. "
      "Думаю, что у меня болит печень. Ёлка стоит в углу.")

def test_cyrillic_words_tokenize():
    toks = tokenize(RU)
    assert "человек" in toks and "больной" in toks
    assert len([t for t in toks if any("а" <= c <= "я" for c in t)]) > 8

def test_cyrillic_counted_words_enough_for_guard():
    # the "almost no text" guard needs >= 20 counted words on a real page
    assert len(counted_words(RU * 2)) >= 20

def test_yo_folds_to_ye():
    assert fold("Ёлка") == "елка"
    assert fold("ёж") == "еж"
    assert fold("ЧЕЛОВЕК") == "человек"

def test_classify_cyrillic_is_ru():
    assert classify_language("человек") == "ru"
    assert classify_language("casa") == "es"          # unchanged
    assert classify_language("house") == "en"         # unchanged

def test_spanish_unchanged():
    assert fold("Señora") == "senora"
    assert "señora" in tokenize("La señora vieja")
    assert is_counted("señora") and is_counted("casa")

def test_broader_latin_accents():
    # French/German/Italian accented words now tokenize as whole words
    assert "château" in tokenize("le château")
    assert "schön" in tokenize("sehr schön")
    assert is_counted("città")
