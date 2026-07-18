# -*- coding: utf-8 -*-
"""
Research (user-commanded): 3 ways to reduce unknown types at level 0.
  exp1 refine  — 2nd Gemini pass, 'solve the puzzle: eliminate these words'
  exp2 cosine  — embedding-nearest known word per unknown -> substitution
                 table -> Gemini applies it with grammar fixes
  exp3 helpers — greedy top-15 residual unknowns become 'helper words' the
                 learner agrees to learn; rewrite allowing vocab+helpers
Baseline for comparison: current vocab-guided L0 cache (unkT ~36/page).
Outputs cached as page<N>_L0_exp<K>.json — reruns are free.
"""
import json, math, os, re, sys, time
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from collections import Counter
from core import pipeline
from analyze import counted_words, fold, proper_nouns, read_api_key
from simplify_page import MODELS

SLUG, PAGES, LV = "la_celestina", (41, 42, 43), 0
pipeline.set_user(1)
KEY = read_api_key()
known = {fold(w) for w in pipeline.known_set()}
names = {fold(w) for w in proper_nouns(open(os.path.join(
    pipeline.book_dir(SLUG), "book.txt"), encoding="utf-8").read())}
OK = known | names
import requests

def gen(prompt):
    for model in MODELS:
        try:
            r = requests.post(
                f"https://generativelanguage.googleapis.com/v1beta/models/"
                f"{model}:generateContent?key={KEY}",
                json={"contents": [{"parts": [{"text": prompt}]}],
                      "generationConfig": {"temperature": 0.2}}, timeout=180)
            if r.status_code in (404, 429):
                if r.status_code == 429:
                    raise RuntimeError("429 quota")
                continue
            r.raise_for_status()
            t = r.json()["candidates"][0]["content"]["parts"][0]["text"]
            return json.loads(re.sub(r"^```(json)?|```$", "",
                                     t.strip(), flags=re.M).strip())
        except RuntimeError:
            raise
        except Exception as e:
            err = e
    raise RuntimeError(f"all models failed: {err}")

def score(sentences):
    txt = " ".join(s.get("simple", "") for s in sentences)
    ws = counted_words(txt.lower())
    unk = [w for w in ws if fold(w) not in OK]
    return {"cov": (1 - len(unk) / max(len(ws), 1)) * 100,
            "unkT": len({fold(w) for w in unk}), "len": len(ws),
            "unk_types": sorted({fold(w) for w in unk})}

def cache(page, tag, make):
    fp = os.path.join(pipeline.book_dir(SLUG), "simplified",
                      f"page{page}_L{LV}_{tag}.json")
    if os.path.exists(fp):
        return json.load(open(fp, encoding="utf-8"))
    res = make()
    json.dump(res, open(fp, "w", encoding="utf-8"), ensure_ascii=False)
    time.sleep(pipeline.API_GAP_S)
    return res

def base_result(page):
    return json.load(open(os.path.join(pipeline.book_dir(SLUG), "simplified",
                     f"page{page}_L{LV}.json"), encoding="utf-8"))

def sent_text(res):
    return "\n".join((s.get("speaker", "") + "| " if s.get("speaker") else "")
                     + s.get("simple", "") for s in res["sentences"])

KNOWN_STR = " ".join(sorted(known))
JSONSHAPE = ('Reply ONLY with JSON: {"sentences": [{"speaker": "X or empty", '
             '"simple": "sentence"}]}  Keep the same number of ideas — do '
             'NOT shorten or drop content.')

# ---- exp1: puzzle refine ----
def exp1(page):
    r = base_result(page)
    bad = score(r["sentences"])["unk_types"]
    p = (f"PUZZLE: the text below must contain ONLY words from this "
         f"vocabulary (any inflected form is fine, proper names too):\n"
         f"{KNOWN_STR}\n\nThese words BREAK the rule: {' '.join(bad)}\n"
         f"Rewrite each line to eliminate every rule-breaking word — replace "
         f"it with vocabulary words, or explain the idea with several simple "
         f"vocabulary words. Meaning must survive. {JSONSHAPE}\n\nTEXT "
         f"(speaker| sentence):\n{sent_text(r)}")
    return {"sentences": gen(p)["sentences"]}

