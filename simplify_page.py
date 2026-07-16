# -*- coding: utf-8 -*-
"""
Simplify ONE page of Book 2 with the Gemini API (free tier, cheapest model,
a single request), constrained to the Book 1 known vocabulary.

  simplify_page.py --page 41 --method rewrite --pct 100

methods:
  substitute - keep sentence structure, only swap unknown words for known ones
  rewrite    - freely rewrite each sentence in simple known words
  gloss      - keep the original text, add [simple synonym] after unknown words

--pct N: aim to deal with ~N% of the unknown words (most frequent first).

Results are cached in data/simplified/page<N>_<method>_<pct>.json — an already
computed combination is loaded from disk, NO new API request is made
(use --force to recompute). Cost policy: see CLAUDE.md.
"""
import sys, os, re, json, argparse

sys.stdout.reconfigure(encoding="utf-8")

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from analyze import (load_pages, clean_ocr, counted_words, fold,
                     read_api_key, apply_ocr_fixes, BOOK2, OCR_FIXES_PATH)

# cheapest first; fall back if the account/model is unavailable
MODELS = ["gemini-flash-lite-latest", "gemini-2.5-flash-lite",
          "gemini-3.1-flash-lite", "gemini-flash-latest"]

METHOD_PROMPTS = {
    "substitute": (
        "Keep each sentence's structure. ONLY replace the listed unknown words "
        "with words from the known vocabulary (adjust grammar minimally so the "
        "sentence stays correct)."),
    "rewrite": (
        "Rewrite each sentence from scratch in simple modern Spanish, using "
        "ONLY words from the known vocabulary. Preserve the meaning."),
    "gloss": (
        "Keep the original sentence exactly, but after each listed unknown "
        "word insert a gloss in brackets with a simple synonym from the known "
        "vocabulary, like: menester [necesidad]."),
}

class QuotaError(RuntimeError):
    """HTTP 429 — quota/rate limit; retrying other models won't help."""

def cache_path(page, method, pct):
    return os.path.join(HERE, "data", "simplified",
                        f"page{page}_{method}_{pct}.json")

def load_known():
    board = json.load(open(os.path.join(HERE, "data", "board_data.json"),
                           encoding="utf-8"))
    known = sorted({w["w"] for w in board["book1_words"]})
    names = {fold(w) for w in board.get("proper_nouns", [])}
    return known, set(known) | names   # (prompt list, scoring set)

def page_words(page):
    pages = load_pages(BOOK2)
    if page not in pages:
        raise ValueError(f"page {page} not in book 2 (1-{max(pages)})")
    text = clean_ocr(pages[page])
    if os.path.exists(OCR_FIXES_PATH):
        text = apply_ocr_fixes(
            text, json.load(open(OCR_FIXES_PATH, encoding="utf-8")))
    lines = [l.strip() for l in text.splitlines()
             if l.strip() and not re.fullmatch(r"\d+", l.strip())]
    return re.sub(r"\s+", " ", " ".join(lines)).strip()

def simplify(page, method="rewrite", pct=100, force=False, rescore=False):
    """
    Returns (result_dict, cached). cached=True means loaded from the buffer,
    no API request was made. A fresh run costs exactly one free-tier request.
    """
    if method not in METHOD_PROMPTS:
        raise ValueError(f"unknown method {method!r}")
    pct = max(0, min(100, int(pct)))
    out = cache_path(page, method, pct)

    if os.path.exists(out) and not force and not rescore:
        return json.load(open(out, encoding="utf-8")), True

    known, known_set = load_known()
    page_text = page_words(page)
    words = counted_words(page_text)
    if len(words) < 20:
        raise ValueError(f"page {page} has almost no text "
                         f"({len(words)} words) — likely blank/illustration")
    unknown = sorted({w for w in words if fold(w) not in known_set})
    cov_before = round((1 - sum(1 for w in words if fold(w) not in known_set)
                        / max(len(words), 1)) * 100)
    print(f"page {page}: {len(words)} words, {len(unknown)} unknown types, "
          f"coverage before: {cov_before}%")

    if rescore:
        if not os.path.exists(out):
            raise ValueError(f"nothing to rescore: {out} does not exist")
        prev = json.load(open(out, encoding="utf-8"))
        sentences, model_used = prev["sentences"], prev.get("model", "?")
        vocab = prev.get("vocab", [])
    else:
        parsed, model_used = call_gemini(page_text, known, unknown,
                                         method, pct)
        sentences = parsed.get("sentences", [])
        vocab = parsed.get("vocab", [])

    result = score_and_save(out, page, method, pct, sentences, vocab,
                            model_used, known_set, cov_before, unknown)
    return result, False

