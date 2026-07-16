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
- [ ] 1.3 FastAPI skeleton: /health, /me (stub JWT, single local user),
      OpenAPI docs page.
      **Check:** `curl /health` = ok; /docs renders.
- [ ] 1.4 Port endpoints: known sources, books, stats.
      **Check:** upload both test books via curl; stats JSON = Phase 0 values.
- [ ] 1.5 Jobs in SQLite (`jobs`, `job_events` tables) + worker thread;
      port translate + gap-fill into it.
      **Check:** 1-page job on cached page runs free; events appear;
      progress endpoint counts to 100.
- [ ] 1.6 Port reader + PDF endpoints; app.html + reader_site.html call the
      new API (static SPA).
      **Check:** full wizard in browser: stats -> level -> job -> reader
      hover -> all 4 PDFs. Same results as Phase 0.

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
- [ ] 2a.4 Dockerfile + docker-compose.yml (api, worker, caddy) run LOCALLY.
      **Check:** the Phase 1 checks all pass against `localhost` compose.
- [ ] 2a.5 First deploy: clone repo on VM, `.env` (chmod 600) with PROD
      Gemini key, `docker compose up -d`. Domain optional; start with IP.
      **Check:** wizard works from your phone's browser on the VM address.
- [ ] 2a.6 Backup: nightly cron — tar data/ + SQLite -> Oracle Object
      Storage (free 20 GB) or rsync home; keep 7 days.
      **Check:** delete a test book, restore from yesterday's backup.

## Phase 2b — users + quotas + Telegram

- [ ] 2b.1 Google OAuth + Telegram Login -> users table; JWT sessions;
      per-user data dirs; migrate existing library to your user.
      **Check:** two browsers, two accounts, libraries don't mix.
- [ ] 2b.2 quota.py gate (Free limits from ARCHITECTURE §5) + usage
      counters + `quota` job status handling in the UI.
      **Check:** set limit=2 pages in test config, run 3-page job -> pauses
      politely, resumes after reset.
- [ ] 2b.3 Telegram bot: send PDF = upload book, /stats, /level, /translate
      (progress by editing one message), /pdf mode buttons.
      **Check:** full flow from the phone, no browser involved.
- [ ] 2b.4 monitor.py + cron: disk/RAM/health/queue/429/backup-age checks ->
      Telegram alerts to your chat; /admin/stats page.
      **Check:** fill disk with a dummy file -> alert arrives in <5 min;
      kill api container -> alert.
- [ ] 2b.5 CI: GitHub Actions — pytest on push; on tag: build image, SSH
      deploy to VM.
      **Check:** change a UI string, `git push`, tag -> live on VM with no
      manual SSH.

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
