# -*- coding: utf-8 -*-
"""
Telegram bot (roadmap 2b.3) — a thin client of the Reader API.
Long polling, no public port. Per chat: current book + level, kept in
data/site/tg_state.json. Each Telegram user maps to an API user via
POST /auth/telegram (shared bot secret); the owner's tg id claims user 1.

Flow: send a PDF/TXT -> choose "book to read" or "known vocabulary" ->
/stats -> /level -> /translate 41 45 (progress edits one message) ->
/read link, /pdf -> document reply.
"""
import io, json, os, time
import requests

TOK = os.environ["TELEGRAM_TOKEN"]
API = os.environ.get("API_URL", "http://api:8100")
SECRET = os.environ["TELEGRAM_BOT_SECRET"]
READER = os.environ.get("READER_URL", "https://readersimple.duckdns.org")
TG = f"https://api.telegram.org/bot{TOK}"
STATE_FP = "/app/data/site/tg_state.json"
LEVELS = (0, 25, 50, 75)
MODES = ("spaced", "repeat", "norepeat", "clean")

state = json.load(open(STATE_FP, encoding="utf-8")) \
    if os.path.exists(STATE_FP) else {}

def save():
    with open(STATE_FP, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False)

def st(cid):
    return state.setdefault(str(cid), {"level": 25})

def tg(method, **kw):
    r = requests.post(f"{TG}/{method}", json=kw, timeout=65)
    return r.json()

def send(cid, text, kb=None):
    kw = {"chat_id": cid, "text": text, "parse_mode": "HTML",
          "disable_web_page_preview": True}
    if kb:
        kw["reply_markup"] = {"inline_keyboard": kb}
    return tg("sendMessage", **kw)

def jwt_for(cid, name):
    s = st(cid)
    if "jwt" not in s:
        r = requests.post(f"{API}/auth/telegram", json={
            "tg_id": cid, "name": name, "bot_secret": SECRET}, timeout=30)
        r.raise_for_status()
        s["jwt"] = r.json()["token"]
        save()
    return s["jwt"]

def api(cid, name, method, path, **kw):
    h = {"Authorization": "Bearer " + jwt_for(cid, name)}
    r = requests.request(method, API + path, headers=h, timeout=300, **kw)
    if r.status_code == 401:                     # expired -> re-login once
        st(cid).pop("jwt", None)
        h = {"Authorization": "Bearer " + jwt_for(cid, name)}
        r = requests.request(method, API + path, headers=h, timeout=300, **kw)
    return r

def book_lines(cid, name):
    books = api(cid, name, "GET", "/books").json().get("books", [])
    s = st(cid)
    out, kb = [], []
    for b in books:
        cur = " ✅" if b["slug"] == s.get("book") else ""
        done = ", ".join(f"L{l}:{n}p" for l, n in b["done_pages"].items() if n)
        out.append(f"• <b>{b['title']}</b> — {b['pages']} p. "
                   f"({done or 'not translated'}){cur}")
        kb.append([{"text": b["title"], "callback_data": "book:" + b["slug"]}])
    return "\n".join(out) or "no books yet — send me a PDF or TXT", kb

HELP = ("Send a <b>PDF or TXT file</b> to add a book.\n"
        "/books — choose the current book\n"
        "/stats — coverage of the current book\n"
        "/level — choose difficulty (0/25/50/75)\n"
        "/translate 41 45 — translate a page range\n"
        "/read — link to the online reader\n"
        "/pdf — build and get the PDF here")

