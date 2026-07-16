# -*- coding: utf-8 -*-
"""
Build reader.html — page-by-page HTML reader of the simplified Celestina with
hover translations (EN/RU) from the accumulated per-page vocabularies.
No API calls; uses data/simplified/.

  build_reader.py [--from 41] [--to 90] [--method rewrite] [--pct 100]
"""
import sys, os, json, argparse, base64

sys.stdout.reconfigure(encoding="utf-8")
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from vocab_common import load_dictionary, page_new_words, new_state, MODES

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--from", dest="p_from", type=int, default=41)
    ap.add_argument("--to", dest="p_to", type=int, default=90)
    ap.add_argument("--method", default="rewrite")
    ap.add_argument("--pct", type=int, default=100)
    ap.add_argument("--mode", choices=list(MODES), default="repeat")
    args = ap.parse_args()

    results = []
    for page in range(args.p_from, args.p_to + 1):
        fp = os.path.join(HERE, "data", "simplified",
                          f"page{page}_{args.method}_{args.pct}.json")
        if os.path.exists(fp):
            results.append(json.load(open(fp, encoding="utf-8")))
    if not results:
        sys.exit("no simplified pages found")

    dictionary = load_dictionary(results)

    # per-page vocab: same shared logic as the PDF (in-text new words only)
    pages, vstate = [], new_state()
    for pi, r in enumerate(results):
        vocab = page_new_words(r, dictionary, vstate, mode=args.mode,
                               page_index=pi)
        pages.append({
            "page": r["page"],
            "coverage_after": r.get("coverage_after", "?"),
            "vocab": vocab,
            "sentences": [{"speaker": s.get("speaker", ""),
                           "simple": s.get("simple", "")}
                          for s in r.get("sentences", [])],
        })

    # embed the PDF for the download button (works in the static artifact too)
    pdf_path = os.path.join(HERE, "data", "celestina_simplified_41_90.pdf")
    pdf_b64 = ""
    if os.path.exists(pdf_path):
        pdf_b64 = base64.b64encode(open(pdf_path, "rb").read()).decode()

    tpl = open(os.path.join(HERE, "reader_template.html"),
               encoding="utf-8").read()
    html = (tpl.replace("__READER_JSON__", json.dumps(
                {"pages": pages, "dict": dictionary}, ensure_ascii=False))
               .replace("__PDF_B64__", pdf_b64))
    out = os.path.join(HERE, "reader.html")
    with open(out, "w", encoding="utf-8") as f:
        f.write(html)
    print(f"written {out}: {len(pages)} pages, "
          f"{len(dictionary)} dictionary entries, "
          f"pdf embedded: {bool(pdf_b64)}, {len(html)} bytes")

if __name__ == "__main__":
    main()
