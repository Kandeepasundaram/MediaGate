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
  `scripts/windows_toast.py` (+ `notification_agent.py` alias),
  `scripts/install_service.sh`, `scripts/setup_windows.bat` are written but
  **not executed** — systemd/cron require the real Ubuntu host, and the
  Windows toast agent was deliberately not run because triggering a real
  desktop toast notification felt like an unnecessary side effect to cause
  unprompted; it depends on the optional `winrt-Windows.UI.Notifications` /
  `winrt-Windows.Data.Xml.Dom` packages which are not in `requirements.txt`
  (Linux-side `requirements.txt` can't include Windows-only packages) — install
  them separately on the Windows agent host per `INSTALL.md`.
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

- `archive_tracker.notification_sent_at` is set by a new
  `mark_notification_sent()` DB method, separate from `acknowledge_notification()`.
  The plan's schema implied one flag pair might do both jobs, but that would
  make the cron job either spam a toast every run or silently stop tracking
  once the user dismissed it in the dashboard — split them so "toast sent" and
  "user acknowledged" are independent.
- `config.yaml` gained a `tracker.windows_agent_url` key not in the original
  schema sketch, needed so the Ubuntu cron job knows where to POST.
- Archive confirm also copies sibling subtitle files (matching video stem) to
  the destination folder — the plan didn't say this explicitly but archiving a
  video with no subtitles seemed like an obvious gap.

## Recommended next steps (not done here)

1. Get a real TMDB API key into `.env` and re-verify search/detail calls end to end.
2. Run this on an actual Ubuntu box against a real sample media folder to validate scanner/renamer against real-world messy filenames.
3. Manually click through the dashboard in a browser.
4. Install the `winrt` packages on a real Windows machine and confirm a toast actually appears.
5. Run `scripts/install_service.sh` and `scripts/backup.sh` on Ubuntu for real.
