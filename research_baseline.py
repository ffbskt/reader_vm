# -*- coding: utf-8 -*-
"""
Research (user-commanded): vocab-guided vs vocab-free (baseline) simplify.
Pages 41-43 of La Celestina, levels 0/25/50/75. Metrics per level:
  cov_v   coverage of the VOCAB-GUIDED text vs the learner's vocabulary
  cov_b   coverage of the BASELINE text vs the same vocabulary
  gap     cov_v - cov_b  (the value of sending the vocabulary)
  unkT    unknown word types per page (vocab / baseline)
  jac     Jaccard similarity of content-word sets of the two outputs
  len%    baseline length as % of vocab-guided length
"""
import json, os, sys, time
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from core import pipeline
from core.pipeline import simplify_book_page, simplify_page_baseline
from analyze import counted_words, fold, proper_nouns

SLUG, PAGES, LEVELS = "la_celestina", (41, 42, 43), (0, 25, 50, 75)
pipeline.set_user(1)
known = {fold(w) for w in pipeline.known_set()}
names = {fold(w) for w in proper_nouns(
    open(os.path.join(pipeline.book_dir(SLUG), "book.txt"),
         encoding="utf-8").read())}
ok = known | names

def text_of(res):
    return " ".join(s.get("simple", "") for s in res["sentences"])

def stats(res):
    ws = counted_words(text_of(res).lower())
    unk = [w for w in ws if fold(w) not in ok]
    types = {fold(w) for w in unk}
    cov = (1 - len(unk) / max(len(ws), 1)) * 100
    content = {fold(w) for w in ws if len(w) > 3}
    return cov, len(types), content, len(ws)

print("page level | cov_vocab cov_base gap | unkT_v unkT_b | jac len%")
agg = {}
for lv in LEVELS:
    rows = []
    for p in PAGES:
        v, cv = simplify_book_page(SLUG, p, lv)
        if not cv:
            time.sleep(pipeline.API_GAP_S)
        b, cb = simplify_page_baseline(SLUG, p, lv)
        if not cb:
            time.sleep(pipeline.API_GAP_S)
        cov_v, ut_v, set_v, n_v = stats(v)
        cov_b, ut_b, set_b, n_b = stats(b)
        jac = len(set_v & set_b) / max(len(set_v | set_b), 1) * 100
        rows.append((cov_v, cov_b, ut_v, ut_b, jac, n_b / max(n_v, 1) * 100))
        print(f"{p:4d} L{lv:<4d} | {cov_v:8.1f} {cov_b:8.1f} "
              f"{cov_v-cov_b:5.1f} | {ut_v:6d} {ut_b:6d} | "
              f"{jac:4.0f} {n_b/max(n_v,1)*100:4.0f}", flush=True)
    a = [sum(r[i] for r in rows) / len(rows) for i in range(6)]
    agg[lv] = a
    print(f"  avg L{lv}  | {a[0]:8.1f} {a[1]:8.1f} {a[0]-a[1]:5.1f} | "
          f"{a[2]:6.1f} {a[3]:6.1f} | {a[4]:4.0f} {a[5]:4.0f}", flush=True)

print("\nSUMMARY (avg over pages)")
print("level | learner-coverage vocab | baseline | gap (value of vocab)")
for lv, a in agg.items():
    print(f"L{lv:<4d} | {a[0]:21.1f} | {a[1]:8.1f} | {a[0]-a[1]:5.1f} pp")
