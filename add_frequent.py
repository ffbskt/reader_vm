# -*- coding: utf-8 -*-
"""
Add the most frequent unknown Book 2 words to the allowed vocabulary
(data/extra_known.txt), so they count as Book 1 words after re-analysis.

  add_frequent.py --top 100 --min-freq 3 [--dry-run]

Proper nouns are always excluded. After running, refresh the board:
  analyze.py  then  build_board.py     (no API calls involved)
"""
import sys, os, json, argparse, datetime

sys.stdout.reconfigure(encoding="utf-8")
HERE = os.path.dirname(os.path.abspath(__file__))

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--top", type=int, default=100, help="take at most N words")
    ap.add_argument("--min-freq", type=int, default=3,
                    help="only words repeating more than this many times")
    ap.add_argument("--dry-run", action="store_true", help="print, do not write")
    args = ap.parse_args()

    board = json.load(open(os.path.join(HERE, "data", "board_data.json"),
                           encoding="utf-8"))
    extra_path = os.path.join(HERE, "data", "extra_known.txt")
    already = set()
    if os.path.exists(extra_path):
        already = {l.split("#")[0].strip().lower()
                   for l in open(extra_path, encoding="utf-8")}

    cands = [w for w in board["book2_words"]
             if not w["known"] and w["n"] >= args.min_freq
             and w["w"] not in already]
    cands.sort(key=lambda x: -x["n"])
    take = cands[:args.top]
    if not take:
        print("nothing new to add")
        return

    # projected coverage gain
    tok_total = sum(w["n"] for w in board["book2_words"])
    tok_known = sum(w["n"] for w in board["book2_words"] if w["known"])
    gain = sum(w["n"] for w in take)
    print(f"adding {len(take)} words (freq >= {args.min_freq}), "
          f"projected token coverage: "
          f"{tok_known/tok_total:.1%} -> {(tok_known+gain)/tok_total:.1%}")
    for w in take:
        print(f"  {w['w']:20} x{w['n']}")

    if args.dry_run:
        print("(dry run, nothing written)")
        return
    with open(extra_path, "a", encoding="utf-8") as f:
        f.write(f"# added {datetime.date.today()} top={args.top} "
                f"min_freq={args.min_freq}\n")
        for w in take:
            f.write(f"{w['w']}  # x{w['n']}\n")
    print(f"written to {extra_path} - now run analyze.py and build_board.py")

if __name__ == "__main__":
    main()
