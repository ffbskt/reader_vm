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
import io, os, re, json, time, random, threading, hashlib, shutil
from collections import Counter

# repo root = parent of core/; legacy engine modules (analyze, simplify_page)
# still live there, and all data paths are root-relative
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
import sys
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
from analyze import (fold, counted_words, tokenize, clean_ocr, proper_nouns,
                     read_api_key, load_pages, classify_language)
from simplify_page import MODELS, QuotaError

import contextvars

SITE = os.path.join(ROOT, "data", "site")
LEVELS = (0, 25, 50, 75)
PAGE_WORDS = 220          # pseudo-page size for plain text without markers
API_GAP_S = 5             # pause between Gemini calls (free-tier RPM)
STORAGE_LIMIT = 100 * 1024 * 1024     # per-user library cap (bytes)

# Each user's library lives under SITE/users/<uid>/. The API sets this per
# request, the worker per job; default 1 = the owner / single-user mode.
_current_user = contextvars.ContextVar("user_id", default=1)

def set_user(uid):
    _current_user.set(int(uid))

def user_root(uid=None):
    return os.path.join(SITE, "users", str(uid if uid is not None
                                           else _current_user.get()))

def known_dir():
    return os.path.join(user_root(), "known")

def books_dir():
    return os.path.join(user_root(), "books")

def _dir_size(path):
    total = 0
    for dp, _, files in os.walk(path):
        for f in files:
            try:
                total += os.path.getsize(os.path.join(dp, f))
            except OSError:
                pass
    return total

def storage_used(uid=None):
    """A user's footprint: their known sources + the shared-library content
    they reference (each referenced book counted once)."""
    root = user_root(uid)
    total = _dir_size(os.path.join(root, "known"))
    seen = set()
    bd = os.path.join(root, "books")
    if os.path.isdir(bd):
        for slug in os.listdir(bd):
            rp = os.path.join(bd, slug, "ref.json")
            if os.path.exists(rp):
                h = json.load(open(rp, encoding="utf-8"))["hash"]
                if h not in seen:
                    seen.add(h)
                    total += _dir_size(os.path.join(library_root(), h))
            else:
                total += _dir_size(os.path.join(bd, slug))   # legacy
    return total

def migrate_legacy_to_user1():
    """Pre-multi-user data lived flat in SITE/known and SITE/books. Move it
    once into the owner's per-user library (SITE/users/1/). Idempotent."""
    dest = user_root(1)
    for name in ("known", "books"):
        old = os.path.join(SITE, name)
        new = os.path.join(dest, name)
        if os.path.isdir(old) and not os.path.exists(new):
            os.makedirs(dest, exist_ok=True)
            os.rename(old, new)

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
    os.makedirs(known_dir(), exist_ok=True)
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
        # a book the user has read: words seen at least twice count as known.
        # Bilingual textbooks leak English ("beautiful", "am") — language-
        # filter each word, with the text's own accented words as evidence.
        counts = Counter(counted_words(clean_ocr(text)))
        evidence = frozenset(fold(w) for w in counts
                             if any(c in "áéíóúñü" for c in w))
        words = sorted({fold(w) for w, c in counts.items() if c >= 2
                        and classify_language(w, evidence) == "es"})
    src = {"name": os.path.basename(filename), "slug": slug, "kind": kind,
           "count": len(words), "words": words}
    with open(os.path.join(known_dir(), slug + ".json"), "w",
              encoding="utf-8") as f:
        json.dump(src, f, ensure_ascii=False)
    return {k: src[k] for k in ("name", "slug", "kind", "count")}

def list_known():
    out = []
    if os.path.isdir(known_dir()):
        for fn in sorted(os.listdir(known_dir())):
            if fn.endswith(".json"):
                s = json.load(open(os.path.join(known_dir(), fn),
                                   encoding="utf-8"))
                out.append({k: s[k] for k in ("name", "slug", "kind", "count")})
    return out

def delete_known(slug):
    fp = os.path.join(known_dir(), _slug(slug) + ".json")
    if os.path.exists(fp):
        os.remove(fp)
        return True
    return False

