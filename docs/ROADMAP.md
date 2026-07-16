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
