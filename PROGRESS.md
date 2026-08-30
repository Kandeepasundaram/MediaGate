# Implementation Progress Notes

Built autonomously end-to-end from `phases plan.txt`, following the plan's own
"Recommended Starting Point for Agents" order (DB → TMDB → file processing →
API → dashboard → tracker/automation → tests → deploy docs) since this was
done by a single agent rather than the plan's assumed 4-agent team split.
Environment: Windows dev machine, Python 3.13, git repo initialized fresh
(no prior history).

## Status by phase

- **Phase 0 (Environment)**: Done in spirit, not literally — this is a Windows
  dev box, not the target Ubuntu server, so hardware/OS package steps (apt
  installs, static IP, HDD mount) weren't executable here. `config.yaml`,
  `.env.example`, `requirements.txt`, and the folder structure are in place.
- **Phase 1 (Database & config)**: Done. `app/database.py`, `app/config_loader.py`.
  Added a real migration mechanism (`_MIGRATIONS` dict keyed by schema version)
  even though only v1 exists yet.
- **Phase 2 (TMDB)**: Done. `app/core/tmdb_client.py` + `tmdb_scraper.py`.
  **Not tested against the live themoviedb.org site or a real API key** — no
  TMDB_API_KEY was available in this environment. All tests mock the scraper
  and the `tmdbv3api` classes. If the site's HTML structure has drifted from
  what `tmdb_scraper.py`'s CSS selectors assume, scraper mode will return
  empty results until the selectors are updated.
- **Phase 3 (File processing)**: Done. `scanner.py`, `renamer.py`,
  `subtitle_purger.py`, `archiver.py`. Episode-title lookup
  (`renamer.fetch_episode_title`) only works in API mode (`tmdbv3api.Episode`)
  — scraper mode has no per-episode detail method, so TV files archive with
  just the `SxxEyy` code and no episode title unless a key is configured.
- **Phase 4 (FastAPI backend)**: Done. Verified by booting `uvicorn` locally
  and curling `/api/status`, `/`, `/api/stats` — all returned correctly.
  Switched `@app.on_event("startup")` to the modern `lifespan` context
  manager during the build (the old API is deprecated in the installed
  FastAPI version and emitted warnings).
- **Phase 5 (Dashboard)**: Done — vanilla HTML/CSS/JS, dark theme, 4 tabs,
  Ctrl+S shortcut, 30s notification polling. **Not exercised in an actual
  browser** in this session (no UI automation was run); verified only via the
  API integration tests and a manual `curl` of `/`. Worth a real browser pass
  before relying on it.
- **Phase 6 (Tracker & automation)**: Core logic (`tracker.py`) is
  unit-tested with mocked TMDB responses. `scripts/cron_job.py`,
  `scripts/install_service.sh` are written but **not executed** —
  systemd/cron require the real Ubuntu host. The original plan's Windows
  `winrt` toast agent (`scripts/windows_toast.py`, `notification_agent.py`,
  `setup_windows.bat`) was built, then **deleted** in favor of the browser
  `Notification` API — see "Key deviations" below. Deployment target is now
  Docker/Arcane on the homelab, not a bare Ubuntu systemd install, so
  `install_service.sh`/`deploy.sh`/`backup.sh` will likely be superseded by a
  `Dockerfile` + `docker-compose.yml` (not yet written — pending homelab
  specifics: media mount paths, reverse proxy, TMDB key).
- **Phase 7 (Testing)**: 35 tests, all passing
  (`.venv/Scripts/python -m pytest -q`). Covers DB CRUD, filename parsing,
  TMDB client fallback/caching, renamer path-building + collision handling,
  subtitle purge rules, tracker update detection, and a full
  scan → preview → confirm → history/stats integration flow via
  `TestClient`. No performance testing (Phase 7.4 in the plan) was done —
  no real media library exists to test against yet.
- **Phase 8 (Deployment & docs)**: `README.md`, `INSTALL.md`, `CONFIG.md`,
  `TROUBLESHOOTING.md` written. `CONTRIBUTING.md` was skipped — the plan
  listed it as a filename only, with no actual content requirements, and
  generic contribution-guideline boilerplate wasn't worth fabricating. API
  docs are the FastAPI auto-generated `/docs` (no separate `API.md` needed).
  Deploy/backup scripts are written but unexecuted (same reasoning as Phase 6
  — they assume the Ubuntu host and systemd).

## Key deviations from the plan worth knowing about

- **Notifications are browser-based, not a native Windows agent.** The plan
  called for a `winrt`-based Windows toast agent (Task Scheduler service,
  separate HTTP port, Windows Firewall rule). Built it, then replaced it:
  the dashboard now polls `/api/tracker/notifications` and fires a browser
  `Notification` for anything not already seen (tracked in `localStorage`).
  Rationale: the deploy target is a homelab Docker host (Arcane), and the
  Windows-only agent was the one piece of the whole system that wasn't OS
  agnostic — removing it means the backend has zero OS-specific code and
  "notifications" now just means "keep a dashboard tab open in any browser,
  on any device." Trade-off: no notification once every browser tab is
  closed (vs. a true always-on OS toast); acceptable for a LAN dashboard
  that's normally left open. `scripts/windows_toast.py`,
  `notification_agent.py`, `setup_windows.bat`, and the
  `tracker.windows_agent_url` config key are all removed. DB methods
  `mark_notification_sent`/`list_unsent_notifications` (added to let the old
  agent avoid re-notifying) were removed too — the browser's `localStorage`
  set now does that job client-side.
- Archive confirm also copies sibling subtitle files (matching video stem) to
  the destination folder — the plan didn't say this explicitly but archiving a
  video with no subtitles seemed like an obvious gap.

## Recommended next steps (not done here)

1. Get a real TMDB API key into `.env` and re-verify search/detail calls end to end.
2. Run this on an actual Ubuntu box against a real sample media folder to validate scanner/renamer against real-world messy filenames.
3. Manually click through the dashboard in a browser, including granting the notification permission prompt.
4. Write `Dockerfile` + `docker-compose.yml` and deploy via Arcane — blocked on: media mount paths on the homelab host, whether a reverse proxy (Traefik/Caddy) is in front of Arcane-managed containers, and whether a TMDB key will be supplied.
5. Once containerized, `scripts/install_service.sh`/`deploy.sh` (systemd-based) are probably dead weight — decide whether to keep them for a non-Docker install path or delete them.
