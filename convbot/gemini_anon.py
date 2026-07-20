# -*- coding: utf-8 -*-
"""
Gemini side of the anonymizer. Rewrites message texts replacing names/places
with stable short codes (handles Russian inflections), and reports each
entity's CATEGORY (person / city / country / bar / street / …) so the caller
can render typed labels like "city V". Batched with shared state so a name
keeps one code across the whole conversation.

scope:
  "main"   replace ONLY the given main-character name(s) (people/places off)
  "people" replace all PERSON names (places off)
  "full"   replace all person names AND all place names
"""
import json, os, re, sys, time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
from simplify_page import MODELS, QuotaError
from analyze import read_api_key

BATCH = 40
API_GAP_S = 5

SCOPE_RULE = {
    "main": ("Replace ONLY these people's names (and their inflected forms): "
             "{targets}. Leave every other name and all places unchanged."),
    "people": ("Replace every PERSON name (all inflected forms). "
               "Leave places unchanged."),
    "full": ("Replace every PERSON name AND every PLACE / location name "
             "(city, country, bar, cafe, restaurant, street, shop, company, "
             "region), all inflected forms."),
}


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


def anonymize_texts(texts, entities, scope="full", targets=None):
    """Replace names/places in message texts. `entities` is shared state:
    {canonical_name: {"code": gcode, "type": category}} — mutated in place.
    Returns the anonymized texts (same order)."""
    rule = SCOPE_RULE[scope].format(targets=", ".join(targets or []))
    out = []
    for i in range(0, len(texts), BATCH):
        chunk = texts[i:i + BATCH]
        known = "\n".join(f"{e['code']} = {n} ({e['type']})"
                          for n, e in entities.items()) or "(none yet)"
        numbered = "\n".join(f"{j}. {t}" for j, t in enumerate(chunk))
        prompt = f"""Anonymize a private chat. {rule}

Replace each target with a SHORT CODE token (1 letter; 2 letters only to stay
unique), the SAME code everywhere including inflected forms. Keep every other
word, number and punctuation exactly; do not translate.

CODES ALREADY ASSIGNED (reuse, do not change):
{known}

Return ONLY JSON:
{{"texts": ["anonymized msg 0", "msg 1", ...],
  "map": {{"CODE": {{"name": "canonical name", "type": "person|city|country|"
          "bar|cafe|restaurant|street|shop|company|region|place"}}, ...}}}}
(map = only the NEW codes you introduced)

MESSAGES:
{numbered}"""
        res = _call(prompt)
        for code, info in (res.get("map") or {}).items():
            code = str(code).strip()
            if isinstance(info, str):
                info = {"name": info, "type": "person"}
            name = str(info.get("name", "")).strip()
            typ = str(info.get("type", "person")).strip().lower()
            if code and name and name not in entities:
                entities[name] = {"code": code, "type": typ}
        got = res.get("texts") or []
        for j, orig in enumerate(chunk):
            out.append(got[j] if j < len(got) and isinstance(got[j], str)
                       else orig)
        time.sleep(API_GAP_S)
    return out
