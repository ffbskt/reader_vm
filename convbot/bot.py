# -*- coding: utf-8 -*-
"""
@Conversation_download_bot — anonymize a Telegram chat export.

Send the JSON export (Telegram Desktop -> Export chat history -> JSON), pick
options, and get back the conversation with names/places replaced by short
codes ("city V", "bar O"), timestamps kept (or shifted), plus the code map.

Reuses the reader platform's user + limits code (api.auth / api.db /
api.limits): every Telegram user maps to a platform user by tg_id (owner rule
from OWNER_TG_ID), and daily volume is capped by LIMIT_DAILY_ANON. Long
polling, no public port.
"""
import io, os, sys, time
import requests

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from convbot.run import anonymize_export
from convbot import anonymize as A
from api import auth, db, limits

TOK = os.environ["CONVERSATION_BOT_TOKEN"]
TG = f"https://api.telegram.org/bot{TOK}"
MAX_BYTES = 20 * 1024 * 1024

SCOPES = [("main", "Main character"), ("people", "All names"),
          ("full", "Names + places")]
PENDING = {}          # chat_id -> {bytes, name, scope, time_shift, self}

HELP = ("Send me a Telegram chat export and pick options; I return it with "
        "names/places replaced by short codes, plus the map.\n\n"
        "<b>Export:</b> Telegram Desktop → open chat → ⋮ → Export chat "
        "history → Format <b>JSON</b> → send me <code>result.json</code>.")


def tg(method, **kw):
    return requests.post(f"{TG}/{method}", json=kw, timeout=65).json()

def send(cid, text, kb=None):
    m = {"chat_id": cid, "text": text, "parse_mode": "HTML",
         "disable_web_page_preview": True}
    if kb is not None:
        m["reply_markup"] = {"inline_keyboard": kb}
    return tg("sendMessage", **m)

def edit(cid, mid, text, kb=None):
    m = {"chat_id": cid, "message_id": mid, "text": text, "parse_mode": "HTML"}
    if kb is not None:
        m["reply_markup"] = {"inline_keyboard": kb}
    tg("editMessageText", **m)


def options_kb(st):
    scope_row = [{"text": ("✅ " if st["scope"] == s else "") + label,
                  "callback_data": "scope:" + s} for s, label in SCOPES]
    ts = st["time_shift"]
    return [scope_row,
            [{"text": ("🕑 Time shift: ON (+1w 3m)" if ts
                       else "🕑 Time shift: off"),
              "callback_data": "ts"}],
            [{"text": "▶️ Anonymize", "callback_data": "go"}]]

OPT_TEXT = ("File ready. Choose what to replace, then Anonymize.\n\n"
            "• <b>Main character</b> — only the other person's name\n"
            "• <b>All names</b> — every person's name\n"
            "• <b>Names + places</b> — people and places (typed: city/bar…)")


def user_for(msg_from):
    """Map a Telegram user to a platform user (owner rule)."""
    name = msg_from.get("username") or msg_from.get("first_name", "")
    return auth._login_tg_user(int(msg_from["id"]), name)["user"], name


def handle_document(cid, msg):
    doc = msg["document"]
    if doc.get("file_size", 0) > MAX_BYTES:
        return send(cid, "⚠️ file too big (max 20 MB).")
    info = tg("getFile", file_id=doc["file_id"]).get("result", {})
    path = info.get("file_path")
    if not path:
        return send(cid, "⚠️ could not fetch the file, try again.")
    blob = requests.get(f"https://api.telegram.org/file/bot{TOK}/{path}",
                        timeout=120).content
    _, self_name = user_for(msg["from"])
    PENDING[cid] = {"bytes": blob, "name": doc.get("file_name", "export.json"),
                    "scope": "full", "time_shift": False, "self": self_name}
    send(cid, OPT_TEXT, options_kb(PENDING[cid]))


