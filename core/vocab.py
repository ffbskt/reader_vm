# -*- coding: utf-8 -*-
"""
Shared vocabulary logic for build_pdf.py and build_reader.py, so the PDF and
the HTML reader always show the same per-page word list.

Page vocabulary = words that actually occur in the SIMPLIFIED text and are
still new to the learner (unknown_after), translated via the merged
dictionary, capped (default 10), most frequent first.
"""
import os, json

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FOLD = str.maketrans("áéíóúñü", "aeiounu")

def fold(w):
    return w.lower().translate(FOLD)

def load_dictionary(pages_results):
    """folded es -> {en, ru}; page-vocab translations win, bulk dict fills."""
    d = {}
    for r in pages_results:
        for v in r.get("vocab", []):
            es = str(v.get("es", "")).strip()
            if es and v.get("en") and v.get("ru"):
                d.setdefault(fold(es), {"en": str(v["en"]).strip(),
                                        "ru": str(v["ru"]).strip()})
    wd = os.path.join(ROOT, "data", "word_dict.json")
    if os.path.exists(wd):
        for k, v in json.load(open(wd, encoding="utf-8")).items():
            d.setdefault(k, v)
    return d

# attached object pronouns: consolarle, dimelo, matarse...
CLITICS = ("selos", "selas", "selo", "sela", "melo", "mela", "telo", "tela",
           "nos", "les", "los", "las", "se", "me", "te", "le", "lo", "la")
# common verb endings (accent-folded), longest first; the stripped stem is
# tried directly and as stem+ar/er/ir (mataran -> matar, crecen -> crecer)
VERB_ENDS = sorted(
    ("ariamos eriamos iriamos asteis isteis aramos ieramos abamos iamos "
     "ieron ieran ierais iendo ando aria eria iria arias erias irias arian "
     "erian irian ados adas idos idas ado ada ido ida aban abas aba aran "
     "aras ara aren areis are eran eras era ere iran iras ira ire aron aste "
     "iste asen ase iesen iese emos imos amos ais eis an en es as os a e o "
     "i n s").split(), key=len, reverse=True)

def lookup(dictionary, word):
    """Find a translation for an (inflected) word: exact, minus attached
    pronouns, plural, or conjugated verb mapped back to its infinitive."""
    def rec(k, depth):
        if k in dictionary:
            return dictionary[k]
        if depth >= 3 or len(k) < 4:
            return None
        for cl in CLITICS:
            if k.endswith(cl) and len(k) - len(cl) >= 3:
                r = rec(k[:-len(cl)], depth + 1)
                if r:
                    return r
        for end in VERB_ENDS:
            stem = k[:-len(end)]
            if k.endswith(end) and len(stem) >= 3:
                # stem-changing verbs: pierda->perder, calienta->calentar
                variants = [stem]
                if "ie" in stem:
                    variants.append(stem.replace("ie", "e"))
                if "ue" in stem:
                    variants.append(stem.replace("ue", "o"))
                for st in variants:
                    if st in dictionary:
                        return dictionary[st]
                    for inf in ("ar", "er", "ir"):
                        if st + inf in dictionary:
                            return dictionary[st + inf]
        return None
    return rec(fold(word), 0)

MODES = {
    "repeat":   "every page lists ALL its unknown words, repeats underlined",
    "norepeat": "a word appears in the vocabulary only once, on its first page",
    "spaced":   "repeats come back with growing gaps (1, 2, 4, 8... pages) — "
                "spaced repetition",
    "clean":    "just the text, continuous — no vocabulary, no page marks, "
                "no underlines (for reading aloud)",
}

def new_state():
    return {"count": {}, "last": {}}

def page_new_words(result, dictionary, state, mode="repeat", page_index=0):
    """
    Vocabulary for one page: ALL words that remain in the simplified text and
    are still unknown to the learner (unknown_after), with translations,
    in alphabetical order. `state` (from new_state()) tracks what was already
    shown; `mode` decides how repeats across pages are handled.
    Returns entries=[{es,en,ru,repeat}].
    """
    if mode == "clean":
        return []
    counts = {}
    for s in result.get("sentences", []):
        for w in s.get("unknown_after", []):
            k = fold(w)
            counts.setdefault(k, [w, 0])
            counts[k][1] += 1
    ordered = sorted(counts.items(), key=lambda kv: kv[0])
    entries = []
    for k, (w, n) in ordered:
        tr = lookup(dictionary, w)
        if not tr:
            continue
        shown = state["count"].get(k, 0)
        if shown:
            if mode == "norepeat":
                continue
            if mode == "spaced" and \
                    page_index - state["last"].get(k, -999) < 2 ** shown:
                continue
        entries.append({"es": w, "en": tr["en"], "ru": tr["ru"],
                        "repeat": shown > 0})
        state["count"][k] = shown + 1
        state["last"][k] = page_index
    return entries
