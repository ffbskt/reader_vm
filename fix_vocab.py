# -*- coding: utf-8 -*-
"""
2c.1 one-time maintenance: remove English-classified words from every stored
known-vocab source, then rescore every cached simplified page against the
cleaned vocabulary (unknown_after per sentence, coverage_after). Local only,
no API calls. Run on dev PC and inside the api container on the VM.
"""
import glob, json, os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from analyze import classify_language, counted_words, fold, proper_nouns
from core import pipeline

users_root = os.path.join(pipeline.SITE, "users")
for udir in sorted(glob.glob(os.path.join(users_root, "*"))):
    uid = os.path.basename(udir)
    # 1) clean known sources
    for fp in glob.glob(os.path.join(udir, "known", "*.json")):
        src = json.load(open(fp, encoding="utf-8"))
        before = len(src["words"])
        src["words"] = sorted(w for w in src["words"]
                              if classify_language(w) == "es")
        src["count"] = len(src["words"])
        json.dump(src, open(fp, "w", encoding="utf-8"), ensure_ascii=False)
        print(f"user {uid} {src['name']}: {before} -> {src['count']} words "
              f"(-{before - src['count']} English)")
    # 2) rescore cached pages of this user's books
    pipeline.set_user(int(uid))
    known = {fold(w) for w in pipeline.known_set()}
    for bdir in glob.glob(os.path.join(udir, "books", "*")):
        try:
            book_txt = open(os.path.join(bdir, "book.txt"),
                            encoding="utf-8").read()
        except OSError:
            continue
        ok = known | {fold(w) for w in proper_nouns(book_txt)}
        n = 0
        for pf in glob.glob(os.path.join(bdir, "simplified", "*.json")):
            r = json.load(open(pf, encoding="utf-8"))
            if "sentences" not in r:
                continue
            allw = []
            for s in r["sentences"]:
                ws = counted_words(s.get("simple", "").lower())
                s["unknown_after"] = sorted({w for w in ws
                                             if fold(w) not in ok})
                allw += ws
            if allw:
                r["coverage_after"] = round(
                    (1 - sum(1 for w in allw if fold(w) not in ok)
                     / len(allw)) * 100)
            r["unknown_after_types"] = len(
                {fold(w) for s in r["sentences"]
                 for w in s.get("unknown_after", [])})
            json.dump(r, open(pf, "w", encoding="utf-8"), ensure_ascii=False,
                      indent=1)
            n += 1
        print(f"user {uid} {os.path.basename(bdir)}: rescored {n} pages")
print("done")
