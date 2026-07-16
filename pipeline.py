# -*- coding: utf-8 -*-
"""
Generic pipeline behind the local site (app.html): any known-vocabulary
sources + any target book, leveled simplification via Gemini, cached per
page+level so a combination is never paid for twice.

Levels: 0/25/50/75 = the % of the book's unknown TEXT (word occurrences,
not dictionary entries) that is allowed to REMAIN: the most frequent unknown
words are kept until they cover that share of unknown tokens (0 = only known
words). Token-based on purpose — word frequencies are Zipf-distributed, so a
type-based cut would make the levels feel almost identical.

Layout under data/site/:
  known/<slug>.json                 one uploaded known-vocab source
  books/<slug>/book.txt             page-marked target text (<<<PAGE N>>>)
  books/<slug>/meta.json            title, page count
  books/<slug>/simplified/page<N>_L<level>.json   cached results (fmt 2)
  books/<slug>/job.json             last translate-job state

Cost policy (CLAUDE.md): translation runs ONLY when the user starts a job
from the site; cached pages are returned with no API request.
"""
import io, os, re, json, time, random, threading
from collections import Counter

HERE = os.path.dirname(os.path.abspath(__file__))
import sys
sys.path.insert(0, HERE)
from analyze import (fold, counted_words, tokenize, clean_ocr, proper_nouns,
                     read_api_key, load_pages)
from simplify_page import MODELS, QuotaError

SITE = os.path.join(HERE, "data", "site")
KNOWN_DIR = os.path.join(SITE, "known")
BOOKS_DIR = os.path.join(SITE, "books")
LEVELS = (0, 25, 50, 75)
PAGE_WORDS = 220          # pseudo-page size for plain text without markers
API_GAP_S = 5             # pause between Gemini calls (free-tier RPM)

def _slug(name):
    base = os.path.splitext(os.path.basename(name))[0].lower()
    s = re.sub(r"[^a-z0-9]+", "_", base).strip("_")
    return s[:40] or "book"

def _read_upload_text(filename, blob):
    """PDF/TXT upload -> page-marked text. Plain text gets pseudo-pages."""
    if filename.lower().endswith(".pdf"):
        from pypdf import PdfReader
        reader = PdfReader(io.BytesIO(blob))
        out = []
        for i, page in enumerate(reader.pages):
            out.append(f"\n<<<PAGE {i+1}>>>\n" + (page.extract_text() or ""))
        return "".join(out)
    text = blob.decode("utf-8", errors="replace")
    if "<<<PAGE" in text:
        return text
    # plain text: cut into pseudo-pages of ~PAGE_WORDS words on paragraphs
    paras = re.split(r"\n\s*\n", text)
    pages, cur, n = [], [], 0
    for p in paras:
        cur.append(p)
        n += len(p.split())
        if n >= PAGE_WORDS:
            pages.append("\n\n".join(cur))
            cur, n = [], 0
    if cur:
        pages.append("\n\n".join(cur))
    return "".join(f"\n<<<PAGE {i+1}>>>\n{t}" for i, t in enumerate(pages))

def _looks_like_word_list(text):
    lines = [l for l in text.splitlines() if l.strip()]
    if not lines:
        return False
    avg = sum(len(l.split()) for l in lines) / len(lines)
    return avg <= 3          # one-ish word per line = a vocabulary list

# --------------------------------------------------------------------------
# known-vocabulary sources
# --------------------------------------------------------------------------
def add_known_source(filename, blob):
    os.makedirs(KNOWN_DIR, exist_ok=True)
    slug = _slug(filename)
    if filename.lower().endswith(".pdf"):
        text = _read_upload_text(filename, blob)
        kind = "book"
    else:
        text = blob.decode("utf-8", errors="replace")
        kind = "list" if _looks_like_word_list(text) else "book"
    if kind == "list":
        words = sorted({fold(w) for line in text.splitlines()
                        for w in tokenize(line.split("#")[0]) if len(w) > 1})
    else:
        # a book the user has read: words seen at least twice count as known
        counts = Counter(counted_words(clean_ocr(text)))
        words = sorted({fold(w) for w, c in counts.items() if c >= 2})
    src = {"name": os.path.basename(filename), "slug": slug, "kind": kind,
           "count": len(words), "words": words}
    with open(os.path.join(KNOWN_DIR, slug + ".json"), "w",
              encoding="utf-8") as f:
        json.dump(src, f, ensure_ascii=False)
    return {k: src[k] for k in ("name", "slug", "kind", "count")}

