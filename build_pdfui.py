# -*- coding: utf-8 -*-
"""
Build pdf.html — the PDF-builder page: choose the vocabulary repeat mode
(1 repeat / 2 norepeat / 3 spaced), see per-page unknown-word percentages,
and download the PDF (built live by server.py). No API calls.
"""
import sys, os, json, argparse

sys.stdout.reconfigure(encoding="utf-8")
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from vocab_common import load_dictionary, page_new_words, new_state, MODES

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--from", dest="p_from", type=int, default=41)
    ap.add_argument("--to", dest="p_to", type=int, default=90)
    args = ap.parse_args()

    results = []
    for page in range(args.p_from, args.p_to + 1):
        fp = os.path.join(HERE, "data", "simplified",
                          f"page{page}_rewrite_100.json")
        if os.path.exists(fp):
            results.append(json.load(open(fp, encoding="utf-8")))
    if not results:
        sys.exit("no simplified pages found")

    dictionary = load_dictionary(results)
    states = {m: new_state() for m in MODES}
    stats = []
    for pi, r in enumerate(results):
        counts = {m: len(page_new_words(r, dictionary, states[m],
                                        mode=m, page_index=pi))
                  for m in MODES}
        stats.append({"page": r["page"],
                      "unk_pct": 100 - r.get("coverage_after", 0),
                      "counts": counts})

    tpl = open(os.path.join(HERE, "pdfui_template.html"),
               encoding="utf-8").read()
    html = tpl.replace("__STATS_JSON__", json.dumps(stats, ensure_ascii=False))
    out = os.path.join(HERE, "pdf.html")
    with open(out, "w", encoding="utf-8") as f:
        f.write(html)
    totals = {m: sum(s["counts"][m] for s in stats) for m in MODES}
    print(f"written {out}: {len(stats)} pages, vocab totals {totals}")

if __name__ == "__main__":
    main()
