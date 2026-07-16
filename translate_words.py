# -*- coding: utf-8 -*-
"""
Complete the reader dictionary: translate every word used in the simplified
pages (41-90) that the per-page vocabularies don't cover -> data/word_dict.json.

Batched free-tier Gemini requests (~150 words per request), resume-safe:
already-translated words are never requested again.

  translate_words.py [--dry-run]   (dry run: just count what's missing)
"""
import sys, os, re, json, glob, time, argparse

sys.stdout.reconfigure(encoding="utf-8")
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from simplify_page import MODELS, QuotaError
from analyze import read_api_key

FOLD = str.maketrans("áéíóúñü", "aeiounu")
fold = lambda w: w.lower().translate(FOLD)
WORD_RE = re.compile(r"[A-Za-zÁÉÍÓÚÑÜáéíóúñü]+")
OUT = os.path.join(HERE, "data", "word_dict.json")
BATCH = 150
PAUSE = 15

def collect_missing():
    """Distinct words in simplified texts not covered by page vocab (stemmed)."""
    vocab_keys, words, names = set(), {}, set()
    for fp in sorted(glob.glob(os.path.join(HERE, "data", "simplified",
                                            "page*_rewrite_100.json"))):
        r = json.load(open(fp, encoding="utf-8"))
        for v in r.get("vocab", []):
            vocab_keys.add(fold(str(v.get("es", ""))))
        for s in r.get("sentences", []):
            names.add(fold((s.get("speaker") or "").strip(".")))
            for w in WORD_RE.findall(s.get("simple", "")):
                k = fold(w)
                if len(k) > 1:
                    words[k] = words.get(k, 0) + 1

    def covered(k):
        if k in vocab_keys:
            return True
        for suf in ("s", "es", "n", "la", "lo", "le", "me", "te", "se"):
            if len(k) > len(suf) + 2 and k.endswith(suf) \
                    and k[:-len(suf)] in vocab_keys:
                return True
        return False

    done = {}
    if os.path.exists(OUT):
        done = json.load(open(OUT, encoding="utf-8"))
    missing = [k for k in words
               if not covered(k) and k not in done and k not in names]
    missing.sort(key=lambda k: -words[k])
    return missing, done

def translate_batch(chunk, key):
    import requests
    listing = " ".join(chunk)
    prompt = (
        "Translate each Spanish word to English and Russian (short dictionary "
        "translation, 1-3 words each). The words come from a simplified "
        "Spanish text; accents were stripped (senor = señor). Reply ONLY with "
        'JSON: [{"es":"word","en":"translation","ru":"перевод"}]\n\n' + listing)
    err = None
    for model in MODELS:
        url = (f"https://generativelanguage.googleapis.com/v1beta/models/"
               f"{model}:generateContent?key={key}")
        body = {"contents": [{"parts": [{"text": prompt}]}],
                "generationConfig": {"temperature": 0.1}}
        try:
            r = requests.post(url, json=body, timeout=180)
            if r.status_code == 404:
                continue
            if r.status_code == 429:
                raise QuotaError("429: " + r.text[:300].replace("\n", " "))
            r.raise_for_status()
            text = r.json()["candidates"][0]["content"]["parts"][0]["text"]
            text = re.sub(r"^```(json)?|```$", "", text.strip(), flags=re.M).strip()
            return json.loads(text)
        except QuotaError:
            raise
        except Exception as e:
            err = str(e).replace(key, "***")
            print(f"{model}: {err}")
    raise RuntimeError(f"all models failed: {err}")

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    missing, done = collect_missing()
    n_req = (len(missing) + BATCH - 1) // BATCH
    print(f"already translated: {len(done)} · missing: {len(missing)} words "
          f"-> {n_req} requests of ~{BATCH}")
    if args.dry_run or not missing:
        return
    key = read_api_key()
    if not key:
        sys.exit("no API key found")

    for i in range(0, len(missing), BATCH):
        chunk = missing[i:i + BATCH]
        rows = translate_batch(chunk, key)
        for row in rows:
            k = fold(str(row.get("es", "")))
            en, ru = str(row.get("en", "")).strip(), str(row.get("ru", "")).strip()
            if k and en and ru:
                done[k] = {"en": en, "ru": ru}
        with open(OUT, "w", encoding="utf-8") as f:
            json.dump(done, f, ensure_ascii=False, indent=0)
        print(f"batch {i // BATCH + 1}/{n_req}: +{len(rows)} "
              f"(total {len(done)})")
        if i + BATCH < len(missing):
            time.sleep(PAUSE)
    print(f"written {OUT}: {len(done)} entries")

if __name__ == "__main__":
    main()
