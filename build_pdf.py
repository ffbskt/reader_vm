# -*- coding: utf-8 -*-
"""
Build a learner's PDF from the simplified pages buffer:
per book page — a small vocabulary of new words (es - en - ru), then the
simplified text. No API calls; uses whatever is in data/simplified/.

  build_pdf.py [--from 41] [--to 90] [--method rewrite] [--pct 100]
               [--out data/celestina_simplified.pdf]
"""
import sys, os, re, json, glob, argparse

sys.stdout.reconfigure(encoding="utf-8")
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

from fpdf import FPDF
from vocab_common import load_dictionary, page_new_words, new_state, MODES

# regular / bold / italic with Latin + Cyrillic coverage, per platform
_FONT_SETS = [
    (r"C:\Windows\Fonts\arial.ttf", r"C:\Windows\Fonts\arialbd.ttf",
     r"C:\Windows\Fonts\ariali.ttf"),
    ("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
     "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
     "/usr/share/fonts/truetype/dejavu/DejaVuSans-Oblique.ttf"),
]
FONT = FONT_B = FONT_I = None
for _f, _b, _i in _FONT_SETS:
    if os.path.exists(_f) and os.path.exists(_b):
        FONT, FONT_B, FONT_I = _f, _b, _i
        break
if FONT is None:
    sys.exit("no usable TTF fonts found (need Arial or DejaVu Sans)")

def mark_words(text, words):
    """Wrap occurrences of vocabulary words in fpdf2 markdown underline."""
    for w in sorted(set(words), key=len, reverse=True):
        text = re.sub(rf"\b({re.escape(w)})\b", r"--\1--", text,
                      flags=re.IGNORECASE)
    return text

