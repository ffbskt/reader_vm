# -*- coding: utf-8 -*-
"""
Full pseudonymize pipeline: export bytes -> anonymized .txt + code map.

  python -m convbot.run <export.json> [out.txt]   # local test / CLI
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from convbot import anonymize as A
from convbot.gemini_anon import anonymize_texts


def anonymize_export(raw_bytes, progress=None):
    """Returns {txt, map_text, name_to_code, n_messages}. `progress(done,
    total)` is called as batches complete (for the bot's live counter)."""
    parsed = A.parse_export(raw_bytes)
    msgs = parsed["messages"]
    if not msgs:
        raise ValueError("no messages found in this export")

    # senders coded deterministically first (they anchor the map)
    sender_code, taken = A.code_map_for_senders(msgs)
    name_to_code = dict(sender_code)          # real name -> code

    # anonymize message texts (people + places mentioned inside), batched
    texts = [m["text"] for m in msgs]
    done = [0]
    total = len(texts)

    def batched(all_texts):
        from convbot.gemini_anon import BATCH
        out = []
        for i in range(0, len(all_texts), BATCH):
            part = anonymize_texts(all_texts[i:i + BATCH],
                                   name_to_code, taken)
            out += part
            done[0] = min(total, i + BATCH)
            if progress:
                progress(done[0], total)
        return out

    anon_texts = batched(texts)
    for m, t in zip(msgs, anon_texts):
        m["text"] = t

    txt = A.render_txt(parsed["chat_name"], msgs, sender_code)
    # clean up any letter+digit codes Gemini emitted -> pure 1-2 letters
    txt, name_to_code = A.normalize_codes(txt, name_to_code,
                                          keep=set(sender_code.values()))
    return {"txt": txt, "map_text": A.render_map(name_to_code),
            "name_to_code": name_to_code, "n_messages": len(msgs)}


if __name__ == "__main__":
    raw = open(sys.argv[1], "rb").read()
    r = anonymize_export(raw, progress=lambda d, t: print(f"  {d}/{t}",
                                                          flush=True))
    out = sys.argv[2] if len(sys.argv) > 2 else "anonymized.txt"
    open(out, "w", encoding="utf-8").write(r["txt"])
    print(f"\nwritten {out} ({r['n_messages']} messages)")
    print("MAP:\n" + r["map_text"])