# ---- exp2: embedding cosine substitutions ----
EMB_MODEL = "gemini-embedding-001"
def embed(words):
    out = []
    for i in range(0, len(words), 100):
        chunk = words[i:i + 100]
        r = requests.post(
            f"https://generativelanguage.googleapis.com/v1beta/models/"
            f"{EMB_MODEL}:batchEmbedContents?key={KEY}",
            json={"requests": [{"model": f"models/{EMB_MODEL}",
                  "content": {"parts": [{"text": w}]},
                  "outputDimensionality": 256} for w in chunk]}, timeout=120)
        r.raise_for_status()
        out += [e["values"] for e in r.json()["embeddings"]]
    return out

def cos(a, b):
    d = sum(x * y for x, y in zip(a, b))
    return d / (math.sqrt(sum(x * x for x in a)) *
                math.sqrt(sum(x * x for x in b)) + 1e-9)

_kemb = None
def known_emb():
    global _kemb
    fp = os.path.join(pipeline.SITE, "emb_known.json")
    if _kemb is None:
        if os.path.exists(fp):
            _kemb = json.load(open(fp))
        else:
            ws = sorted(known)
            _kemb = dict(zip(ws, embed(ws)))
            json.dump(_kemb, open(fp, "w"))
    return _kemb

def exp2(page):
    r = base_result(page)
    bad = score(r["sentences"])["unk_types"]
    ke = known_emb()
    kws = list(ke.keys())
    be = embed(bad)
    table = []
    for w, e in zip(bad, be):
        best = max(kws, key=lambda k: cos(e, ke[k]))
        table.append(f"{w} -> {best}")
    p = (f"Apply these word substitutions to the text (chosen by semantic "
         f"similarity). Adjust grammar minimally so sentences stay correct; "
         f"if a substitution makes no sense in context, paraphrase with "
         f"simple words from this vocabulary instead:\n{KNOWN_STR}\n\n"
         f"SUBSTITUTIONS:\n" + "\n".join(table) + f"\n\n{JSONSHAPE}\n\n"
         f"TEXT (speaker| sentence):\n{sent_text(r)}")
    return {"sentences": gen(p)["sentences"], "table": table}

# ---- exp3: optimal helper words ----
def helper_set(k=15):
    cnt = Counter()
    for p in PAGES:
        txt = " ".join(s.get("simple", "")
                       for s in base_result(p)["sentences"])
        for w in counted_words(txt.lower()):
            if fold(w) not in OK:
                cnt[fold(w)] += 1
    return [w for w, _ in cnt.most_common(k)]

def exp3(page, helpers):
    r = base_result(page)
    p = (f"PUZZLE: rewrite so the text uses ONLY this vocabulary (inflected "
         f"forms and proper names allowed):\n{KNOWN_STR} {' '.join(helpers)}"
         f"\n\nEvery other word must be replaced or paraphrased with allowed "
         f"words. Meaning must survive. {JSONSHAPE}\n\n"
         f"TEXT (speaker| sentence):\n{sent_text(r)}")
    return {"sentences": gen(p)["sentences"]}

helpers = helper_set()
print("helper words (exp3, learner adds 15):", " ".join(helpers), flush=True)
print(f"\n{'':8s} {'cov%':>6s} {'unkT':>5s} {'len':>5s}")
tot = {}
for p in PAGES:
    rows = [("base", score(base_result(p)["sentences"]))]
    rows.append(("exp1", score(cache(p, "exp1", lambda: exp1(p))["sentences"])))
    rows.append(("exp2", score(cache(p, "exp2", lambda: exp2(p))["sentences"])))
    e3 = score(cache(p, "exp3", lambda: exp3(p, helpers))["sentences"])
    hs = set(helpers)
    e3["unkT_after_learn"] = len([w for w in e3["unk_types"] if w not in hs])
    rows.append(("exp3", e3))
    print(f"page {p}:", flush=True)
    for name, s in rows:
        extra = f"  (after learning 15: {s['unkT_after_learn']})" \
            if "unkT_after_learn" in s else ""
        print(f"  {name:6s} {s['cov']:6.1f} {s['unkT']:5d} {s['len']:5d}"
              f"{extra}", flush=True)
        tot.setdefault(name, []).append(
            (s["cov"], s["unkT"], s["len"],
             s.get("unkT_after_learn", s["unkT"])))
print("\nAVERAGES (3 pages)")
for name, v in tot.items():
    n = len(v)
    print(f"  {name:6s} cov {sum(x[0] for x in v)/n:5.1f}%  "
          f"unkT {sum(x[1] for x in v)/n:5.1f}  "
          f"len {sum(x[2] for x in v)/n:5.0f}  "
          f"unkT_after_learn {sum(x[3] for x in v)/n:5.1f}")