def list_known():
    out = []
    if os.path.isdir(KNOWN_DIR):
        for fn in sorted(os.listdir(KNOWN_DIR)):
            if fn.endswith(".json"):
                s = json.load(open(os.path.join(KNOWN_DIR, fn),
                                   encoding="utf-8"))
                out.append({k: s[k] for k in ("name", "slug", "kind", "count")})
    return out

def delete_known(slug):
    fp = os.path.join(KNOWN_DIR, _slug(slug) + ".json")
    if os.path.exists(fp):
        os.remove(fp)
        return True
    return False

def known_set():
    words = set()
    if os.path.isdir(KNOWN_DIR):
        for fn in os.listdir(KNOWN_DIR):
            if fn.endswith(".json"):
                s = json.load(open(os.path.join(KNOWN_DIR, fn),
                                   encoding="utf-8"))
                words.update(s["words"])
    return words

# --------------------------------------------------------------------------
# target books
# --------------------------------------------------------------------------
def add_book(filename, blob):
    slug = _slug(filename)
    bdir = os.path.join(BOOKS_DIR, slug)
    os.makedirs(os.path.join(bdir, "simplified"), exist_ok=True)
    text = _read_upload_text(filename, blob)
    with open(os.path.join(bdir, "book.txt"), "w", encoding="utf-8") as f:
        f.write(text)
    pages = load_pages(os.path.join(bdir, "book.txt"))
    title = os.path.splitext(os.path.basename(filename))[0]
    meta = {"slug": slug, "title": title, "pages": max(pages) if pages else 0}
    with open(os.path.join(bdir, "meta.json"), "w", encoding="utf-8") as f:
        json.dump(meta, f, ensure_ascii=False)
    return meta

def list_books():
    out = []
    if os.path.isdir(BOOKS_DIR):
        for slug in sorted(os.listdir(BOOKS_DIR)):
            mp = os.path.join(BOOKS_DIR, slug, "meta.json")
            if os.path.exists(mp):
                m = json.load(open(mp, encoding="utf-8"))
                m["done_pages"] = {
                    lv: len(cached_pages(slug, lv)) for lv in LEVELS}
                out.append(m)
    return out

def book_dir(slug):
    return os.path.join(BOOKS_DIR, _slug(slug))

def book_pages(slug):
    return load_pages(os.path.join(book_dir(slug), "book.txt"))

def page_text(slug, n):
    pages = book_pages(slug)
    if n not in pages:
        raise ValueError(f"page {n} not in book (1-{max(pages)})")
    text = clean_ocr(pages[n])
    lines = [l.strip() for l in text.splitlines()
             if l.strip() and not re.fullmatch(r"\d+", l.strip())]
    return re.sub(r"\s+", " ", " ".join(lines)).strip()

def cache_file(slug, page, level):
    return os.path.join(book_dir(slug), "simplified",
                        f"page{page}_L{level}.json")

def cached_pages(slug, level):
    sd = os.path.join(book_dir(slug), "simplified")
    out = []
    if os.path.isdir(sd):
        for fn in os.listdir(sd):
            m = re.fullmatch(rf"page(\d+)_L{level}\.json", fn)
            if m:
                out.append(int(m.group(1)))
    return sorted(out)

# --------------------------------------------------------------------------
# coverage stats
# --------------------------------------------------------------------------
def _allowed_types(ranked, unk, level):
    """The most frequent unknown types that together cover `level`% of the
    unknown TOKENS (word occurrences) in the book."""
    if level <= 0:
        return set()
    target = sum(unk.values()) * level / 100
    keep, cum = set(), 0
    for w in ranked:
        if cum >= target:
            break
        keep.add(w)
        cum += unk[w]
    return keep

def _book_analysis(slug, known):
    """Shared tokenization: full text, proper nouns, unknown freq ranking."""
    pages = book_pages(slug)
    full = clean_ocr("\n".join(pages[k] for k in sorted(pages)))
    names = {fold(w) for w in proper_nouns(full)}
    counts = Counter(w for w in counted_words(full) if fold(w) not in names)
    unk = Counter({w: c for w, c in counts.items() if fold(w) not in known})
    ranked = [w for w, _ in unk.most_common()]   # most frequent first
    return pages, names, counts, unk, ranked