def known_set():
    words = set()
    if os.path.isdir(known_dir()):
        for fn in os.listdir(known_dir()):
            if fn.endswith(".json"):
                s = json.load(open(os.path.join(known_dir(), fn),
                                   encoding="utf-8"))
                words.update(s["words"])
    return words

# --------------------------------------------------------------------------
# target books — content-addressed SHARED library (2c.2)
#   library/<hash>/{book.txt, meta.json, simplified/, word_dict.json}  shared
#   users/<uid>/books/<slug>/ref.json = ownership record {hash, name, ...}
# Same text uploaded by two users => one stored copy, translations reused.
# --------------------------------------------------------------------------
def library_root():
    return os.path.join(SITE, "library")

def _text_hash(text):
    """Stable id of a book's content: page markers dropped, whitespace and
    case normalized, so re-uploads / minor reformatting dedupe."""
    norm = re.sub(r"<<<PAGE \d+>>>", " ", text)
    norm = re.sub(r"\s+", " ", norm).strip().lower()
    return hashlib.sha256(norm.encode("utf-8")).hexdigest()[:16]

def _ref_path(slug):
    return os.path.join(books_dir(), _slug(slug), "ref.json")

def _read_ref(slug):
    fp = _ref_path(slug)
    return json.load(open(fp, encoding="utf-8")) if os.path.exists(fp) else None

def book_dir(slug):
    """Physical dir of a book's shared content for the current user."""
    ref = _read_ref(slug)
    if ref:
        return os.path.join(library_root(), ref["hash"])
    return os.path.join(books_dir(), _slug(slug))   # legacy (pre-migration)

def add_book(filename, blob):
    slug = _slug(filename)
    text = _read_upload_text(filename, blob)
    h = _text_hash(text)
    lib = os.path.join(library_root(), h)
    reused = os.path.exists(os.path.join(lib, "book.txt"))
    os.makedirs(os.path.join(lib, "simplified"), exist_ok=True)
    title = os.path.splitext(os.path.basename(filename))[0]
    if not reused:
        with open(os.path.join(lib, "book.txt"), "w", encoding="utf-8") as f:
            f.write(text)
        pages = load_pages(os.path.join(lib, "book.txt"))
        json.dump({"hash": h, "title": title,
                   "pages": max(pages) if pages else 0},
                  open(os.path.join(lib, "meta.json"), "w", encoding="utf-8"),
                  ensure_ascii=False)
    meta = json.load(open(os.path.join(lib, "meta.json"), encoding="utf-8"))
    # per-user ownership reference
    os.makedirs(os.path.join(books_dir(), slug), exist_ok=True)
    json.dump({"hash": h, "slug": slug, "title": meta["title"],
               "name": os.path.basename(filename), "added_at": time.time(),
               "pages_read": 0},
              open(_ref_path(slug), "w", encoding="utf-8"), ensure_ascii=False)
    done = {lv: len(cached_pages(slug, lv)) for lv in LEVELS}
    return {"slug": slug, "title": meta["title"], "pages": meta["pages"],
            "reused": reused, "existing_translations": sum(done.values()),
            "done_pages": done}

def list_books():
    out = []
    bd = books_dir()
    if os.path.isdir(bd):
        for slug in sorted(os.listdir(bd)):
            lib = book_dir(slug)
            mp = os.path.join(lib, "meta.json")
            if not os.path.exists(mp):
                continue
            ref = _read_ref(slug) or {}
            m = json.load(open(mp, encoding="utf-8"))
            item = {"slug": slug, "title": ref.get("title", m.get("title", slug)),
                    "pages": m.get("pages", 0),
                    "done_pages": {lv: len(cached_pages(slug, lv))
                                   for lv in LEVELS},
                    "done_base": {lv: len(cached_pages(slug, lv, True))
                                  for lv in LEVELS}}
            sd = os.path.join(lib, "simplified")
            times = [os.path.getmtime(os.path.join(sd, f))
                     for f in os.listdir(sd)] if os.path.isdir(sd) else []
            item["updated"] = max(times) if times else os.path.getmtime(mp)
            out.append(item)
    return out