def on_message(m):
    cid = m["chat"]["id"]
    name = m["from"].get("username") or m["from"].get("first_name", "")
    s = st(cid)
    txt = (m.get("text") or "").strip()

    if "document" in m:
        s["file"] = {"id": m["document"]["file_id"],
                     "name": m["document"].get("file_name", "book.pdf")}
        save()
        return send(cid, f"Got <b>{s['file']['name']}</b>. What is it?", [[
            {"text": "📕 Book to read", "callback_data": "up:book"},
            {"text": "🧠 Known vocabulary", "callback_data": "up:known"}]])

    if txt.startswith("/start") or txt.startswith("/help"):
        jwt_for(cid, name)
        return send(cid, "📚 <b>Reader</b> — leveled book simplifier.\n\n"
                    + HELP + f"\n\nWeb app: {READER}/app.html")
    if txt.startswith("/books"):
        lines, kb = book_lines(cid, name)
        return send(cid, lines, kb)
    if txt.startswith("/stats"):
        if not s.get("book"):
            return send(cid, "choose a book first: /books")
        d = api(cid, name, "GET", f"/books/{s['book']}/stats").json()
        lv = "\n".join(f"level {l['level']}: {l['kept_types']} words stay, "
                       f"≈{l['unk_pct_after']}% unknown text"
                       for l in d["levels"])
        return send(cid, f"<b>{s['book']}</b>: {d['pages']} pages, "
                    f"{d['token_coverage']}% covered by your "
                    f"{d['known_words']} known words, "
                    f"{d['unknown_types']} unknown types.\n{lv}")
    if txt.startswith("/level"):
        return send(cid, f"current level: {s.get('level', 25)}", [[
            {"text": ("✅ " if s.get("level") == l else "") + str(l),
             "callback_data": f"lvl:{l}"} for l in LEVELS]])
    if txt.startswith("/translate"):
        if not s.get("book"):
            return send(cid, "choose a book first: /books")
        parts = txt.split()
        if len(parts) < 3:
            return send(cid, "usage: /translate 41 45")
        r = api(cid, name, "POST", f"/books/{s['book']}/translate",
                json={"level": s.get("level", 25),
                      "from": int(parts[1]), "to": int(parts[2])})
        if r.status_code != 202:
            return send(cid, "⚠️ " + str(r.json().get("detail", r.text)[:200]))
        job = r.json()
        msg = send(cid, "⏳ queued…")["result"]["message_id"]
        s["job"] = {"id": job["id"], "msg": msg}
        save()
        return
    if txt.startswith("/read"):
        if not s.get("book"):
            return send(cid, "choose a book first: /books")
        return send(cid, f"📖 {READER}/reader_site.html?"
                         f"book={s['book']}&level={s.get('level', 25)}")
    if txt.startswith("/pdf"):
        if not s.get("book"):
            return send(cid, "choose a book first: /books")
        return send(cid, "PDF vocabulary mode:", [[
            {"text": mo, "callback_data": "pdf:" + mo} for mo in MODES]])
    return send(cid, HELP)

def on_callback(q):
    cid = q["message"]["chat"]["id"]
    name = q["from"].get("username") or q["from"].get("first_name", "")
    s = st(cid)
    kind, _, val = q["data"].partition(":")
    tg("answerCallbackQuery", callback_query_id=q["id"])

    if kind == "book":
        s["book"] = val
        save()
        return send(cid, f"current book: <b>{val}</b>\n/stats /level "
                         f"/translate /read /pdf")
    if kind == "lvl":
        s["level"] = int(val)
        save()
        return send(cid, f"level set to <b>{val}</b>")
    if kind == "up" and s.get("file"):
        f = s.pop("file")
        save()
        send(cid, "⏳ downloading and analyzing…")
        info = tg("getFile", file_id=f["id"])["result"]
        blob = requests.get(
            f"https://api.telegram.org/file/bot{TOK}/{info['file_path']}",
            timeout=120).content
        kind_q = "books" if val == "book" else "known"
        r = api(cid, name, "POST", f"/{kind_q}?name={f['name']}", data=blob)
        d = r.json()
        if r.status_code != 200:
            return send(cid, "⚠️ " + str(d.get("detail", d))[:200])
        if val == "book":
            s["book"] = d["slug"]
            save()
            return send(cid, f"📕 <b>{d['title']}</b>: {d['pages']} pages. "
                             f"Now /stats, /level, then /translate.")
        return send(cid, f"🧠 added, {d['total_known']} known words total.")
    if kind == "pdf":
        send(cid, f"⏳ building {val} PDF…")
        r = api(cid, name, "GET",
                f"/books/{s['book']}/pdf?level={s.get('level', 25)}&mode={val}")
        if r.status_code != 200:
            return send(cid, "⚠️ " + str(r.json().get("detail", ""))[:200])
        requests.post(f"{TG}/sendDocument", data={"chat_id": cid},
                      files={"document":
                             (f"{s['book']}_L{s.get('level', 25)}_{val}.pdf",
                              io.BytesIO(r.content), "application/pdf")},
                      timeout=120)

def poll_jobs():
    """Edit one message per running job with live progress."""
    for cid, s in list(state.items()):
        j = s.get("job")
        if not j:
            continue
        try:
            d = api(int(cid), "", "GET", f"/jobs/{j['id']}").json()
        except Exception:
            continue
        bar = "▓" * (d["pct"] // 10) + "░" * (10 - d["pct"] // 10)
        eta = f" ~{d['eta_s']//60}m{d['eta_s']%60:02d}s left" \
            if d.get("eta_s") else ""
        tg("editMessageText", chat_id=int(cid), message_id=j["msg"],
           text=f"{bar} {d['pct']}% — {d['done']}/{d['total']} pages"
                f"{eta} ({d['status']})")
        if d["status"] not in ("queued", "running"):
            s.pop("job", None)
            save()
            extra = f"\n⚠️ {d['error']}" if d.get("error") else ""
            send(int(cid), f"✅ job {d['status']}: {d['done']}/{d['total']} "
                 f"pages ({d['cached']} were cached, free).{extra}\n"
                 f"/read — open the reader, /pdf — get the PDF")

def main():
    offset = 0
    last_jobs = 0
    print("bot up", flush=True)
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
            if time.time() - last_jobs > 4:
                last_jobs = time.time()
                poll_jobs()
        except Exception as e:
            print("poll error:", e, flush=True)
            time.sleep(5)

if __name__ == "__main__":
    main()
