# Implementation Progress Notes

Built autonomously end-to-end from `phases plan.txt`, following the plan's own
"Recommended Starting Point for Agents" order (DB → TMDB → file processing →
API → dashboard → tracker/automation → tests → deploy docs) since this was
done by a single agent rather than the plan's assumed 4-agent team split.
Environment: Windows dev machine, Python 3.13, git repo initialized fresh
(no prior history).

## Status by phase

- **Phase 0 (Environment)**: Done in spirit, not literally — built on a
  Windows dev box, deployed to a homelab Docker host (Arcane), neither of
  which is the plan's assumed bare Ubuntu server, so the plan's apt-install/
  static-IP/HDD-mount steps don't apply as written. `config.yaml`,
  `.env.example`, `requirements.txt`, and the folder structure are in place;
  Docker Desktop was available locally, so the actual Docker path (Phase 8)
  got real build/run verification instead of staying purely theoretical.
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
  unit-tested with mocked TMDB responses. The original plan's Windows
  `winrt` toast agent (`scripts/windows_toast.py`, `notification_agent.py`,
  `setup_windows.bat`) was built, then **deleted** in favor of the browser
  `Notification` API — see "Key deviations" below. The daily check itself
  no longer depends on host cron either: `app/core/scheduler.py` runs it
  in-process via a background `asyncio` task started in `main.py`'s
  `lifespan`, verified by building the real Docker image and confirming
  `/api/status` etc. respond after container start (see Phase 8).
  `scripts/cron_job.py`/`install_service.sh`/`deploy.sh`/`backup.sh`
  (systemd-based) are kept only as an alternative for a non-Docker install;
  unexecuted, since Docker/Arcane is now the primary target.
- **Phase 7 (Testing)**: 50 tests, all passing
  (`.venv/Scripts/python -m pytest -q`). Covers DB CRUD, filename parsing,
  TMDB client fallback/caching, renamer path-building + collision handling,
  subtitle purge rules, tracker update detection, config load/save
  round-tripping, the scheduler's time math, and full integration flows
  (scan → preview → confirm → history/stats; settings GET/POST/permissions)
  via `TestClient`. No performance testing (Phase 7.4 in the plan) was done —
  no real media library exists to test against yet.
- **Phase 8 (Deployment & docs)**: `README.md`, `INSTALL.md`, `CONFIG.md`,
  `TROUBLESHOOTING.md` written. `CONTRIBUTING.md` was skipped — the plan
  listed it as a filename only, with no actual content requirements, and
  generic contribution-guideline boilerplate wasn't worth fabricating. API
  docs are the FastAPI auto-generated `/docs` (no separate `API.md` needed).
  **`Dockerfile` + `docker-compose.yml` written and actually built/run** —
  Docker Desktop was available on the dev machine, so this got real
  verification rather than just being written: `docker build`, then a full
  container run with named volumes, hitting `/api/status`,
  `/api/settings` (GET + POST), `/api/settings/permissions-check`, and
  confirming a setting saved via the API survived a `docker restart`
  (proving `/config` volume persistence actually works, not just that the
  compose file looks right). One real snag hit and worth knowing about: on
  this dev machine's network, `pip install` inside the build fails
  certificate verification against pypi.org — confirmed via a plain
  vanilla `python:3.12-slim` container hitting the same error, so it's this
  network's TLS interception (proxy/AV), not a `Dockerfile` defect. Did
  **not** work around it by disabling cert verification in the shipped
  `Dockerfile` (would be a real security regression); validated the rest of
  the build/run pipeline with a throwaway local-only `--trusted-host` build
  that was deleted immediately after, never committed. The systemd-based
  `install_service.sh`/`deploy.sh`/`backup.sh` remain written but
  unexecuted (Ubuntu-only, no longer the primary deploy path).

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
- **Deploy target moved from "per-host `config.yaml` you hand-edit" to "one
  generic container, configure everything from the web UI after boot."**
  Added a Settings API (`GET`/`POST /api/settings`,
  `GET /api/settings/permissions-check`) so media paths and the TMDB key are
  editable at runtime without a rebuild/restart — see the "Settings API"
  section in `CLAUDE.md`. Explicitly did **not** add a way to fix filesystem
  permissions from the web UI (only diagnose them) — mutating host directory
  ownership from an unauthenticated LAN request is a real privilege/security
  question, not just a convenience feature, and that decision belongs at the
  compose/host level (`user:` + matching ownership), not baked into the app.
  No reverse-proxy-specific config was added either — the app doesn't
  hardcode ports/URLs, so a proxy can be added later with zero app changes,
  which resolved that requirement without building anything for it.

## Recommended next steps (not done here)

1. Get a real TMDB API key and re-verify search/detail calls end to end (can now be set via the Settings tab, no `.env` needed).
2. Run this on the actual Arcane host against a real sample media folder to validate scanner/renamer against real-world messy filenames, and confirm the `/media` bind mount's permissions work with whatever user Arcane runs the container as.
3. Manually click through the dashboard in a browser, including granting the notification permission prompt and using the new Settings tab (paths form, TMDB key field, Test Permissions button) — only exercised via `curl`/`TestClient` and a real container so far, not a real browser.
4. Decide whether to keep `scripts/install_service.sh`/`deploy.sh`/`backup.sh` (systemd-based, Ubuntu-only) around for a non-Docker install path or delete them now that Docker/Arcane is the primary target.
5. Consider publishing a prebuilt image (GHCR) so Arcane can pull instead of building from source each deploy.
