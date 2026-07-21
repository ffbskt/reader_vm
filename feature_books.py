# -*- coding: utf-8 -*-
"""
Mark the owner's public-domain classics as a FEATURED public shelf (every
logged-in user can read them) and ensure one baseline level-0 page per
language is translated so the logged-out /samples teaser has content.
Run inside the api container: docker compose exec -T api python feature_books.py
"""
import json, os, sys, time
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from core import pipeline
from core.pipeline import simplify_page_baseline, QuotaError

FEATURED = [
    ("alice_s_adventures_in_wonderland", "English"),
    ("grimms_fairy_tales",               "English"),
    ("andersen_s_fairy_tales",           "English"),
    ("fables_de_la_fontaine",            "French"),
    ("le_avventure_di_pinocchio",        "Italian"),
    ("la_celestina",                     "Spanish"),
]

def main():
    pipeline.set_user(1)
    entries, langs_first = [], {}
    for slug, lang in FEATURED:
        ref = pipeline._read_ref(slug)
        if not ref:
            print(f"!! no ref for {slug}, skipped")
            continue
        meta = json.load(open(os.path.join(pipeline.book_dir(slug),
                                           "meta.json"), encoding="utf-8"))
        entries.append({"slug": slug, "hash": ref["hash"],
                        "title": ref.get("title", meta["title"]),
                        "lang": lang, "pages": meta["pages"]})
        langs_first.setdefault(lang, slug)     # first book of each language
    json.dump(entries, open(pipeline._featured_path(), "w", encoding="utf-8"),
              ensure_ascii=False, indent=1)
    print(f"featured {len(entries)} books: "
          + ", ".join(e["slug"] for e in entries))

    # one baseline-L0 page per language so the samples teaser is populated
    for lang, slug in langs_first.items():
        if pipeline.cached_pages(slug, 0, baseline=True):
            print(f"{lang}: sample already translated")
            continue
        try:
            simplify_page_baseline(slug, 1, 0)
            print(f"{lang}: translated sample page 1 of {slug}")
            time.sleep(6)
        except QuotaError as e:
            print(f"{lang}: quota, stop: {e}")
            break
        except Exception as e:
            print(f"{lang}: {slug} p1 failed: {str(e)[:80]}")

if __name__ == "__main__":
    main()
