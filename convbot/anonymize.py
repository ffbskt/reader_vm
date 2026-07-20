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


def render_txt(chat_name, messages, sender_code):
    """Anonymized transcript: [timestamp] CODE: text (text already
    anonymized upstream). Sender labels replaced by their codes."""
    lines = [f"# {chat_name} (anonymized)", ""]
    for m in messages:
        code = sender_code.get(m["sender"], "?")
        lines.append(f"[{m['date']}] {code}: {m['text']}")
    return "\n".join(lines) + "\n"


def _clean_codes(reserved):
    """Yield pure-letter codes A,B,…,Z,AA,AB,… skipping `reserved`."""
    import itertools, string
    for size in (1, 2):
        for combo in itertools.product(string.ascii_uppercase, repeat=size):
            c = "".join(combo)
            if c not in reserved:
                yield c


def normalize_codes(txt, name_to_code, keep):
    """Remap any code that isn't a clean 1-2 LETTER code (e.g. Gemini's
    'K1', 'P15') to pure-letter codes, updating both the text and the map.
    `keep` = codes to preserve as-is (the sender codes). Order follows first
    appearance in the text so the map reads top-to-bottom.
    Returns (new_txt, new_name_to_code)."""
    keep = set(keep)
    to_remap = {c for c in name_to_code.values()
                if c not in keep and not re.fullmatch(r"[A-Z]{1,2}", c)}
    if not to_remap:
        return txt, name_to_code
    # order by first appearance (longest codes matched first when searching)
    def first_pos(code):
        m = re.search(rf"(?<![A-Za-z0-9]){re.escape(code)}(?![A-Za-z0-9])", txt)
        return m.start() if m else 10 ** 9
    ordered = sorted(to_remap, key=first_pos)
    gen = _clean_codes(keep | {c for c in name_to_code.values()
                               if c in keep or re.fullmatch(r"[A-Z]{1,2}", c)})
    old_to_new = {old: next(gen) for old in ordered}
    # placeholder pass avoids collisions (old codes may be substrings)
    for i, old in enumerate(sorted(to_remap, key=len, reverse=True)):
        txt = re.sub(rf"(?<![A-Za-z0-9]){re.escape(old)}(?![A-Za-z0-9])",
                     f"\x00{i}\x00", txt)
        old_to_new[f"\x00{i}\x00"] = old_to_new[old]
    for i, old in enumerate(sorted(to_remap, key=len, reverse=True)):
        txt = txt.replace(f"\x00{i}\x00", old_to_new[old])
    new_map = {name: old_to_new.get(code, code)
               for name, code in name_to_code.items()}
    return txt, new_map


def render_map(name_to_code):
    """Human-readable map for the Telegram reply: CODE = real name."""
    rows = sorted(name_to_code.items(), key=lambda kv: kv[1])
    return "\n".join(f"{code} = {name}" for name, code in rows)
