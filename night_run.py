# -*- coding: utf-8 -*-
"""
Overnight batch (user-commanded 2026-07-14): simplify La Celestina pages 41-90
with per-page vocabulary (es-en-ru), then build the learner PDF and the board.

- one free-tier Gemini request per page, paced PAUSE seconds apart
- already-cached pages with vocabulary are skipped (resume-safe checkpointing)
- on rate-limit errors waits and retries; aborts cleanly if quota is exhausted
  (rerunning this script resumes where it stopped)

Log: data/night_run.log
Output: data/celestina_simplified_41_90.pdf
"""
import sys, os, json, time, datetime, subprocess, traceback

sys.stdout.reconfigure(encoding="utf-8")
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from simplify_page import simplify, cache_path, QuotaError

P_FROM, P_TO = 41, 90
METHOD, PCT = "rewrite", 100
PAUSE = 20            # seconds between API requests (free-tier RPM safety)
RETRY_WAIT = 600      # after a rate-limit error
MAX_RETRIES = 3

LOG = os.path.join(HERE, "data", "night_run.log")

def log(msg):
    line = f"[{datetime.datetime.now():%Y-%m-%d %H:%M:%S}] {msg}"
    print(line)
    with open(LOG, "a", encoding="utf-8") as f:
        f.write(line + "\n")

FMT = 2   # current result format: vocab + per-sentence speaker fields

def has_vocab(page):
    """None = not computed, False = stale format (refresh), True = complete."""
    fp = cache_path(page, METHOD, PCT)
    if not os.path.exists(fp):
        return None
    r = json.load(open(fp, encoding="utf-8"))
    return r.get("fmt", 1) >= FMT and bool(r.get("vocab"))

def main():
    log(f"night run start: pages {P_FROM}-{P_TO}, {METHOD} {PCT}%")
    done = skipped = failed = 0
    for page in range(P_FROM, P_TO + 1):
        state = has_vocab(page)
        if state is True:
            skipped += 1
            continue
        force = state is False            # cached but from before vocab existed
        for attempt in range(1, MAX_RETRIES + 1):
            try:
                result, cached = simplify(page, METHOD, PCT, force=force)
                log(f"page {page}: ok ({result['coverage_before']}%->"
                    f"{result['coverage_after']}%, "
                    f"{len(result.get('vocab', []))} vocab words)"
                    + (" [refreshed]" if force else ""))
                done += 1
                time.sleep(PAUSE)
                break
            except ValueError as e:       # blank/illustration page etc.
                log(f"page {page}: skipped - {e}")
                skipped += 1
                break
            except QuotaError as e:
                if attempt < MAX_RETRIES:
                    log(f"page {page}: rate limited, waiting "
                        f"{RETRY_WAIT}s (attempt {attempt}) - {str(e)[:220]}")
                    time.sleep(RETRY_WAIT)
                    continue
                log(f"page {page}: quota exhausted after {attempt} tries - "
                    f"aborting; rerun to resume - {str(e)[:220]}")
                failed += 1
                return finish(done, skipped, failed)
            except Exception as e:
                log(f"page {page}: FAILED - {str(e)[:300]}")
                log(traceback.format_exc(limit=2))
                failed += 1
                time.sleep(PAUSE)
                break
    finish(done, skipped, failed)

def finish(done, skipped, failed):
    log(f"requests done={done} skipped={skipped} failed={failed}")
    py = sys.executable
    for cmd in (
        [py, os.path.join(HERE, "build_pdf.py"), "--from", str(P_FROM),
         "--to", str(P_TO), "--out",
         os.path.join("data", "celestina_simplified_41_90.pdf")],
        [py, os.path.join(HERE, "build_reader.py"), "--from", str(P_FROM),
         "--to", str(P_TO)],
        [py, os.path.join(HERE, "build_pdfui.py"), "--from", str(P_FROM),
         "--to", str(P_TO)],
        [py, os.path.join(HERE, "build_board.py")],
    ):
        try:
            r = subprocess.run(cmd, capture_output=True, text=True,
                               encoding="utf-8", cwd=HERE, timeout=600)
            log(f"{os.path.basename(cmd[1])}: rc={r.returncode} "
                f"{(r.stdout or '').strip().splitlines()[-1] if r.stdout else ''}")
        except Exception as e:
            log(f"{os.path.basename(cmd[1])}: error {e}")
    log("night run finished")

if __name__ == "__main__":
    main()
