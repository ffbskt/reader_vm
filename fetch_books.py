# -*- coding: utf-8 -*-
"""
Download a curated set of PUBLIC-DOMAIN classics from Project Gutenberg into
the shared library (owner = user 1). Run inside the api container:
    docker compose exec -T api python fetch_books.py
Idempotent: re-running dedups (same text hash) and just re-links.

Public domain: all authors below died >150 years ago (or the work is
pre-1880), so copying AND making simplified/translated derivatives is free.
Latin-script only for now — the tokenizer is Spanish-tuned (Cyrillic etc.
need the generalization noted in the roadmap).
"""
import os, re, sys, urllib.request
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from core import pipeline

# (gutenberg id, display filename, url-scheme)
BOOKS = [
    (11,    "Alice's Adventures in Wonderland.txt", "cache"),   # EN, Carroll
    (2591,  "Grimms' Fairy Tales.txt",              "cache"),   # EN, Grimm
    (1597,  "Andersen's Fairy Tales.txt",           "cache"),   # EN, Andersen
    (56327, "Fables de La Fontaine.txt",            "files0"),  # FR, La Fontaine
    (52484, "Le avventure di Pinocchio.txt",        "cache"),   # IT, Collodi
]

def gutenberg_url(gid, kind):
    if kind == "files0":
        return f"https://www.gutenberg.org/files/{gid}/{gid}-0.txt"
    return f"https://www.gutenberg.org/cache/epub/{gid}/pg{gid}.txt"

def strip_boilerplate(t):
    parts = re.split(r"\*\*\* ?START OF TH[EI][S ].*?\*\*\*", t, flags=re.I | re.S)
    if len(parts) > 1:
        t = parts[1]
    t = re.split(r"\*\*\* ?END OF TH[EI][S ].*?\*\*\*", t, flags=re.I | re.S)[0]
    return t.strip()

def main():
    pipeline.set_user(1)
    for gid, name, kind in BOOKS:
        try:
            raw = urllib.request.urlopen(
                gutenberg_url(gid, kind), timeout=90).read().decode(
                "utf-8", "replace")
            text = strip_boilerplate(raw)
            if len(text) < 5000:
                print(f"!! {name}: too short ({len(text)}B), skipped")
                continue
            info = pipeline.add_book(name, text.encode("utf-8"))
            print(f"OK {name}: {info['pages']} pages, "
                  f"{'reused' if info['reused'] else 'new'}, slug={info['slug']}")
        except Exception as e:
            print(f"!! {name}: {e}")

if __name__ == "__main__":
    main()
