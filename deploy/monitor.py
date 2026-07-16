#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
reader-vm watchdog (roadmap 2b.4): runs from cron every 5 minutes, checks
the box and the app, and messages Telegram when something is wrong — and
again when it recovers. Alerts repeat at most every 6 h while a condition
persists (no 3 a.m. spam storms).

Install (as root):
  cp deploy/monitor.py /usr/local/bin/reader_monitor.py
  echo '*/5 * * * * root /usr/bin/python3 /usr/local/bin/reader_monitor.py' \
      > /etc/cron.d/reader-monitor

Config comes from /home/denis-reader/app/.env:
  TELEGRAM_TOKEN=...   TELEGRAM_CHAT_ID=...
State (for dedup) lives in /var/lib/reader_monitor.json.
"""
import json, os, shutil, sqlite3, time, urllib.request

APP = "/home/denis-reader/app"
DB = os.path.join(APP, "data", "site", "app.db")
STATE = "/var/lib/reader_monitor.json"
REALERT_S = 6 * 3600
DISK_PCT_MAX = int(os.environ.get("MON_DISK_MAX", 85))
MEM_MB_MIN = int(os.environ.get("MON_MEM_MIN", 100))
STUCK_S = int(os.environ.get("MON_STUCK_S", 15 * 60))

def env():
    out = {}
    with open(os.path.join(APP, ".env"), encoding="utf-8") as f:
        for line in f:
            if "=" in line and not line.lstrip().startswith("#"):
                k, v = line.split("=", 1)
                out[k.strip()] = v.strip()
    return out

def tg_send(cfg, text):
    body = json.dumps({"chat_id": cfg["TELEGRAM_CHAT_ID"],
                       "text": "🖥 reader-vm: " + text}).encode()
    req = urllib.request.Request(
        f"https://api.telegram.org/bot{cfg['TELEGRAM_TOKEN']}/sendMessage",
        data=body, headers={"Content-Type": "application/json"})
    urllib.request.urlopen(req, timeout=20)

def checks():
    """Returns {check_name: problem_text or None}."""
    out = {}

    du = shutil.disk_usage("/")
    pct = du.used / du.total * 100
    out["disk"] = (f"disk {pct:.0f}% full ({du.free // 2**30} GB free)"
                   if pct > DISK_PCT_MAX else None)

    avail_kb = 0
    with open("/proc/meminfo", encoding="ascii") as f:
        for line in f:
            if line.startswith("MemAvailable:"):
                avail_kb = int(line.split()[1])
                break
    out["memory"] = (f"only {avail_kb // 1024} MB memory available"
                     if avail_kb // 1024 < MEM_MB_MIN else None)

    try:
        with urllib.request.urlopen("http://localhost/health",
                                    timeout=10) as r:
            ok = json.load(r).get("status") == "ok"
        out["api"] = None if ok else "API /health returned bad status"
    except Exception as e:
        out["api"] = f"API /health unreachable: {str(e)[:80]}"

    out["job_stuck"] = out["job_failed"] = None
    if os.path.exists(DB):
        try:
            con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True, timeout=10)
            row = con.execute(
                "SELECT j.id, MAX(e.ts) FROM jobs j LEFT JOIN job_events e "
                "ON e.job_id = j.id WHERE j.status='running'").fetchone()
            if row and row[0] is not None:
                last = row[1] or 0
                if time.time() - last > STUCK_S:
                    out["job_stuck"] = (f"job {row[0]} running but silent "
                                        f"for {int(time.time()-last)//60} min")
            bad = con.execute(
                "SELECT id, status, error FROM jobs WHERE status IN "
                "('error','quota') AND finished_at > ?",
                (time.time() - 600,)).fetchall()
            if bad:
                j = bad[0]
                out["job_failed"] = (f"job {j[0]} ended '{j[1]}': "
                                     f"{(j[2] or '')[:120]}")
            con.close()
        except Exception as e:
            out["job_stuck"] = f"cannot read jobs db: {str(e)[:80]}"
    return out

def main():
    cfg = env()
    if not cfg.get("TELEGRAM_TOKEN") or not cfg.get("TELEGRAM_CHAT_ID"):
        return
    state = {}
    if os.path.exists(STATE):
        state = json.load(open(STATE, encoding="utf-8"))
    now = time.time()

    for name, problem in checks().items():
        prev = state.get(name, {})
        if problem:
            if not prev.get("bad") or now - prev.get("sent", 0) > REALERT_S:
                try:
                    tg_send(cfg, f"⚠️ {problem}")
                    state[name] = {"bad": True, "sent": now}
                except Exception:
                    state[name] = {"bad": True, "sent": 0}
            else:
                state[name]["bad"] = True
        else:
            if prev.get("bad"):
                try:
                    tg_send(cfg, f"✅ {name} recovered")
                except Exception:
                    pass
            state[name] = {"bad": False, "sent": 0}

    os.makedirs(os.path.dirname(STATE), exist_ok=True)
    with open(STATE, "w", encoding="utf-8") as f:
        json.dump(state, f)

if __name__ == "__main__":
    main()
