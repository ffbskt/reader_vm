# -*- coding: utf-8 -*-
"""
Pseudonymize a Telegram chat export: replace person names and place names
with short 1-2 letter codes, keep timestamps, return the anonymized text
plus the code->name map.

Pure parsing/rendering/code-assignment here (unit-tested, no network).
The name/place detection over message text uses Gemini (handles Russian
inflections) and lives in gemini_anonymize_batch().
"""
import json, re
from datetime import datetime, timedelta

# what a code looks like per entity category. Persons keep a bare letter;
# places carry a human label so the reader knows it's a place ("city V").
PLACE_LABELS = {
    "city": "city", "country": "country", "bar": "bar", "cafe": "cafe",
    "restaurant": "restaurant", "street": "street", "region": "region",
    "shop": "shop", "company": "company", "place": "place",
}

def entity_label(category, letter):
    """Render the visible code: person -> 'V'; city -> 'city V'; etc."""
    cat = (category or "").lower()
    if cat in ("person", "people", "name", "") :
        return letter
    return f"{PLACE_LABELS.get(cat, cat)} {letter}"


def shift_time(date_str, weeks=1, minutes=3):
    """Add a fixed offset to a 'YYYY-MM-DD HH:MM:SS' timestamp."""
    try:
        dt = datetime.strptime(date_str, "%Y-%m-%d %H:%M:%S")
    except ValueError:
        return date_str
    return (dt + timedelta(weeks=weeks, minutes=minutes)) \
        .strftime("%Y-%m-%d %H:%M:%S")


def flatten_text(text):
    """Telegram 'text' is a str, or a list of str / {type,text} parts."""
    if isinstance(text, str):
        return text
    if isinstance(text, list):
        out = []
        for part in text:
            if isinstance(part, str):
                out.append(part)
            elif isinstance(part, dict):
                out.append(part.get("text", ""))
        return "".join(out)
    return ""


def parse_export(raw_bytes):
    """Bytes of a Telegram Desktop result.json -> chat name + messages
    [{id, date, sender, text}] (service messages and empty texts dropped)."""
    data = json.loads(raw_bytes.decode("utf-8-sig"))
    msgs = []
    for m in data.get("messages", []):
        if m.get("type") != "message":
            continue
        text = flatten_text(m.get("text", "")).strip()
        if not text:
            continue
        msgs.append({"id": m.get("id"),
                     "date": (m.get("date") or "").replace("T", " "),
                     "sender": m.get("from") or "?",
                     "text": text})
    return {"chat_name": data.get("name", "chat"), "messages": msgs}


def _first_letters(name):
    """Yield candidate codes for a name: initials of the words, then the
    first 1, 2, 3 letters — used to pick a short unique code."""
    words = [w for w in re.split(r"\s+", name.strip()) if w]
    if words:
        initials = "".join(w[0] for w in words).upper()
        yield initials[0]                 # first initial (usual case)
        if len(initials) > 1:
            yield initials[:2]            # two initials for collisions
    flat = re.sub(r"\s+", "", name)
    for n in (1, 2, 3):
        if len(flat) >= n:
            yield flat[:n].upper()


def assign_code(name, taken):
    """A short code for `name` not already in `taken` (a set of codes)."""
    for cand in _first_letters(name):
        if cand not in taken:
            taken.add(cand)
            return cand
    # last resort: letter + running number
    i = 1
    base = (re.sub(r"\W", "", name) or "X")[0].upper()
    while f"{base}{i}" in taken:
        i += 1
    taken.add(f"{base}{i}")
    return f"{base}{i}"


def code_map_for_senders(messages):
    """Deterministic code per distinct sender, in first-appearance order.
    Returns {sender_name: code} and the set of taken codes."""
    taken, mapping = set(), {}
    for m in messages:
        s = m["sender"]
        if s not in mapping:
            mapping[s] = assign_code(s, taken)
    return mapping, taken


def render_txt(chat_name, messages, sender_code, time_shift=False):
    """Anonymized transcript: [timestamp] CODE: text (text already
    anonymized upstream). Sender labels replaced by their codes. If
    time_shift, every timestamp is moved +1 week +3 minutes."""
    lines = [f"# {chat_name} (anonymized)", ""]
    for m in messages:
        code = sender_code.get(m["sender"], "?")
        date = shift_time(m["date"]) if time_shift else m["date"]
        lines.append(f"[{date}] {code}: {m['text']}")
    return "\n".join(lines) + "\n"


def _clean_codes(reserved):
    """Yield pure-letter codes A,B,…,Z,AA,AB,… skipping `reserved`."""
    import itertools, string
    for size in (1, 2):
        for combo in itertools.product(string.ascii_uppercase, repeat=size):
            c = "".join(combo)
            if c not in reserved:
                yield c


def finalize(txt, entities, reserved):
    """Turn Gemini's raw codes into clean typed labels in BOTH the text and
    the map. `entities` = {name: {"code": gcode, "type": category}}.
    `reserved` = letter codes already used (sender letters). Persons render as
    a bare letter, places as 'city V' etc. Returns (new_txt, {name: label})."""
    reserved = set(reserved)
    # a stable letter for every entity, preserving any clean sender letters
    def first_pos(code):
        m = re.search(rf"(?<![A-Za-z0-9]){re.escape(code)}(?![A-Za-z0-9])", txt)
        return m.start() if m else 10 ** 9
    ordered = sorted(entities.items(), key=lambda kv: first_pos(kv[1]["code"]))
    gen = _clean_codes(reserved)
    label_of, code_to_label = {}, {}
    for name, e in ordered:
        code = e["code"]
        if re.fullmatch(r"[A-Z]{1,2}", code) and code in reserved:
            letter = code                      # keep sender letters as-is
        else:
            letter = next(gen)
        label = entity_label(e["type"], letter)
        label_of[name] = label
        code_to_label[code] = label
    # placeholder-safe replacement of each raw code -> its typed label
    codes = sorted(code_to_label, key=len, reverse=True)
    for i, code in enumerate(codes):
        txt = re.sub(rf"(?<![A-Za-z0-9]){re.escape(code)}(?![A-Za-z0-9])",
                     f"\x00{i}\x00", txt)
    for i, code in enumerate(codes):
        txt = txt.replace(f"\x00{i}\x00", code_to_label[code])
    return txt, label_of


def render_map(name_to_code):
    """Human-readable map for the Telegram reply: CODE = real name."""
    rows = sorted(name_to_code.items(), key=lambda kv: kv[1])
    return "\n".join(f"{code} = {name}" for name, code in rows)