def _hash_referenced_by_others(text_hash, exclude_uid):
    """True if any user OTHER than exclude_uid still references this hash."""
    users = os.path.join(SITE, "users")
    if not os.path.isdir(users):
        return False
    for uid in os.listdir(users):
        if str(uid) == str(exclude_uid):
            continue
        bd = os.path.join(users, uid, "books")
        if not os.path.isdir(bd):
            continue
        for slug in os.listdir(bd):
            rp = os.path.join(bd, slug, "ref.json")
            if os.path.exists(rp):
                try:
                    if json.load(open(rp, encoding="utf-8"))["hash"] == text_hash:
                        return True
                except (OSError, KeyError, ValueError):
                    pass
    return False

def delete_book(slug):
    """Remove the current user's book. Deletes only their ownership ref;
    the SHARED library content is garbage-collected ONLY when no other user
    still references it. Returns {deleted, shared_removed}."""
    slug = _slug(slug)
    udir = os.path.join(books_dir(), slug)
    ref = _read_ref(slug)
    if not os.path.isdir(udir):
        return {"deleted": False, "shared_removed": False}
    shared_removed = False
    if ref:
        uid = os.path.basename(user_root())
        if not _hash_referenced_by_others(ref["hash"], uid):
            lib = os.path.join(library_root(), ref["hash"])
            if os.path.isdir(lib):
                shutil.rmtree(lib)
                shared_removed = True
    shutil.rmtree(udir)                 # drop this user's reference only
    return {"deleted": True, "shared_removed": shared_removed}

def migrate_books_to_library():
    """One-time: move each user's per-book content into the shared library
    and leave a ref.json behind. Idempotent (skips dirs that already have a
    ref, or no book.txt)."""
    users = os.path.join(SITE, "users")
    if not os.path.isdir(users):
        return
    for uid in os.listdir(users):
        bd = os.path.join(users, uid, "books")
        if not os.path.isdir(bd):
            continue
        for slug in os.listdir(bd):
            d = os.path.join(bd, slug)
            if os.path.exists(os.path.join(d, "ref.json")) \
                    or not os.path.exists(os.path.join(d, "book.txt")):
                continue
            text = open(os.path.join(d, "book.txt"), encoding="utf-8").read()
            h = _text_hash(text)
            lib = os.path.join(library_root(), h)
            old_meta = {}
            mp = os.path.join(d, "meta.json")
            if os.path.exists(mp):
                old_meta = json.load(open(mp, encoding="utf-8"))
            title = old_meta.get("title", slug)
            if os.path.isdir(lib):
                shutil.rmtree(d)                 # library already has it
            else:
                os.makedirs(library_root(), exist_ok=True)
                shutil.move(d, lib)
            os.makedirs(d, exist_ok=True)
            pages = load_pages(os.path.join(lib, "book.txt"))
            json.dump({"hash": h, "title": title,
                       "pages": max(pages) if pages else 0},
                      open(os.path.join(lib, "meta.json"), "w",
                           encoding="utf-8"), ensure_ascii=False)
            json.dump({"hash": h, "slug": slug, "title": title,
                       "name": slug, "added_at": time.time(),
                       "pages_read": 0},
                      open(os.path.join(d, "ref.json"), "w",
                           encoding="utf-8"), ensure_ascii=False)

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

# guided cache = page<N>_L<lvl>.json ; baseline (vocab-free) = ..._base.json.
# baseline results are universal (no user's vocabulary) -> always shareable.
def cache_file(slug, page, level, baseline=False):
    suf = "_base" if baseline else ""
    return os.path.join(book_dir(slug), "simplified",
                        f"page{page}_L{level}{suf}.json")

