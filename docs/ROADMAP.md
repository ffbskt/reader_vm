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
- [ ] 2b.5 CI: GitHub Actions — pytest on push; on tag: build image, SSH
      deploy to VM.
      **Check:** change a UI string, `git push`, tag -> live on VM with no
      manual SSH.

## Phase 2c — shared library + puzzle translation (research-driven redesign)

Decisions from research (research_baseline.py, research_reduce.py, 2026-07):
level-25/50 vocab gap is only ~1.6 pp -> translations are SHARED per
(book_hash, level); personalization = per-user word marking at read time;
level-0 quality path = guided translate + iterative "puzzle" refine pass
(unkT 36 -> 20 in one pass; target ~10 with two).

- [ ] 2c.1 Fix known-vocab English pollution: site add_known_source must
      language-filter like analyze.classify_language ("beautiful", "am"
      leaked in). Rebuild easy_spanish source; rescore cached pages.
      **Check:** no EN words in known list; coverage numbers shift honestly.
- [ ] 2c.2 Content-addressed shared library: data/library/<text_hash>/
      {book.txt, page<N>_L<lvl>.json, word_dict.json}; page-level hashes
      for partial dedup across editions. DB: documents(hash,...),
      user_documents(user_id, doc_hash, name, added_at, pages_read).
      Migrate users/<uid>/books/. User quota = their references.
      **Check:** two accounts upload the same TXT -> one stored copy,
      second user instantly sees existing translations; different
      edition with matching pages reuses those pages' translations.
- [ ] 2c.3 Vocab similarity ("flexible book1"): store vocab_hash + word
      set per user; when Jaccard(user_vocab, cache_vocab) >= threshold
      (start 0.8), serve the shared translation and mark unknown words
      per-user at read time; below threshold offer personal translation.
      **Check:** synthetic vocab 90% overlapping -> shared cache served,
      hover marks differ per user.
- [ ] 2c.4 Puzzle refine in the pipeline: level-0 jobs run translate +
      refine pass automatically (2 calls/page, "do not shorten" guard);
      second refine only if unkT still > ~15. Baseline (no-vocab) mode
      exposed in UI as "skip step 1" with warning.
      **Check:** pages 41-43 L0 rerun -> unkT <= ~12, length within 10%.
- [ ] 2c.5 Update ARCHITECTURE.md sections 3/5 to the shared-library
      model (documents/user_documents, reference-based quota).

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
