# -*- coding: utf-8 -*-
"""
Phase 2f: download the curated EN/ES public-domain catalogue from Project
Gutenberg into the owner's shared library, then rebuild featured.json as the
3-per-language shelf with difficulty levels. fr/it/de books drop off the
shelf (they stay in the library, just not featured). RU added later (needs
the Cyrillic tokenizer, 2f.1).

Run in the api container: docker compose exec -T api python fetch_catalogue.py
"""
import json, os, re, sys, urllib.request
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from core import pipeline

# (gutenberg id, output filename -> slug, language, difficulty)
DOWNLOADS = [
    (1342, "Pride and Prejudice.txt",   "English", "medium"),
    (98,   "A Tale of Two Cities.txt",  "English", "hard"),
    (55206, "Fabulas.txt",              "Spanish", "easy"),
    (53552, "Becquer Obras escogidas.txt", "Spanish", "medium"),
    (2000, "Don Quijote.txt",           "Spanish", "hard"),
]

# the final shelf (slug, lang, level). Alice + Celestina already in library.
FEATURED = [
    ("alice_s_adventures_in_wonderland", "English", "easy"),
    ("pride_and_prejudice",              "English", "medium"),
    ("a_tale_of_two_cities",             "English", "hard"),
    ("fabulas",                          "Spanish", "easy"),
    ("becquer_obras_escogidas",          "Spanish", "medium"),
    ("don_quijote",                      "Spanish", "hard"),
    ("la_celestina",                     "Spanish", "medium"),
]

def download(gid):
    for url in (f"https://www.gutenberg.org/cache/epub/{gid}/pg{gid}.txt",
                f"https://www.gutenberg.org/files/{gid}/{gid}-0.txt"):
        try:
            t = urllib.request.urlopen(url, timeout=90).read().decode(
                "utf-8", "replace")
            if len(t) > 20000:
                return t
        except Exception:
            pass
    return None

def strip_boilerplate(t):
    m = re.search(r"\*\*\*\s*START OF TH.*?\*\*\*", t, re.S)
    if m:
        t = t[m.end():]
    m = re.search(r"\*\*\*\s*END OF TH.*?PROJECT GUTENBERG.*?\*\*\*", t, re.S)
    if m:
        t = t[:m.start()]
    return t.strip()

def main():
    pipeline.set_user(1)
    for gid, fname, lang, level in DOWNLOADS:
        slug = pipeline._slug(fname)
        if pipeline._read_ref(slug):
            print(f"{slug}: already present")
            continue
        raw = download(gid)
        if not raw:
            print(f"!! #{gid} download failed")
            continue
        info = pipeline.add_book(fname, strip_boilerplate(raw).encode("utf-8"))
        print(f"added {slug}: {info['pages']} pages ({lang}, {level})")

    entries = []
    for slug, lang, level in FEATURED:
        ref = pipeline._read_ref(slug)
        if not ref:
            print(f"!! no ref for {slug}, not featured")
            continue
        meta = json.load(open(os.path.join(pipeline.book_dir(slug),
                                            "meta.json"), encoding="utf-8"))
        entries.append({"slug": slug, "hash": ref["hash"],
                        "title": ref.get("title", meta["title"]),
                        "lang": lang, "level": level, "pages": meta["pages"]})
    json.dump(entries, open(pipeline._featured_path(), "w", encoding="utf-8"),
              ensure_ascii=False, indent=1)
    print(f"\nfeatured shelf ({len(entries)}): "
          + ", ".join(f"{e['slug']}[{e['lang'][:2]}/{e['level']}]"
                      for e in entries))

if __name__ == "__main__":
    main()
