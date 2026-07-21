# -*- coding: utf-8 -*-
"""
Autonomous, self-paced BASELINE level-0 translator for the owner's shared
library (the public-domain classics). Runs from cron every 30 min inside the
api container; a daily budget keeps it FAR from every limit.

Baseline level 0 = generic A1 simplification, no vocabulary needed — the
whole book becomes readable with no per-user setup.

Budget math (defaults): 80 pages/day, 8 per run, 6 s between Gemini calls.
  - Gemini free tier is ~1000+ requests/day and ~15/min. 80/day is ~8% of
    the daily cap; 6 s gap = 10/min < 15/min. Comfortably far from limits.
  - Also under the app's own 100 pages/user/day quota, leaving headroom for
    manual use.
State: data/site/auto_translate.json {date, done}. Lock via cron `flock`.
"""
import json, os, sys, time
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from core import pipeline
from core.pipeline import simplify_page_baseline, QuotaError

BUDGET = int(os.environ.get("AUTO_DAILY_PAGES", "80"))
BATCH = int(os.environ.get("AUTO_BATCH", "8"))
GAP = int(os.environ.get("AUTO_GAP_S", "6"))
LEVEL, OWNER = 0, 1
STATE = os.path.join(pipeline.SITE, "auto_translate.json")

def load():
    if os.path.exists(STATE):
        try:
            return json.load(open(STATE, encoding="utf-8"))
        except ValueError:
            pass
    return {"date": "", "done": 0}

def save(s):
    json.dump(s, open(STATE, "w", encoding="utf-8"))

def main():
    pipeline.set_user(OWNER)
    today = time.strftime("%Y-%m-%d")
    st = load()
    if st.get("date") != today:
        st = {"date": today, "done": 0}
    if st["done"] >= BUDGET:
        print(f"{today}: daily budget {BUDGET} reached")
        return

    # collect pages still missing a baseline-L0 translation
    todo = []
    for b in pipeline.list_books():
        have = set(pipeline.cached_pages(b["slug"], LEVEL, baseline=True))
        todo += [(b["slug"], p) for p in range(1, b["pages"] + 1)
                 if p not in have]
    if not todo:
        print(f"{today}: all books fully baseline-translated ✓")
        return

    made = 0
    for slug, page in todo:
        if st["done"] >= BUDGET or made >= BATCH:
            break
        try:
            _, cached = simplify_page_baseline(slug, page, LEVEL)
            if not cached:
                st["done"] += 1
                made += 1
                save(st)
                time.sleep(GAP)
        except QuotaError as e:
            print(f"{today}: Gemini quota hit, stopping for now: {e}")
            break
        except Exception as e:            # blank/short pseudo-pages: skip free
            print(f"skip {slug} p{page}: {str(e)[:60]}")
    print(f"{today}: +{made} pages this run, {st['done']}/{BUDGET} today, "
          f"{len(todo)} pages still pending across the library")

if __name__ == "__main__":
    main()