def call_gemini(page_text, known, unknown, method, pct):
    key = read_api_key()
    if not key:
        raise RuntimeError(
            "no API key found (gemini_key.txt / API_KEY.txt / GEMINI_API_KEY)")

    prompt = f"""You are simplifying a page of 'La Celestina' (classic Spanish, digitized
with OCR errors) for a learner.

KNOWN VOCABULARY (the learner knows these words, accents were stripped;
any inflected form of these words is fine, as are proper names):
{" ".join(known)}

UNKNOWN WORDS on this page: {" ".join(unknown)}

TASK: {METHOD_PROMPTS[method]}
Handle approximately {pct}% of the unknown words, prioritizing the ones
most important for understanding; you may leave the rest as-is.
Fix obvious OCR errors while you work.

ALSO build a small vocabulary: pick the ~20 unknown words most important for
understanding this page and translate each to English and Russian
(dictionary form + the form as used, e.g. ir - went - шел).

This is a PLAY: lines usually start with a character name like 'CALIXTO.—'.
For each sentence set "speaker" to that character's name (uppercase), keeping
the name OUT of the orig/simple text; use "" for narration, stage directions,
headings (ACTO/ARGUMENTO) and other non-dialog text.

Reply ONLY with JSON:
{{"vocab": [{{"es": "word", "en": "translation", "ru": "перевод"}}],
  "sentences": [{{"speaker": "CALIXTO", "orig": "original sentence",
                  "simple": "result sentence"}}]}}

PAGE TEXT:
{page_text}"""

    import requests
    err = None
    for model in MODELS:
        url = (f"https://generativelanguage.googleapis.com/v1beta/models/"
               f"{model}:generateContent?key={key}")
        body = {"contents": [{"parts": [{"text": prompt}]}],
                "generationConfig": {"temperature": 0.2}}
        try:
            r = requests.post(url, json=body, timeout=120)
            if r.status_code == 404:
                print(f"{model}: not available, trying next")
                continue
            if r.status_code == 429:
                # quota is shared per project: don't hammer other models too.
                # Include the body so the log shows WHICH limit was hit.
                detail = r.text[:300].replace("\n", " ")
                raise QuotaError(f"429 rate/quota on {model}: {detail}")
            r.raise_for_status()
            text = r.json()["candidates"][0]["content"]["parts"][0]["text"]
            cleaned = re.sub(r"^```(json)?|```$", "", text.strip(),
                             flags=re.M).strip()
            parsed = json.loads(cleaned)
            if isinstance(parsed, list):        # legacy shape: bare sentence list
                parsed = {"sentences": parsed, "vocab": []}
            return parsed, model
        except QuotaError as e:
            raise QuotaError(str(e).replace(key, "***"))
        except Exception as e:
            err = str(e).replace(key, "***")
            print(f"{model}: {err}")
    raise RuntimeError(f"all models failed, last error: {err}")

def score_and_save(out, page, method, pct, sentences, vocab, model_used,
                   known_set, cov_before, unknown):
    """Verify coverage of the simplified text and write the result JSON."""
    simple_all = " ".join(s.get("simple", "") for s in sentences)
    # ignore bracket glosses when scoring the gloss method
    scored_text = (re.sub(r"\[[^\]]*\]", "", simple_all)
                   if method == "gloss" else simple_all)
    sw = counted_words(scored_text.lower())
    still_unknown = sorted({w for w in sw if fold(w) not in known_set})
    cov_after = round((1 - sum(1 for w in sw if fold(w) not in known_set)
                       / max(len(sw), 1)) * 100)
    print(f"coverage after: {cov_after}%  (still unknown: {len(still_unknown)} types)")

    for s in sentences:
        ws = counted_words(s.get("simple", "").lower())
        s["unknown_after"] = sorted({w for w in ws if fold(w) not in known_set})

    result = {
        "page": page, "method": method, "pct": pct,
        "fmt": 2,          # 2 = has vocab + per-sentence speaker fields
        "model": model_used,
        "coverage_before": cov_before, "coverage_after": cov_after,
        "unknown_before": len(unknown), "unknown_after": len(still_unknown),
        "vocab": vocab,
        "sentences": sentences,
    }
    os.makedirs(os.path.dirname(out), exist_ok=True)
    with open(out, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=1)
    print("written", out)
    return result

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--page", type=int, required=True)
    ap.add_argument("--method", choices=list(METHOD_PROMPTS), default="rewrite")
    ap.add_argument("--pct", type=int, default=100,
                    help="target %% of unknown words to handle (freq-first)")
    ap.add_argument("--force", action="store_true",
                    help="recompute even if cached (costs one request)")
    ap.add_argument("--rescore", action="store_true",
                    help="recompute coverage of an existing result, no API call")
    args = ap.parse_args()
    result, cached = simplify(args.page, args.method, args.pct,
                              force=args.force, rescore=args.rescore)
    if cached:
        print(f"loaded from cache ({cache_path(args.page, args.method, args.pct)}), "
              "no API request made; use --force to recompute")

if __name__ == "__main__":
    main()