def run_job(cid, msg_from):
    st = PENDING.get(cid)
    if not st:
        return send(cid, "Send me an export file first.")
    user, _ = user_for(msg_from)
    uid = user["id"]

    # limit gate (reuses the platform usage table)
    try:
        n = len(A.parse_export(st["bytes"])["messages"])
    except Exception:
        return send(cid, "⚠️ that doesn't look like a Telegram JSON export.")
    used = db.anon_today(uid)
    if used + n > limits.DAILY_ANON_MESSAGES:
        left = max(0, limits.DAILY_ANON_MESSAGES - used)
        return send(cid, f"⚠️ daily limit reached "
                    f"({limits.DAILY_ANON_MESSAGES} messages/day). "
                    f"{left} left today; this file has {n}.")

    status = send(cid, "⏳ anonymizing…")["result"]["message_id"]
    t0 = time.time()

    def progress(done, total):
        edit(cid, status, f"⏳ anonymizing… {done}/{total} · "
                          f"{int(time.time() - t0)}s")
    try:
        r = anonymize_export(st["bytes"], scope=st["scope"],
                             time_shift=st["time_shift"],
                             self_name=st["self"], progress=progress)
    except ValueError as e:
        return edit(cid, status, f"⚠️ {e}")
    except Exception as e:
        return edit(cid, status, f"⚠️ failed: {str(e)[:200]}")

    db.add_anon_usage(uid, n)
    edit(cid, status, f"✅ done: {r['n_messages']} messages, "
                      f"scope={st['scope']}"
                      + (", +1w3m" if st["time_shift"] else "")
                      + f" · {int(time.time() - t0)}s")
    out_name = os.path.splitext(st["name"])[0] + "_anon.txt"
    requests.post(f"{TG}/sendDocument", data={"chat_id": cid},
                  files={"document": (out_name,
                         io.BytesIO(r["txt"].encode("utf-8")), "text/plain")},
                  timeout=120)
    mp = r["map_text"] or "(nothing replaced)"
    if len(mp) < 3500:
        send(cid, "🔑 <b>Map</b>\n<pre>" + mp + "</pre>")
    else:
        requests.post(f"{TG}/sendDocument", data={"chat_id": cid},
                      files={"document": ("name_map.txt",
                             io.BytesIO(mp.encode("utf-8")), "text/plain")},
                      timeout=120)
    PENDING.pop(cid, None)


def on_callback(q):
    cid = q["message"]["chat"]["id"]
    mid = q["message"]["message_id"]
    data = q["data"]
    tg("answerCallbackQuery", callback_query_id=q["id"])
    st = PENDING.get(cid)
    if not st:
        return
    if data.startswith("scope:"):
        st["scope"] = data.split(":", 1)[1]
        edit(cid, mid, OPT_TEXT, options_kb(st))
    elif data == "ts":
        st["time_shift"] = not st["time_shift"]
        edit(cid, mid, OPT_TEXT, options_kb(st))
    elif data == "go":
        run_job(cid, q["from"])


def on_message(m):
    cid = m["chat"]["id"]
    if "document" in m:
        return handle_document(cid, m)
    txt = (m.get("text") or "").strip()
    if txt.startswith("/start"):
        user_for(m["from"])          # register the user on first contact
        return send(cid, "👋 Conversation anonymizer.\n\n" + HELP)
    return send(cid, HELP)


def main():
    offset = 0
    print("convbot up", flush=True)
    while True:
        try:
            up = requests.get(f"{TG}/getUpdates",
                              params={"offset": offset, "timeout": 25},
                              timeout=40).json()
            for u in up.get("result", []):
                offset = u["update_id"] + 1
                try:
                    if "message" in u:
                        on_message(u["message"])
                    elif "callback_query" in u:
                        on_callback(u["callback_query"])
                except Exception as e:
                    print("handler error:", e, flush=True)
        except Exception as e:
            print("poll error:", e, flush=True)
            time.sleep(5)


if __name__ == "__main__":
    main()
