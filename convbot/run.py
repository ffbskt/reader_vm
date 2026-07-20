# -*- coding: utf-8 -*-
"""
Full pseudonymize pipeline with options.

  python -m convbot.run <export.json> [out.txt] [scope] [timeshift] [self]
    scope     = main | people | full   (default full)
    timeshift = 0 | 1                    (default 0; +1 week +3 min)
    self      = uploader display name    (to detect the main character)
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from convbot import anonymize as A
from convbot.gemini_anon import anonymize_texts, BATCH


def _norm(s):
    return "".join(ch for ch in (s or "").lower() if ch.isalnum())

def detect_self(senders, self_name):
    """Which sender is the uploader (kept as 'me'). Match by name overlap."""
    if not self_name:
        return None
    sn = _norm(self_name)
    for s in senders:
        ns = _norm(s)
        if sn and (sn in ns or ns in sn or
                   set(sn) and _norm(s).startswith(sn[:4])):
            return s
    return None


def anonymize_export(raw_bytes, scope="full", time_shift=False,
                     self_name=None, progress=None):
    """Returns {txt, map_text, labels, n_messages}."""
    parsed = A.parse_export(raw_bytes)
    msgs = parsed["messages"]
    if not msgs:
        raise ValueError("no messages found in this export")

    senders = list(dict.fromkeys(m["sender"] for m in msgs))
    me = detect_self(senders, self_name)
    others = [s for s in senders if s != me]

    # both speakers always get a letter LABEL (a transcript needs distinct
    # tags); pre-seed them as known person-entities so in-text mentions of
    # the same person reuse the same letter.
    sender_code, taken = A.code_map_for_senders(msgs)
    entities = {s: {"code": sender_code[s], "type": "person"} for s in senders}

    # scope decides what ELSE gets replaced inside the message text
    targets = None
    if scope == "main":
        targets = others or senders          # the main character(s)

    texts = [m["text"] for m in msgs]
    total = len(texts)
    out = []
    for i in range(0, total, BATCH):
        out += anonymize_texts(texts[i:i + BATCH], entities,
                               scope=scope, targets=targets)
        if progress:
            progress(min(total, i + BATCH), total)
    for m, t in zip(msgs, out):
        m["text"] = t

    txt = A.render_txt(parsed["chat_name"], msgs, sender_code,
                       time_shift=time_shift)
    txt, labels = A.finalize(txt, entities, reserved=set(sender_code.values()))
    return {"txt": txt, "map_text": A.render_map(labels),
            "labels": labels, "n_messages": total}


if __name__ == "__main__":
    raw = open(sys.argv[1], "rb").read()
    out = sys.argv[2] if len(sys.argv) > 2 else "anonymized.txt"
    scope = sys.argv[3] if len(sys.argv) > 3 else "full"
    ts = len(sys.argv) > 4 and sys.argv[4] == "1"
    self_name = sys.argv[5] if len(sys.argv) > 5 else None
    r = anonymize_export(raw, scope=scope, time_shift=ts, self_name=self_name,
                         progress=lambda d, t: print(f"  {d}/{t}", flush=True))
    open(out, "w", encoding="utf-8").write(r["txt"])
    print(f"\nwritten {out} ({r['n_messages']} messages, scope={scope}, "
          f"timeshift={ts})\nMAP:\n{r['map_text']}")
