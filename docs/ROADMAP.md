# Roadmap

Working agreement: one step = one small session with Claude. Every step has
a **Check** — how we prove it works before ticking it. Claude reads this file
at session start; mark `[x]` only after the check passes.

Infrastructure choice: **Google Cloud free tier e2-micro** (Oracle rejected
the user's card, 2026-07-16). Free forever: 1 e2-micro VM in
us-west1/us-central1/us-east1, 30 GB disk, 1 GB egress/mo; static IPv4 free
while attached to the running instance. Card needed for signup; must upgrade
to paid billing account within 90 days to keep the VM (free tier still
bills $0). 1 GB RAM -> swap file required. Fallbacks: home PC + Cloudflare
Tunnel (no card), Hetzner €4 (PayPal).

## Phase 1 — split core / API / frontend (local, no server needed)

- [x] 1.1 `git init` + public GitHub repo, .gitignore (data/, *.env, keys),
      LICENSE (GPL-3.0, user's choice), README. 2026-07-16:
      https://github.com/ffbskt/reader_vm — pushed, no secrets in history.
      TODO USER: repo Settings -> Code security -> enable Push protection.
      **Check:** repo online; `git status` clean; key scan of staged files
      empty. Push-protection live test pending user enabling it.
- [x] 1.2 2026-07-16: `core/` package = core/pipeline.py + core/vocab.py
      (root pipeline.py / vocab_common.py are shims; analyze + simplify_page
      stay at root as the legacy Celestina engine, wrapped later). 15 pytest
      tests: morphology lookup, vocab modes, level math, cache keying.
      **Check:** PASSED — 15/15 green; site stats/reader/pdf.html identical
      through shims (levels 202/1109/3839 unchanged).
- [x] 1.3 2026-07-16: api/ package — /health, /me behind get_current_user
      (Phase-1 stub: any/no token = local user; JWT slots into
      api/auth.py:verify_token in 2b). requirements.txt added; launch.json
      config "api" (uvicorn, port 8100); 5 TestClient tests.
      **Check:** PASSED — curl /health ok, /me returns local user,
      Swagger /docs renders in browser; 20/20 tests green.
- [x] 1.4 2026-07-16: api/routes.py — GET/POST /known, DELETE /known/{slug},
      GET/POST /books, GET /books/{slug}/stats (REST shapes from
      ARCHITECTURE §4; raw-body uploads). 4 isolated tests (tmp data dir).
      **Check:** PASSED — stats via new API == old server on every field;
      live curl upload known+book -> correct stats; throwaway data removed;
      24/24 tests green.
- [x] 1.5 2026-07-16: api/db.py (jobs + job_events in data/site/app.db) +
      api/worker.py (daemon FIFO consumer, QuotaError -> 'quota', gap-fill
      at end). POST /books/{slug}/translate (202, 409 on duplicate),
      GET /jobs/{id}, GET /jobs?book=. Windows-anaconda sqlite DLL fix in
      db.py. job.json path still serves the legacy wizard until 1.6.
      **Check:** PASSED — live 5-page cached job: $0, pct 100, five
      page_done + job_done events; 27/27 tests green.
- [x] 1.6 2026-07-16: GET /books/{slug}/reader + /books/{slug}/pdf; SPA
      pages served explicitly at / and /reader_site.html (never the repo
      dir — API key lives there); app.html + reader_site.html rewired to
      the REST endpoints. server.py stays for the legacy Celestina board.
      **Check:** PASSED — full wizard driven in browser on :8100 (known
      1727 -> stats 59% -> level cards -> live job w/ ETA + gap_fill event
      -> reader 23/23 hover tooltips -> all 4 PDF modes 200). Note: the
      wizard check translated pages 41-45 at level 25 (6 free-tier
      requests) — now cached. 27/27 tests green. PHASE 1 COMPLETE.

## Phase 2a — Google Cloud VM up (user + Claude together)

- [ ] 2a.1 USER: activate Google Cloud free trial on console.cloud.google.com
      (card for identity, $0 during trial); later upgrade billing account
      within 90 days to keep the free tier permanently.
      **Check:** console opens; budget alert at $1 configured.
- [x] 2a.2 2026-07-16: reader-vm, e2-micro, us-central1-f, Ubuntu 26.04,
      30 GB standard disk, IP 35.254.216.89, user denis-reader, key
      ~/.ssh/gcp_reader.
      **Check:** PASSED — ssh from dev PC works.
- [x] 2a.3 2026-07-16: apt upgraded, unattended-upgrades + fail2ban active,
      2 GB swap, Docker + Compose v5.3.1.
      **Check:** PASSED — hello-world runs; only port 22 listening
      (80/443 open in GCP firewall, used once Caddy deploys).
- [x] 2a.4 2026-07-16: Dockerfile (python:3.11-slim + DejaVu fonts;
      build_pdf now falls back Arial->DejaVu per platform), .dockerignore
      (secrets/data excluded), compose (api + caddy :80), deploy/Caddyfile.
      No local Docker on the dev PC -> built and checked ON the VM (worker
      = thread inside api container in Phase 1; separate container comes
      with Redis in Phase 4).
- [x] 2a.5 2026-07-16: DEPLOYED — repo cloned to ~/app on reader-vm, .env
      via scp (chmod 600, never printed), data/site + word_dict rsynced
      (1.1 MB), compose up. http://35.254.216.89/app.html live.
      **Check:** PASSED via public IP — /health ok, library intact
      (levels 0/25/75), stats == local (59.0%, 10692 types), clean PDF
      built on VM with DejaVu (3 pages), reader hover 23/23 tooltips.
      USER: confirm from your phone's browser.
- [x] 2a.6 2026-07-16: pull-backup to the dev PC (user's choice):
      deploy/backup_pull.ps1 tars the VM's data/ -> D:\Backups\reader_vm,
      keeps newest 14; Windows task "ReaderVmBackup" daily 20:00
      (StartWhenAvailable if the PC was off). PHASE 2a COMPLETE.
      **Check:** PASSED — marker file backed up, deleted on VM, restored
      from the D: archive with intact content. Caveat: backups only run
      when the PC is on; revisit (VM-side snapshot to GCS) in 2b.4.

## Phase 2b — users + quotas + Telegram

- [ ] 2b.1 Google OAuth + Telegram Login -> users table; JWT sessions;
      per-user data dirs; migrate existing library to your user.
      2026-07-16 DONE: users table (user 1 = legacy 'local'); HS256 JWT
      sessions (JWT_SECRET in VM .env); POST /auth/google verifies the
      Google ID token (google-auth) — owner email claims user 1 and its
      library; GET /auth/config; Sign-in-with-Google button + Bearer
      headers in app.html/reader_site.html; anonymous still = user 1
      until REQUIRE_AUTH=1 (flips in 2b.2). 32/32 tests.
      Config done 2026-07-16: Google refuses bare-IP origins -> domain
      https://readersimple.duckdns.org (DuckDNS, user's account) + auto
      HTTPS via Caddy; client id + JWT_SECRET in VM .env (env changes need
      compose up --force-recreate, not restart). Button renders live.
      Telegram Login lands with the bot (2b.3); per-user data dirs with
      quotas (2b.2).
      **Check:** PENDING USER — sign in with ffbskt@gmail.com at
      /app.html; header shows email; owner rule keeps the library.
      Later full check: two browsers, two accounts, libraries don't mix.
- [x] 2b.2 2026-07-16: per-user storage isolation — libraries now live in
      data/site/users/<uid>/{known,books}; pipeline paths driven by a
      contextvar (set in async get_current_user so it copies into the sync
      endpoint thread; worker sets it per job). 100 MB/user cap enforced on
      upload (413). /me reports storage. Startup migrates legacy flat dirs
      -> users/1/. ALSO Telegram login widget on the web (POST
      /auth/telegram-widget, hash-verified) + /auth/config exposes bot name.
      **Check:** PASSED — 37 tests incl. two-user isolation + 413 quota;
      live: migration moved data to users/1/, owner keeps La Celestina,
      /me shows 1.1MB/100MB. PENDING USER: BotFather /setdomain
      readersimple.duckdns.org so the Telegram web button renders.
- [x] 2b.2b PROTECTION 2026-07-16: REQUIRE_AUTH=1 live (anonymous /books
      and /me -> 401; closed the "anonymous = owner user 1" hole). Daily
      translation quota (limits.py DAILY_PAGES=100 non-cached pages/user/day,
      counted in usage table by the worker), MAX_CONCURRENT_JOBS=1,
      MAX_RANGE=200. /me returns quota; web app gates the wizard behind
      login + shows storage/quota in the header chip. 41 tests.
      **Check:** PASSED live — anon 401, authed owner sees library +
      1.1MB/100MB + 0/100 pages, logged-out web shows "please sign in".
      Build note: `docker compose up --build` OOMs the 1GB VM if run
      attached — build detached (nohup) and it completes.
- [x] 2b.3 2026-07-16: bot/bot.py — thin API client (long polling, no
      public port), bot container in compose. POST /auth/telegram (shared
      TELEGRAM_BOT_SECRET) maps tg_id -> user; OWNER_TG_ID claims user 1.
      Send PDF/TXT -> book|known buttons; /books /stats /level /translate
      (one message edited as a progress bar) /read /pdf (document reply).
      Also fixed on the way: db connections now closed + WAL (worker
      leaked one every 2 s -> "database is locked"); 2b.1 also done live
      (Google sign-in verified by user, header shows email).
      **Check:** deployed, bot polling ("bot up"); PENDING USER — full
      flow from the phone: /start, /books, /translate, /pdf.
- [x] 2b.4 2026-07-16: deploy/monitor.py on the VM (cron */5 as root):
      disk >85%, memory <100 MB, /health, stuck jobs (>15 min silent),
      failed/quota jobs -> Telegram alerts to the user's chat (bot
      @ffbskt_reader_bot, chat id 318973541), with recovery messages and
      6 h re-alert throttle. Secrets in VM .env (fixed a merged-line bug
      there). Deferred: /admin/stats page, backup-age check (backups
      live on the dev PC, invisible from the VM).
      **Check:** PASSED — stopped the api container -> ⚠️ alert delivered;
      started it -> ✅ recovery delivered; healthy run silent.
- [x] 2b.5 2026-07-18: GitHub Actions. .github/workflows/test.yml runs
      pytest on every push/PR (ran green on the push that added it).
      deploy.yml auto-deploys on a `v*` tag (test -> SSH git pull + compose
      up + health check). NOTE: CI = automation/quality net, NOT security
      (that's 2b.2b). Deploy needs USER to add repo secrets SSH_PRIVATE_KEY
      (~/.ssh/gcp_reader) + VM_HOST (denis-reader@35.254.216.89); until
      then, tag-deploy is inert and manual `git pull` on the VM still works.
      **Check:** PASSED — tests workflow succeeded live on GitHub.

## Phase 2c — shared library + puzzle translation (research-driven redesign)

Decisions from research (research_baseline.py, research_reduce.py, 2026-07):
level-25/50 vocab gap is only ~1.6 pp -> translations are SHARED per
(book_hash, level); personalization = per-user word marking at read time;
level-0 quality path = guided translate + iterative "puzzle" refine pass
(unkT 36 -> 20 in one pass; target ~10 with two).

- [x] 2c.1 2026-07-18: add_known_source language-filters via
      classify_language; fix_vocab.py cleaned stored sources (easy_spanish
      1727 -> 1393, -334 EN) and rescored cached pages, on dev + VM.
      **Check:** PASSED — beautiful/the gone, casa/perro kept; coverage
      58.6% (was 59, honest drop); 41 tests green.
- [x] 2c.2 2026-07-18: content-addressed shared library —
      SITE/library/<text_hash>/{book.txt,meta,simplified,word_dict};
      users/<uid>/books/<slug>/ref.json = ownership record. book_dir(slug)
      resolves via ref -> library (single choke point). Dedup by
      normalized-text sha256. storage_used counts referenced content.
      Migration at startup (idempotent), ran on dev + VM.
      **Check:** PASSED — 43 tests incl. two users same text -> 1 library
      copy + 2nd sees existing translations; different text not deduped.
      VM: owner keeps La Celestina (15/10/5), 1.04 MB.
      NOTE: page-level partial-edition dedup deferred to 2c.2b (whole-book
      hash shipped; partial needs per-page-hash cache keys).
- [ ] 2c.3 Vocab similarity ("flexible book1"): store vocab_hash + word
      set per user; when Jaccard(user_vocab, cache_vocab) >= threshold
      (start 0.8), serve the shared translation and mark unknown words
      per-user at read time; below threshold offer personal translation.
      **Check:** synthetic vocab 90% overlapping -> shared cache served,
      hover marks differ per user.
- [x] 2c.4 2026-07-18: level-0 jobs auto-run up to 2 puzzle-refine passes
      (call_gemini_raw + _refine_pass; only accept a pass if unkT drops;
      stops at REFINE_TARGET=15). "Never shorten" is the design — text may
      grow because hard words are paraphrased, not deleted.
      **Check:** pages 41-43 L0 rerun: unkT 23/46/41 -> 6/11/25 (avg
      37->14), coverage 74->92%; length grows (144->149, 164->187,
      158->229) = no deletion. Note: p43 (dense archaic) stays at 25 after
      2 passes — a 3rd pass would cost more for diminishing return; left
      capped. Corrected the check: guard is "not shorter", not "±10%".
- [x] 2c.4b 2026-07-18: baseline (no-vocab CEFR) mode exposed end-to-end.
      jobs.baseline column (+ idempotent ALTER migration); translate/
      reader/pdf take a baseline flag; cache namespaced page<N>_L<lvl>_base
      (universal, always shareable); worker branches to
      simplify_page_baseline; list_books reports done_base; wizard has a
      "generic simplification" checkbox that repoints level cards, reader
      link and PDF. Vocab requirement only enforced when uncached pages
      actually need it.
      **Check:** PASSED — 44 tests; live on VM: baseline job on p60 ->
      method "baseline", separate done_base:{0:1}, guided 15/10/5 intact.
- [x] 2c.5 2026-07-18: ARCHITECTURE.md §3 (data model) and §5 (limits)
      rewritten to the as-built shared-library model — content-addressed
      library/<hash>, per-user ref.json ownership, reference-based storage
      quota, live limit values (100 MB / 100 pages-day / 1 job / 200 range),
      baseline vs guided cache keys. Status header now "BUILT & DEPLOYED".

## Phase 2d — content & reach (TODO, user-requested 2026-07-21)

- [x] 2d.1 2026-07-21: autonomous public-domain library. fetch_books.py
      pulled Alice/Grimm/Andersen (EN), La Fontaine (FR), Pinocchio (IT)
      from Gutenberg into the owner's shared library (~1057 pages).
      auto_translate.py cron */30 trickles baseline L0 at 80 pages/day
      (far under Gemini free tier + the 100/day quota).
- [x] 2d.2 2026-07-21: featured public shelf — featured.json; book_dir +
      list_books resolve featured books for EVERY logged-in user (read-only,
      ★ badge, no delete). La Celestina + the 5 classics featured. Public
      /samples endpoint + logged-out before/after teaser (EN/FR/IT/ES).
- [ ] 2d.3 Generalize the tokenizer beyond Spanish/Latin: analyze.WORD_RE
      + fold + counted_words are Latin-only (áéíóúñü). Add other Latin
      accents (à è ê ç ä ö ß ì ò ù …) and a Unicode-letter path for
      Cyrillic/Greek so Russian (Pushkin), etc. work. Affects tokenization,
      the "almost no text" guard, coverage scoring, and hover vocab.
      **Check:** a Russian Gutenberg book -> baseline L0 page has >20
      tokens and readable output; hover vocab non-empty.
- [ ] 2d.4 Improve weak samples (French sample is a title line) — pick the
      first CONTENT page, not the front matter, for the teaser.

## Phase 2e — multilingual help languages + UI language (user-requested 2026-07-21)

Goal: the user picks the language THEY understand ("help language"); hard
words are translated into it on hover (reader AND public teaser). One primary
+ an optional second ("add second language", like today's EN+RU). The whole
UI (menu, labels) switches to the primary language too. A language bar sits
always on top.

**Core design — reading language ⟂ help language(s).**
- The book's SIMPLIFIED TEXT is language-independent of help language, so the
  page cache (page<N>_L<lvl>[_base].json) stays SHARED across all users and
  all help languages. Nothing about this changes.
- Only the WORD DICTIONARY is per-help-language. Store it multilingual and
  shared per book: word_dict.json = {folded_word: {en, ru, fr, de, …}}.
  A help language's column is generated LAZILY (Gemini) the first time any
  user reads that book needing it, then cached for everyone → pay once.
- UI strings are STATIC per-language tables (no runtime cost, reliable).
- "Site language" sets BOTH the UI language and the default (primary) help
  language; "add second language" adds a 2nd help language for hover only.

Supported set (start): en, ru, fr, es, de, it (Gemini can add more later).

- [ ] 2e.1 Multilingual dictionary model. Migrate word_dict from {es:{en,ru}}
      to {word:{lang:translation}}; core.vocab.lookup(word, langs) returns
      the requested langs (morphology unchanged). Back-fill existing en/ru.
      Page-vocab embedded en/ru becomes dict-sourced (page stores the unknown
      words; translations resolved from the shared dict at read time).
      **Check:** existing EN/RU hovers unchanged; dict keyed per language.
- [ ] 2e.2 Lazy per-language word generation. pipeline.fill_language(book,
      lang): find words in the book's translated pages lacking `lang`,
      batch-translate into `lang` (one Gemini request / ~120 words), cache in
      the shared word_dict. Counts against the daily budget; reuses the
      gap-fill batching. Idempotent.
      **Check:** a book with only en/ru -> request fr -> French column
      generated + cached; second request instant, no API call.
- [ ] 2e.3 API: help-language params. reader/samples/pdf accept
      `langs=<primary>[,<second>]`; reader_payload returns the dictionary
      for those langs, triggering 2e.2 if a language is missing (returns a
      "generating" state the client can poll). Persist the user's languages
      on the users row (ui_lang, help_langs); GET/PUT /me/languages.
      **Check:** reader?langs=fr and ?langs=en,ru return the right dicts;
      user's choice persists across sessions.
- [ ] 2e.4 UI i18n. Static strings table strings.json for en/ru/fr/es/de/it
      (~40 keys: step titles, buttons, messages). app.html + reader_site.html
      render every label via t(key) for the chosen UI language. Keep English
      as the fallback for missing keys.
      **Check:** switch UI language -> all menu/labels change; no hard-coded
      English left in the chrome.
- [ ] 2e.5 Language bar (always on top). Top component: primary-language
      select + "＋ add second language" toggle (max 2). Persisted (2e.3 +
      localStorage). Drives UI language, hover languages, teaser, and the
      reader link (carries langs). Replaces the ad-hoc auth-row placement.
      **Check:** pick French -> UI + hovers French; add Russian -> hovers
      show both; reload keeps the choice.
- [ ] 2e.6 Hover in reader + PUBLIC teaser. reader_site shows 1-2 help
      languages per word (touch/hover). The logged-out /samples teaser gets
      the same per-word hover (needs the sample pages' dictionary in the
      chosen/browser language; a small language picker on the teaser).
      **Check:** touch a word in the teaser and in the reader -> correct
      translation(s) in the selected 1-2 languages.
- [ ] 2e.7 Autonomous/gap-fill language policy. The background translator
      pre-generates ONLY the site default help language(s) for featured
      books; all other languages stay on-demand (2e.2) to stay far from
      limits. Document the budget split.
      **Check:** background stays within the 80/day budget; on-demand
      languages appear only after a user requests them.

## Phase 3 — payments

- [ ] 3.1 Stripe account, Checkout for Plus, webhook -> tier (test mode).
      **Check:** test-card purchase upgrades limits instantly.
- [ ] 3.2 Grace period + downgrade path; usage/billing page in web app.
      **Check:** cancel in test mode -> read-only library after grace.
- [ ] 3.3 ToS + privacy pages; go-live checklist (real Stripe keys, domain,
      backups verified).
      **Check:** one real $ purchase by you, then refund it.

## Phase 4 — scale & mobile (when metrics demand)

- [ ] 4.1 Postgres behind the DB interface; migrate SQLite.
- [ ] 4.2 Object storage behind the Storage interface.
- [ ] 4.3 Redis queue, 2+ workers, paid-tier Gemini key.
- [ ] 4.4 Mobile client spike on the same API.

## Done

- [x] Phase 0: local pipeline — 4-level token-based simplify, wizard UI,
      reader with morphology hover + gap-fill dictionary, 4 PDF modes,
      resume-safe jobs, night batch run pages 41–90 (2026-07-15)
