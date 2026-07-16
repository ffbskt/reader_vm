# Reader — leveled book simplifier

Rewrite hard books (classic literature) using only the vocabulary a learner
already knows, at four difficulty levels. Upload the books you have read (or
a word list), upload the book you want to read, pick a level, and get an
online reader with hover translations (EN/RU) plus a printable PDF in four
vocabulary modes.

- Level 0/25/50/75 = how much of the unknown text is allowed to remain
  (token-based — see `docs/ARCHITECTURE.md` for why type-based cuts fail).
- Translations run as a resume-safe background job on the Gemini free tier,
  cached per (book, page, level) so nothing is ever paid for twice.
- PDF modes: full vocabulary, no repeats, spaced repetition, clean text.

## Run locally

```
python server.py          # -> http://localhost:8642/app.html
```

Python 3.9+, `pip install fpdf2 pypdf requests`. Gemini API key in
`gemini_key.txt` / `API_KEY.txt` (gitignored) or the `GEMINI_API_KEY` env var.

## Docs

- `docs/ARCHITECTURE.md` — target multi-user design (API, quotas, payments)
- `docs/ROADMAP.md` — phased plan with checks; current status lives here
- `CLAUDE.md` — working notes for AI-assisted development
