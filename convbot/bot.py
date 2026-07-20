# -*- coding: utf-8 -*-
"""
@Conversation_download_bot — send it a Telegram chat export (the JSON from
Telegram Desktop -> Export chat history -> Format: JSON) and it returns the
same conversation with every person and place name replaced by a short code,
timestamps kept, plus the code->name map. Long polling, no public port.

Runs on reader-vm as its own container. Token: CONVERSATION_BOT_TOKEN.
No Telegram account session is used — the user chooses the chat by exporting
it, so the bot never needs access to their account.
"""
import io, os, sys, time
import requests

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from convbot.run import anonymize_export

TOK = os.environ["CONVERSATION_BOT_TOKEN"]
TG = f"https://api.telegram.org/bot{TOK}"
MAX_BYTES = 20 * 1024 * 1024

HELP = ("Send me a Telegram chat export and I'll return it with names and "
        "places replaced by short codes (timestamps kept), plus the map.\n\n"
        "How to export: open the chat in <b>Telegram Desktop</b> → ⋮ → "
        "<b>Export chat history</b> → Format <b>JSON</b> → send me the "
        "<code>result.json</code> file.")


def tg(method, **kw):
    return requests.post(f"{TG}/{method}", json=kw, timeout=65).json()

def send(cid, text):
    return tg("sendMessage", chat_id=cid, text=text, parse_mode="HTML",
              disable_web_page_preview=True)

def edit(cid, mid, text):
    tg("editMessageText", chat_id=cid, message_id=mid, text=text,
       parse_mode="HTML")


def handle_document(cid, doc):
    name = doc.get("file_name", "export.json")
    if doc.get("file_size", 0) > MAX_BYTES:
        return send(cid, "⚠️ file too big (max 20 MB).")
    info = tg("getFile", file_id=doc["file_id"]).get("result", {})
    path = info.get("file_path")
    if not path:
        return send(cid, "⚠️ could not fetch the file, try again.")
    blob = requests.get(f"https://api.telegram.org/file/bot{TOK}/{path}",
                        timeout=120).content

    status = send(cid, "⏳ anonymizing…")["result"]["message_id"]
    t0 = time.time()

    def progress(done, total):
        el = int(time.time() - t0)
        edit(cid, status, f"⏳ anonymizing… {done}/{total} messages · {el}s")

    try:
        r = anonymize_export(blob, progress=progress)
    except ValueError as e:
        return edit(cid, status, f"⚠️ {e}")
    except Exception as e:
        return edit(cid, status, f"⚠️ failed: {str(e)[:200]}")

    edit(cid, status, f"✅ done: {r['n_messages']} messages in "
                      f"{int(time.time() - t0)}s")
    # the anonymized transcript as a .txt document
    out_name = os.path.splitext(name)[0] + "_anon.txt"
    requests.post(f"{TG}/sendDocument", data={"chat_id": cid},
                  files={"document": (out_name,
                         io.BytesIO(r["txt"].encode("utf-8")), "text/plain")},
                  timeout=120)
    # the code -> name map (may be long -> as its own file if needed)
    mp = r["map_text"] or "(no names detected)"
    if len(mp) < 3500:
        send(cid, "🔑 <b>Name map</b>\n<pre>" + mp + "</pre>")
    else:
        requests.post(f"{TG}/sendDocument", data={"chat_id": cid},
                      files={"document": ("name_map.txt",
                             io.BytesIO(mp.encode("utf-8")), "text/plain")},
                      timeout=120)


def on_message(m):
    cid = m["chat"]["id"]
    if "document" in m:
        return handle_document(cid, m["document"])
    txt = (m.get("text") or "").strip()
    if txt.startswith("/start"):
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
                except Exception as e:
                    print("handler error:", e, flush=True)
        except Exception as e:
            print("poll error:", e, flush=True)
            time.sleep(5)


if __name__ == "__main__":
    main()