class BookPDF(FPDF):
    head_text = "La Celestina — texto simplificado"

    def header(self):
        if self.page_no() == 1 or not self.head_text:
            return
        self.set_font("ArialU", "", 8)
        self.set_text_color(130, 130, 130)
        self.cell(0, 6, self.head_text, align="R",
                  new_x="LMARGIN", new_y="NEXT")
        self.ln(2)

    def footer(self):
        if not self.head_text:      # clean mode: no page numbers either
            return
        self.set_y(-14)
        self.set_font("ArialU", "", 8)
        self.set_text_color(130, 130, 130)
        self.cell(0, 6, str(self.page_no()), align="C")

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--from", dest="p_from", type=int, default=41)
    ap.add_argument("--to", dest="p_to", type=int, default=90)
    ap.add_argument("--method", default="rewrite")
    ap.add_argument("--pct", type=int, default=100)
    ap.add_argument("--mode", choices=list(MODES), default="repeat",
                    help="vocabulary repeat handling: " +
                    "; ".join(f"{k} = {v}" for k, v in MODES.items()))
    ap.add_argument("--out", default=os.path.join("data",
                    "celestina_simplified.pdf"))
    # generic-book options (the local site passes these per uploaded book)
    ap.add_argument("--dir", default=os.path.join("data", "simplified"),
                    help="directory with the simplified page JSONs")
    ap.add_argument("--pattern", default=None,
                    help="page filename pattern with {n}, e.g. page{n}_L50.json")
    ap.add_argument("--title", default="La Celestina")
    ap.add_argument("--author", default="Fernando de Rojas")
    ap.add_argument("--known-note", dest="known_note",
                    default="al vocabulario de «Easy Spanish step-by-step»")
    args = ap.parse_args()
    pattern = args.pattern or f"page{{n}}_{args.method}_{args.pct}.json"

    results = []
    for page in range(args.p_from, args.p_to + 1):
        fp = os.path.join(HERE, args.dir, pattern.format(n=page))
        if os.path.exists(fp):
            results.append(json.load(open(fp, encoding="utf-8")))
    if not results:
        sys.exit("no simplified pages found for that range")

    pdf = BookPDF(format="A4")
    pdf.set_margins(20, 16, 20)
    pdf.set_auto_page_break(True, margin=18)
    pdf.add_font("ArialU", "", FONT)
    pdf.add_font("ArialU", "B", FONT_B)
    if os.path.exists(FONT_I):
        pdf.add_font("ArialU", "I", FONT_I)

    clean = args.mode == "clean"
    pdf.head_text = f"{args.title} — texto simplificado"
    if clean:
        # clean = pure text only: no title page, no running header
        pdf.head_text = ""
        pdf.add_page()   # continuous text, breaks only when the sheet fills
    else:
        # title page
        pdf.add_page()
        pdf.ln(60)
        pdf.set_font("ArialU", "B", 26)
        pdf.set_text_color(30, 30, 30)
        pdf.multi_cell(0, 12, args.title, align="C",
                       new_x="LMARGIN", new_y="NEXT")
        if args.author:
            pdf.set_font("ArialU", "", 14)
            pdf.multi_cell(0, 9, args.author, align="C",
                           new_x="LMARGIN", new_y="NEXT")
        pdf.ln(10)
        pdf.set_font("ArialU", "", 11)
        pdf.set_text_color(90, 90, 90)
        cov = sum(r["coverage_after"] for r in results) / len(results)
        pdf.multi_cell(0, 7,
            f"Texto simplificado {args.known_note}\n"
            f"páginas {results[0]['page']}–{results[-1]['page']} del original · "
            f"{len(results)} páginas · cobertura media {cov:.0f}%\n"
            "con vocabulario nuevo por página (español – english – русский)\n"
            "subrayado en el texto = palabra del vocabulario de la página\n"
            "subrayado en la lista = ya apareció en una página anterior\n"
            f"modo de repetición: {args.mode} — {MODES[args.mode]}",
            align="C")

    dictionary = load_dictionary(results)
    # site books keep a gap-fill word_dict.json next to their simplified dir
    book_wd = os.path.normpath(os.path.join(HERE, args.dir, "..",
                                            "word_dict.json"))
    if os.path.exists(book_wd):
        for k, v in json.load(open(book_wd, encoding="utf-8")).items():
            dictionary.setdefault(k, v)
    vstate = new_state()   # tracks vocabulary shown on earlier pages
    for pi, r in enumerate(results):
        # vocabulary computed first: its words get underlined in the text
        vocab = page_new_words(r, dictionary, vstate, mode=args.mode,
                               page_index=pi)
        vocab_words = [v["es"] for v in vocab]

        if not clean:
            pdf.add_page()
            pdf.set_font("ArialU", "B", 14)
            pdf.set_text_color(30, 30, 30)
            pdf.cell(0, 8, f"Página {r['page']}",
                     new_x="LMARGIN", new_y="NEXT")
            pdf.set_font("ArialU", "", 8.5)
            pdf.set_text_color(130, 130, 130)
            pdf.cell(0, 5, f"cobertura {r['coverage_before']}% → "
                     f"{r['coverage_after']}%  ·  método: {r['method']}",
                     new_x="LMARGIN", new_y="NEXT")
            pdf.ln(3)

        # simplified text: group consecutive sentences of one speaker into a
        # dialog line (bold name), narration into plain paragraphs
        groups = []
        for s in r["sentences"]:
            simple = s.get("simple", "").strip()
            if not simple:
                continue
            speaker = (s.get("speaker") or "").strip().upper().rstrip(".")
            if groups and groups[-1][0] == speaker:
                groups[-1][1].append(simple)
            else:
                groups.append((speaker, [simple]))
        pdf.set_text_color(20, 20, 20)
        for speaker, parts in groups:
            text = " ".join(parts).replace("**", "").replace("--", "-")
            if not clean and vocab_words:
                text = mark_words(text, vocab_words)   # underline vocab words
            pdf.set_font("ArialU", "", 11)
            if speaker:
                pdf.multi_cell(0, 6.4, f"**{speaker}.** — {text}",
                               new_x="LMARGIN", new_y="NEXT", markdown=True)
            else:
                pdf.multi_cell(0, 6.4, text,
                               new_x="LMARGIN", new_y="NEXT", markdown=True)
            pdf.ln(1.8)

        # vocabulary block UNDER the page text: all words that remain in the
        # simplified text and are still unknown; repeats underlined.
        # Laid out row by row (2 columns) so page breaks are safe.
        if vocab:
            pdf.ln(2)
            pdf.set_fill_color(243, 242, 236)
            pdf.set_font("ArialU", "B", 10)
            pdf.set_text_color(60, 60, 60)
            pdf.cell(0, 7, "  Vocabulario nuevo", fill=True,
                     new_x="LMARGIN", new_y="NEXT")
            col_w = (pdf.w - pdf.l_margin - pdf.r_margin) / 2
            for i in range(0, len(vocab), 2):
                if pdf.get_y() + 5.6 > pdf.page_break_trigger:
                    pdf.add_page()
                for col, v in enumerate(vocab[i:i + 2]):
                    pdf.set_x(pdf.l_margin + col * col_w)
                    es, en, ru = v["es"], v["en"], v["ru"]
                    pdf.set_text_color(120, 120, 120) if v["repeat"] \
                        else pdf.set_text_color(30, 30, 30)
                    pdf.set_font("ArialU", "BU" if v["repeat"] else "B", 9.5)
                    pdf.cell(pdf.get_string_width(es) + 1, 5.6, es)
                    pdf.set_font("ArialU", "", 9.5)
                    pdf.set_text_color(90, 90, 90)
                    pdf.cell(col_w - pdf.get_string_width(es) - 1, 5.6,
                             f" – {en} – {ru}")
                pdf.ln(5.6)
            pdf.ln(2)

    out = os.path.join(HERE, args.out)
    pdf.output(out)
    print(f"written {out} ({len(results)} book pages, {pdf.page_no()} PDF pages)")

if __name__ == "__main__":
    main()
