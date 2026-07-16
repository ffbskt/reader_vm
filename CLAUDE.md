# Translation_simplify_app

Docs: `docs/ARCHITECTURE.md` = target multi-user design (API, limits,
phases); `docs/ROADMAP.md` = current phase + next session's task. Read the
roadmap when the user asks to continue the project.

Pipeline that rewrites hard texts (classic literature) using only vocabulary the
learner already knows (from a textbook). Book 1 = Easy Spanish step-by-step
(known words), Book 2 = La Celestina (target text).

## Cost policy — IMPORTANT

- **Never call paid/expensive APIs without a direct command from the user.**
  This includes bulk Gemini runs (whole book), any paid tier, or any new
  external service. "Direct command" means the user explicitly asks for that
  specific run in the current conversation.
- Default to the free tier and the cheapest model (`gemini-2.0-flash-lite` /
  `gemini-2.5-flash-lite`). Prefer one batched request over many small ones.
- Test on the smallest useful unit first (one page, one batch) and show the
  result before scaling up.
- The API key lives in `gemini_key.txt` (gitignored if repo is ever created) or
  the `GEMINI_API_KEY` env var. Never print or commit the key.

## Files

- `extract_text.py <pdf> <out.txt>` — PDF -> page-marked text in `data/`
- `analyze.py [--gemini]` — vocabulary coverage + pairings -> `data/board_data.json`
- `simplify_page.py --page N [--method M] [--pct P]` — Gemini-simplify one page
  -> `data/simplified/`. Results are cached there: an existing combination is
  loaded from disk with NO API request (`--force` to recompute).
- `server.py` — serves the board on port 8642 plus `GET /api/simplify` (live
  simplify from the board UI; cached combos are free, a new combo = 1 request)
- `build_board.py` — injects data + all cached simplified pages into
  `board_template.html` -> `board.html`
- `build_pdf.py [--from N --to M]` — learner PDF from the buffer (per page:
  simplified dialog text, then vocabulary es-en-ru under it; repeated words
  underlined). No API calls.
- `build_reader.py` — reader.html: page-by-page HTML reader, hover word
  translations EN/RU, PDF download button. Artifact:
  https://claude.ai/code/artifact/e18236f3-2b60-4376-85db-da51b92998ec
- `vocab_common.py` — shared page-vocabulary logic (all in-text unknown words,
  alphabetical; modes: repeat / norepeat / spaced / clean) used by PDF, reader,
  and pdf.html. build_pdf also underlines vocab words inside the text; clean
  mode = continuous text, no title page/headers/vocab/underlines.
- `build_pdfui.py` — pdf.html: PDF-builder page (mode choice, unknown-%
  stats, download via server `GET /api/build_pdf?mode=`). Artifact:
  https://claude.ai/code/artifact/aa248f4d-e316-467f-a38c-35e8df52787e
- `night_run.py` — batch simplify pages 41-90 + PDF + board; resume-safe.
  Scheduled once via Windows Task Scheduler ("CelestinaNightRun", 2026-07-15
  01:30, user-commanded). Log: `data/night_run.log`.
- Board artifact: https://claude.ai/code/artifact/3e42e3b0-cad9-4d93-8309-ec345c5b56a1

## Local site (generic pipeline, any books)

- `pipeline.py` — engine behind the site: known-vocab sources (uploaded
  PDF books freq>=2 or word lists), target books, coverage stats, LEVELED
  simplify (level 0/25/50/75 = % of unknown TOKENS allowed to remain: the most
  frequent unknown types are kept until they cover that share of unknown
  occurrences — token-based because Zipf makes a type-based cut feel flat;
  only these discrete levels so the cache never pays twice),
  background translate job (sequential free-tier calls, 5 s gap, progress/ETA,
  stops on 429; ends with a gap-fill pass: one batched request translates any
  words the page vocabs missed -> books/<slug>/word_dict.json, keyed on the
  exact inflected form). Hover lookup (vocab_common.lookup + both readers'
  JS) does morphology: attached pronouns, plurals, verb endings -> infinitive,
  ie/ue stem changes. Data: `data/site/known/*.json`,
  `data/site/books/<slug>/{book.txt,meta.json,job.json,simplified/page<N>_L<lvl>.json}`.
- `app.html` — the site (http://localhost:8642/app.html): 6-step wizard
  (known vocab -> book upload + stats/plots -> level -> translate w/ progress ->
  reader link -> PDF in 4 modes). Static file, all data via `/api/site/*`.
- `reader_site.html` — generic online reader (?book=&level=): hover EN/RU
  translations, per-page alphabetical vocab.
- Site API in `server.py`: POST `/api/site/upload?kind=known|book&name=`
  (raw file body), GET `known / known_delete / books / stats / translate /
  job / reader_data / build_pdf`. `translate` is user-initiated (their click
  = the direct command); cached pages are returned free.
- `build_pdf.py` extra args for site books: `--dir --pattern --title --author
  --known-note` (defaults keep the Celestina behavior).

## Run

Python: `C:\Users\Denis\anaconda3\python.exe` (no `python` on PATH).
Preview server: `.claude/launch.json` -> `board` runs `server.py` (port 8642).
The published artifact is a static snapshot: its Simplify tab serves only
buffered results; live simplification needs the local server.