def cached_pages(slug, level, baseline=False):
    sd = os.path.join(book_dir(slug), "simplified")
    suf = "_base" if baseline else ""
    out = []
    if os.path.isdir(sd):
        for fn in os.listdir(sd):
            m = re.fullmatch(rf"page(\d+)_L{level}{suf}\.json", fn)
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
REFINE_TARGET = 15        # keep refining a page while more unknowns remain
REFINE_MAX_PASSES = 2     # ... but never more than this many extra calls

def _refine_pass(title, sentences, allowed_words):
    """One 'puzzle' pass: rewrite so listed forbidden words disappear, WITHOUT
    shortening (research exp1: unkT 36->20). Returns new sentences or the old
    ones if the call fails/gets worse."""
    allowed_f = set(allowed_words)
    bad = sorted({fold(w) for s in sentences
                  for w in counted_words(s.get("simple", "").lower())
                  if fold(w) not in allowed_f})
    if not bad:
        return sentences
    body = "\n".join((f"{s.get('speaker','')}| " if s.get("speaker") else "")
                     + s.get("simple", "") for s in sentences)
    prompt = (
        f"PUZZLE: the text below (from '{title}') must use ONLY words from "
        f"this vocabulary (any inflected form and proper names are fine):\n"
        f"{' '.join(sorted(allowed_words))}\n\n"
        f"These words BREAK the rule: {' '.join(bad)}\n"
        f"Rewrite each line so every rule-breaking word is gone — replace it "
        f"with vocabulary words, or explain the idea with several simple "
        f"vocabulary words. Keep ALL the meaning; do NOT shorten or drop "
        f"content. Reply ONLY with JSON: "
        f'{{"sentences": [{{"speaker": "X or empty", "simple": "sentence"}}]}}'
        f"\n\nTEXT (speaker| sentence):\n{body}")
    try:
        parsed, _ = call_gemini_raw(prompt)
        new = parsed.get("sentences")
        if new and isinstance(new, list):
            def unk(ss):
                return len({fold(w) for s in ss
                            for w in counted_words(s.get("simple", "").lower())
                            if fold(w) not in allowed_f})
            if unk(new) < unk(sentences):        # only accept improvements
                return new
    except Exception:
        pass
    return sentences

def call_gemini_raw(prompt):
    """Minimal JSON call used by refine/baseline. Returns (parsed, model)."""
    key = read_api_key()
    if not key:
        raise RuntimeError("no API key")
    import requests as _rq
    err = None
    for model in MODELS:
        url = (f"https://generativelanguage.googleapis.com/v1beta/models/"
               f"{model}:generateContent?key={key}")
        try:
            r = _rq.post(url, json={"contents": [{"parts": [{"text": prompt}]}],
                         "generationConfig": {"temperature": 0.2}}, timeout=180)
            if r.status_code == 404:
                continue
            if r.status_code == 429:
                raise QuotaError("429 on refine call")
            r.raise_for_status()
            t = r.json()["candidates"][0]["content"]["parts"][0]["text"]
            t = re.sub(r"^```(json)?|```$", "", t.strip(), flags=re.M).strip()
            return json.loads(t), model
        except QuotaError:
            raise
        except Exception as e:
            err = str(e).replace(key, "***")
    raise RuntimeError(f"all models failed: {err}")

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

    # 2c.4: at the strictest level, iteratively "puzzle-refine" to drive
    # remaining unknown words down toward REFINE_TARGET (extra calls, but
    # only where it matters). allowed = known vocab + this level's allowed +
    # names (names are always fine in the text).
    if level == 0:
        allow_words = ({w for w in known} | set(allowed)
                       | {n for n in names})
        known_f0 = {fold(w) for w in known} | names
        for _ in range(REFINE_MAX_PASSES):
            ut = len({fold(w) for s in sentences
                      for w in counted_words(s.get("simple", "").lower())
                      if fold(w) not in known_f0})
            if ut <= REFINE_TARGET:
                break
            new = _refine_pass(meta["title"], sentences, allow_words)
            if new is sentences:
                break
            sentences = new
            time.sleep(API_GAP_S)

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