def book_stats(slug, sample_n=30):
    known = known_set()
    pages, names, counts, unk, ranked = _book_analysis(slug, known)

    tok_total = sum(counts.values())
    tok_unk = sum(unk.values())
    per_page = []
    for n in sorted(pages):
        ws = [w for w in counted_words(clean_ocr(pages[n]))
              if fold(w) not in names]
        u = sum(1 for w in ws if fold(w) not in known)
        per_page.append({"page": n, "words": len(ws),
                         "unk_pct": round(u / max(len(ws), 1) * 100, 1)})

    rng = random.Random(7)
    sample = sorted(rng.sample(ranked, min(sample_n, len(ranked))))

    # each level keeps the top-frequency words covering L% of unknown TOKENS
    levels = []
    for lv in LEVELS:
        keep = _allowed_types(ranked, unk, lv)
        kept_tok = sum(unk[w] for w in keep)
        levels.append({
            "level": lv,
            "kept_types": len(keep),
            "removed_types": len(ranked) - len(keep),
            "unk_pct_after": round(kept_tok / max(tok_total, 1) * 100, 1)})

    return {
        "slug": _slug(slug), "pages": max(pages) if pages else 0,
        "known_words": len(known),
        "token_coverage": round((1 - tok_unk / max(tok_total, 1)) * 100, 1),
        "type_coverage": round((1 - len(unk) / max(len(counts), 1)) * 100, 1),
        "unknown_types": len(unk), "total_types": len(counts),
        "sample": [{"w": w, "n": unk[w]} for w in sample],
        "per_page": per_page,
        "levels": levels,
    }

# --------------------------------------------------------------------------
# leveled simplification (one Gemini request per page+level, cached)
# --------------------------------------------------------------------------
def simplify_book_page(slug, page, level, force=False):
    """Returns (result, cached). Cached combination = NO API request."""
    if level not in LEVELS:
        raise ValueError(f"level must be one of {LEVELS}")
    out = cache_file(slug, page, level)
    if os.path.exists(out) and not force:
        return json.load(open(out, encoding="utf-8")), True

    known = known_set()
    if not known:
        raise RuntimeError("no known vocabulary uploaded yet (step 1)")
    _, names, counts, unk, ranked = _book_analysis(slug, known)
    allowed = _allowed_types(ranked, unk, level)
    known_or_ok = {fold(w) for w in known} | {fold(w) for w in allowed} | names

    text = page_text(slug, page)
    words = counted_words(text)
    if len(words) < 20:
        raise ValueError(f"page {page} has almost no text ({len(words)} words)")
    page_unknown = sorted({w for w in words
                           if fold(w) not in known_or_ok
                           and fold(w) not in names})
    page_allowed = sorted({w for w in words if w in allowed})
    cov_before = round((1 - sum(1 for w in words if fold(w) not in known
                                and fold(w) not in names)
                        / max(len(words), 1)) * 100)

    meta = json.load(open(os.path.join(book_dir(slug), "meta.json"),
                          encoding="utf-8"))
    parsed, model = _call_gemini_site(meta["title"], text, sorted(known),
                                      page_allowed, page_unknown)
    sentences = parsed.get("sentences", [])
    vocab = parsed.get("vocab", [])

    # score vs the learner's KNOWN set (allowed words still count as unknown
    # to the learner — they must appear in the vocab/hover translations)
    known_f = {fold(w) for w in known} | names
    simple_all = " ".join(s.get("simple", "") for s in sentences)
    sw = counted_words(simple_all.lower())
    cov_after = round((1 - sum(1 for w in sw if fold(w) not in known_f)
                       / max(len(sw), 1)) * 100)
    for s in sentences:
        ws = counted_words(s.get("simple", "").lower())
        s["unknown_after"] = sorted({w for w in ws if fold(w) not in known_f})

    result = {
        "page": page, "method": "rewrite", "level": level, "fmt": 2,
        "model": model,
        "coverage_before": cov_before, "coverage_after": cov_after,
        "unknown_before": len(page_unknown) + len(page_allowed),
        "unknown_after": len({fold(w) for w in sw if fold(w) not in known_f}),
        "vocab": vocab, "sentences": sentences,
    }
    os.makedirs(os.path.dirname(out), exist_ok=True)
    with open(out, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=1)
    return result, False

