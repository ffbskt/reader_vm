# -*- coding: utf-8 -*-
"""
Gemini-side of the anonymizer: rewrite message texts replacing every person
name and place name with its code. Gemini (not a regex) does the replacement
so Russian inflected forms — Денис / Дениса / Денису, Москва / в Москве —
all collapse to the same code. Processed in batches with a shared, growing
map so a name gets the SAME code across the whole conversation.
"""
import json, os, re, sys, time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
from simplify_page import MODELS, QuotaError          # reuse model list
from analyze import read_api_key

BATCH = 40            # messages per Gemini call
API_GAP_S = 5

def _call(prompt):
    import requests
    key = read_api_key()
    if not key:
        raise RuntimeError("no Gemini API key")
    err = None
    for model in MODELS:
        url = (f"https://generativelanguage.googleapis.com/v1beta/models/"
               f"{model}:generateContent?key={key}")
        try:
            r = requests.post(url, json={
                "contents": [{"parts": [{"text": prompt}]}],
                "generationConfig": {"temperature": 0}}, timeout=180)
            if r.status_code == 404:
                continue
            if r.status_code == 429:
                raise QuotaError("429")
            r.raise_for_status()
            t = r.json()["candidates"][0]["content"]["parts"][0]["text"]
            return json.loads(re.sub(r"^```(json)?|```$", "", t.strip(),
                                     flags=re.M).strip())
        except QuotaError:
            raise
        except Exception as e:
            err = str(e).replace(key, "***")
    raise RuntimeError(f"all models failed: {err}")


def anonymize_texts(texts, name_to_code, taken):
    """Replace names/places in a list of message texts. `name_to_code` and
    `taken` are shared state carried across batches (mutated in place).
    Returns the list of anonymized texts (same length/order)."""
    out = []
    for i in range(0, len(texts), BATCH):
        chunk = texts[i:i + BATCH]
        known = "\n".join(f"{c} = {n}" for n, c in name_to_code.items()) \
            or "(none yet)"
        numbered = "\n".join(f"{j}. {t}" for j, t in enumerate(chunk))
        prompt = f"""Anonymize a private chat. In each numbered message, replace every
PERSON name and every PLACE/location name with a SHORT CODE (1 letter;
use 2 letters only to stay unique). Same name -> same code everywhere,
including inflected forms (Russian: Денис/Дениса/Денису -> D; Москва/в
Москве -> M). Replace ONLY names of people and places — keep all other
words, punctuation and meaning exactly. Do not translate.

CODES ALREADY ASSIGNED (reuse these, do not change them):
{known}

Return ONLY JSON:
{{"texts": ["anonymized message 0", "... message 1", ...],
  "map": {{"CODE": "canonical name", ...}}}}   (map = NEW codes you added)

MESSAGES:
{numbered}"""
        res = _call(prompt)
        for code, name in (res.get("map") or {}).items():
            code = str(code).strip()
            name = str(name).strip()
            if code and name and name not in name_to_code:
                name_to_code[name] = code
                taken.add(code)
        got = res.get("texts") or []
        # keep original if the model dropped a line (never lose a message)
        for j, orig in enumerate(chunk):
            out.append(got[j] if j < len(got) and isinstance(got[j], str)
                       else orig)
        time.sleep(API_GAP_S)
    return out
