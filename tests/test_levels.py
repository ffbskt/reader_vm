# -*- coding: utf-8 -*-
"""core.pipeline: token-based level math + cache keying."""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from core.pipeline import _allowed_types, cache_file, LEVELS

# synthetic Zipf-ish frequencies: rank r -> ~ 1/r
UNK = {f"w{r}": max(1, 1000 // r) for r in range(1, 201)}
RANKED = sorted(UNK, key=lambda w: -UNK[w])
TOTAL = sum(UNK.values())

def cov(keep):
    return sum(UNK[w] for w in keep) / TOTAL * 100

def test_level_zero_keeps_nothing():
    assert _allowed_types(RANKED, UNK, 0) == set()

def test_levels_cover_their_token_share():
    for lv in (25, 50, 75):
        keep = _allowed_types(RANKED, UNK, lv)
        assert cov(keep) >= lv                    # target reached
        assert cov(keep) < lv + 15                # no wild overshoot

def test_levels_are_nested_and_frequency_first():
    k25 = _allowed_types(RANKED, UNK, 25)
    k50 = _allowed_types(RANKED, UNK, 50)
    k75 = _allowed_types(RANKED, UNK, 75)
    assert k25 < k50 < k75
    assert k25 == set(RANKED[:len(k25)])          # a prefix of the ranking

def test_zipf_asymmetry_types_vs_tokens():
    k50 = _allowed_types(RANKED, UNK, 50)
    assert len(k50) / len(RANKED) < 0.25          # few types, half the tokens

def test_cache_key_is_page_and_level():
    fp = cache_file("mybook", 7, 50)
    assert fp.endswith(os.path.join("mybook", "simplified", "page7_L50.json"))
    assert set(LEVELS) == {0, 25, 50, 75}