def _call_gemini_site(title, page_text_s, known, allowed, unknown):
    key = read_api_key()
    if not key:
        raise RuntimeError("no API key (gemini_key.txt / GEMINI_API_KEY)")

    allowed_part = (
        f"\nALLOWED DIFFICULT WORDS (the learner is studying these; they MAY "
        f"stay in the text as-is): {' '.join(allowed)}\n" if allowed else "")
    prompt = f"""You are simplifying one page of '{title}' for a language learner.

KNOWN VOCABULARY (the learner knows these words; accents were stripped; any
inflected form is fine, as are proper names):
{" ".join(known)}
{allowed_part}
WORDS TO ELIMINATE (unknown to the learner, NOT in the allowed list):
{" ".join(unknown)}

TASK: rewrite each sentence in simple modern language (same language as the
text), using ONLY the known vocabulary plus the allowed difficult words.
Replace or rephrase every word from the eliminate list. Preserve the meaning.
Fix obvious OCR errors while you work.

ALSO build a vocabulary: list EVERY word that remains in your simplified
text and is not in the known vocabulary (allowed words included — up to 40
entries, most important first if you must cut) and translate each to English
and Russian (dictionary form + the form as used).

If the text is a play or dialog, lines start with a character name; put that
name (uppercase) in "speaker" and keep it OUT of the orig/simple text.
Use "" for narration, headings and other non-dialog text.

Reply ONLY with JSON:
{{"vocab": [{{"es": "word", "en": "translation", "ru": "перевод"}}],
  "sentences": [{{"speaker": "NAME", "orig": "original sentence",
                  "simple": "result sentence"}}]}}

PAGE TEXT:
{page_text_s}"""

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
                continue
            if r.status_code == 429:
                raise QuotaError(
                    "429 rate/quota on " + model + ": "
                    + r.text[:300].replace("\n", " ").replace(key, "***"))
            r.raise_for_status()
            txt = r.json()["candidates"][0]["content"]["parts"][0]["text"]
            cleaned = re.sub(r"^```(json)?|```$", "", txt.strip(),
                             flags=re.M).strip()
            return json.loads(cleaned), model
        except QuotaError:
            raise
        except Exception as e:
            err = str(e).replace(key, "***")
    raise RuntimeError(f"all models failed, last error: {err}")

# --------------------------------------------------------------------------
# background translate job (one per book at a time)
# --------------------------------------------------------------------------
_jobs = {}          # slug -> state dict (also persisted to job.json)
_jobs_lock = threading.Lock()

def _job_path(slug):
    return os.path.join(book_dir(slug), "job.json")