# baseline mode: no known vocabulary — Gemini simplifies to a CEFR-ish tier.
# Used when a user skips step 1 (with a warning) and for research.
BASELINE_LEVELS = {
    0: "A1 (absolute beginner: very short sentences, only the most common "
       "everyday words)",
    25: "A2 (beginner: simple sentences, high-frequency vocabulary)",
    50: "B1 (intermediate: natural but plain language, avoid rare/archaic "
        "words)",
    75: "B2 (upper-intermediate: keep most vocabulary, only replace rare, "
        "archaic or very literary words)",
}

def simplify_page_baseline(slug, page, level, force=False):
    """Returns (result, cached). Vocab-free simplification, cached apart
    from the vocab-guided cache (page<N>_L<lvl>_base.json)."""
    if level not in LEVELS:
        raise ValueError(f"level must be one of {LEVELS}")
    out = os.path.join(book_dir(slug), "simplified",
                       f"page{page}_L{level}_base.json")
    if os.path.exists(out) and not force:
        return json.load(open(out, encoding="utf-8")), True
    text = page_text(slug, page)
    if len(counted_words(text)) < 20:
        raise ValueError(f"page {page} has almost no text")
    meta = json.load(open(os.path.join(book_dir(slug), "meta.json"),
                          encoding="utf-8"))
    key = read_api_key()
    if not key:
        raise RuntimeError("no API key")
    prompt = f"""You are simplifying a page of '{meta['title']}' (may contain OCR errors)
for a language learner. There is NO learner vocabulary list.

TASK: rewrite each sentence in simple modern language at CEFR level
{BASELINE_LEVELS[level]}. Preserve the meaning. Fix obvious OCR errors.

ALSO build a vocabulary: list up to 40 words that remain in your simplified
text and could still be difficult at that level, translated to English and
Russian.

This may be a PLAY: lines often start with a character name like 'CALIXTO.—'.
Set "speaker" per sentence (uppercase, name kept OUT of the text); "" for
narration/headings.

Reply ONLY with JSON:
{{"vocab": [{{"es": "word", "en": "translation", "ru": "перевод"}}],
  "sentences": [{{"speaker": "X", "orig": "...", "simple": "..."}}]}}

PAGE TEXT:
{text}"""
    import requests as _rq
    err = None
    for model in MODELS:
        url = (f"https://generativelanguage.googleapis.com/v1beta/models/"
               f"{model}:generateContent?key={key}")
        try:
            r = _rq.post(url, json={"contents": [{"parts": [{"text": prompt}]}],
                         "generationConfig": {"temperature": 0.2}}, timeout=180)
            if r.status_code == 404:
                continue
            if r.status_code == 429:
                raise QuotaError("429 on baseline call")
            r.raise_for_status()
            t = r.json()["candidates"][0]["content"]["parts"][0]["text"]
            t = re.sub(r"^```(json)?|```$", "", t.strip(), flags=re.M).strip()
            parsed = json.loads(t)
            break
        except QuotaError:
            raise
        except Exception as e:
            err = str(e).replace(key, "***")
    else:
        raise RuntimeError(f"all models failed: {err}")
    result = {"page": page, "method": "baseline", "level": level, "fmt": 2,
              "model": model, "vocab": parsed.get("vocab", []),
              "sentences": parsed.get("sentences", [])}
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
    from core.vocab import load_dictionary, lookup
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
def reader_payload(slug, level, baseline=False):
    from core.vocab import load_dictionary
    slug = _slug(slug)
    meta = json.load(open(os.path.join(book_dir(slug), "meta.json"),
                          encoding="utf-8"))
    results = []
    for p in cached_pages(slug, level, baseline):
        results.append(json.load(open(cache_file(slug, p, level, baseline),
                                      encoding="utf-8")))
    # hover dictionary: vocab from EVERY cached page at EVERY level of this
    # book + the book's gap-fill word_dict, so translations collected once
    # help everywhere
    dictionary = load_dictionary(_all_cached_results(slug) or results)
    dictionary.update(_book_word_dict(slug))
    return {"title": meta["title"], "slug": slug, "level": level,
            "dictionary": dictionary,
            "pages": results}