def _persist(state):
    with open(_job_path(state["slug"]), "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False)

def job_state(slug):
    slug = _slug(slug)
    with _jobs_lock:
        if slug in _jobs:
            return dict(_jobs[slug])
    jp = _job_path(slug)
    if os.path.exists(jp):
        return json.load(open(jp, encoding="utf-8"))
    return {"slug": slug, "status": "idle"}

def start_job(slug, p_from, p_to, level):
    """User-initiated translate run: pages from-to at one level, sequential
    Gemini calls with a pause; cached pages are skipped for free."""
    slug = _slug(slug)
    if level not in LEVELS:
        return {"error": f"level must be one of {LEVELS}"}
    pages = book_pages(slug)
    p_from, p_to = max(1, p_from), min(max(pages), p_to)
    if p_from > p_to:
        return {"error": "empty page range"}
    with _jobs_lock:
        cur = _jobs.get(slug)
        if cur and cur["status"] == "running":
            return {"error": "a job is already running for this book"}
        todo = list(range(p_from, p_to + 1))
        state = {"slug": slug, "status": "running", "level": level,
                 "from": p_from, "to": p_to, "total": len(todo),
                 "done": 0, "cached": 0, "current": None, "errors": [],
                 "started": time.time(), "eta_s": None, "pct": 0}
        _jobs[slug] = state
    threading.Thread(target=_run_job, args=(state, todo, level),
                     daemon=True).start()
    return dict(state)

def _run_job(state, todo, level):
    slug = state["slug"]
    api_calls, api_time = 0, 0.0
    for p in todo:
        state["current"] = p
        try:
            t0 = time.time()
            _, cached = simplify_book_page(slug, p, level)
            if cached:
                state["cached"] += 1
            else:
                api_calls += 1
                api_time += time.time() - t0
                time.sleep(API_GAP_S)
        except QuotaError as e:
            state["errors"].append(f"page {p}: {e}")
            state["status"] = "quota"      # stop: retrying won't help today
            break
        except Exception as e:
            state["errors"].append(f"page {p}: {e}")
        state["done"] += 1
        state["pct"] = round(state["done"] / state["total"] * 100)
        remaining = state["total"] - state["done"]
        if api_calls:
            per = api_time / api_calls + API_GAP_S
            state["eta_s"] = round(remaining * per)
        _persist(state)
    if state["status"] == "running":
        # final pass: batch-translate any words the page vocabs missed, so
        # every hover in the reader has a translation (1 request / ~120 words)
        try:
            gap = fill_missing_translations(slug)
            if gap.get("added"):
                state["gap_filled"] = gap["added"]
        except Exception as e:
            state["errors"].append(f"gap-fill: {e}")
        state["status"] = "done"
        state["pct"] = 100
        state["eta_s"] = 0
    state["current"] = None
    _persist(state)

# --------------------------------------------------------------------------
# translation gap-filler: words in simplified texts with no dictionary entry
# --------------------------------------------------------------------------
def _book_word_dict(slug):
    fp = os.path.join(book_dir(slug), "word_dict.json")
    if os.path.exists(fp):
        return json.load(open(fp, encoding="utf-8"))
    return {}

def _all_cached_results(slug):
    import glob as _glob
    out = []
    for fp in _glob.glob(os.path.join(book_dir(slug), "simplified",
                                      "page*_L*.json")):
        out.append(json.load(open(fp, encoding="utf-8")))
    return out

def fill_missing_translations(slug):
    """Translate every word that occurs in the simplified texts but has no
    dictionary entry — ONE batched Gemini request per ~120 words, merged into
    the book's own word_dict.json. Runs at the end of a translate job."""
    from vocab_common import load_dictionary, lookup
    slug = _slug(slug)
    results = _all_cached_results(slug)
    dictionary = load_dictionary(results)
    book_dict = _book_word_dict(slug)
    dictionary.update(book_dict)
    missing = sorted({fold(w) for r in results for s in r["sentences"]
                      for w in s.get("unknown_after", [])
                      if lookup(dictionary, w) is None})
    if not missing:
        return {"added": 0, "requests": 0}

    key = read_api_key()
    if not key:
        return {"added": 0, "requests": 0, "error": "no API key"}
    import requests
    added, reqs = 0, 0
    for i in range(0, len(missing), 120):
        chunk = missing[i:i + 120]
        prompt = (
            "Translate each Spanish word to English and Russian (short, "
            "dictionary-style). Some words are inflected forms or contain "
            "attached pronouns — translate the underlying word, but the "
            '"es" field must repeat the given word EXACTLY as written. '
            'Reply ONLY with JSON: [{"es": "word", "en": "...", "ru": "..."}]'
            "\n\n" + " ".join(chunk))
        for model in MODELS:
            url = (f"https://generativelanguage.googleapis.com/v1beta/models/"
                   f"{model}:generateContent?key={key}")
            try:
                r = requests.post(url, json={
                    "contents": [{"parts": [{"text": prompt}]}],
                    "generationConfig": {"temperature": 0.1}}, timeout=120)
                if r.status_code in (404, 429):
                    if r.status_code == 429:
                        raise QuotaError("429 on gap-fill batch")
                    continue
                r.raise_for_status()
                txt = r.json()["candidates"][0]["content"]["parts"][0]["text"]
                txt = re.sub(r"^```(json)?|```$", "", txt.strip(),
                             flags=re.M).strip()
                reqs += 1
                for row in json.loads(txt):
                    es = fold(str(row.get("es", "")).strip())
                    if es and row.get("en") and row.get("ru"):
                        book_dict.setdefault(es, {
                            "en": str(row["en"]).strip(),
                            "ru": str(row["ru"]).strip()})
                        added += 1
                break
            except QuotaError:
                raise
            except Exception:
                continue
        time.sleep(API_GAP_S)
    with open(os.path.join(book_dir(slug), "word_dict.json"), "w",
              encoding="utf-8") as f:
        json.dump(book_dict, f, ensure_ascii=False, indent=1)
    return {"added": added, "requests": reqs}

# --------------------------------------------------------------------------
# reader payload: simplified pages + merged hover dictionary
# --------------------------------------------------------------------------
def reader_payload(slug, level):
    from vocab_common import load_dictionary
    import glob as _glob
    slug = _slug(slug)
    meta = json.load(open(os.path.join(book_dir(slug), "meta.json"),
                          encoding="utf-8"))
    results = []
    for p in cached_pages(slug, level):
        results.append(json.load(open(cache_file(slug, p, level),
                                      encoding="utf-8")))
    # hover dictionary: vocab from EVERY cached page at EVERY level of this
    # book + the book's gap-fill word_dict, so translations collected once
    # help everywhere
    dictionary = load_dictionary(_all_cached_results(slug) or results)
    dictionary.update(_book_word_dict(slug))
    return {"title": meta["title"], "slug": slug, "level": level,
            "dictionary": dictionary,
            "pages": results}
