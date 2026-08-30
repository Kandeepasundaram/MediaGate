# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

```bash
# Setup (Windows shown; use .venv/bin/ instead of .venv/Scripts/ on Linux/macOS)
python -m venv .venv
.venv/Scripts/pip install -r requirements.txt

# Run the dev server (dashboard at /, API at /api/*, docs at /docs)
.venv/Scripts/python -m uvicorn app.main:app --reload

# Run the full test suite
.venv/Scripts/python -m pytest -q

# Run a single test file / test
.venv/Scripts/python -m pytest tests/test_tracker.py
.venv/Scripts/python -m pytest tests/test_tmdb_client.py::test_parse_filename_tv_show

# Run the tracker check manually (what the daily cron job does)
.venv/Scripts/python scripts/cron_job.py
```

There is no separate lint/format tooling configured yet — none was requested.

## Architecture

FastAPI backend (`app/`) + a vanilla JS dashboard (`app/static/`) served from the
same process, designed to run on an Ubuntu box with an external HDD mounted,
reachable from Windows machines on the LAN. See `phases plan.txt` for the
original phase-by-phase plan and `PROGRESS.md` for what has actually been
built against it, including deviations and things that are written but not
live-tested (systemd, cron, real Ubuntu deploy, Windows toast popups, real
TMDB scraping against themoviedb.org).

**Request flow for the core feature (archive a file)**:
`scanner.scan_directory()` walks `paths.active_dir` → returns `ScannedFile`
objects with a `parsed` (`tmdb_client.parse_filename()`) guess at
title/year/season/episode → `POST /api/archive/preview` resolves each parsed
title against `TMDBClient` and builds a `RenamePlan` via `renamer.py` (movie:
`Name (Year)/Name (Year).ext`; TV: `Show/Season NN/Show - SNNENN - Title.ext`)
→ user approves in the dashboard → `POST /api/archive/confirm` runs
`subtitle_purger` on the source folder, then `archiver.archive_file()` copies
(`shutil.copy2`, not move — checksum verification is intentionally deferred)
the file to the archive tree and records it in `media_items` +
`operation_log`.

**TMDB access is hybrid** (`app/core/tmdb_client.py` + `tmdb_scraper.py`):
`TMDBClient` uses the official API (`tmdbv3api`) when `tmdb.api_key` /
`TMDB_API_KEY` is set, and transparently falls back to scraping
themoviedb.org (`TMDBScraper`, rate-limited, rotating user agents) on missing
key or any API error. Both paths return the same `MediaResult` shape, and
results are memoized per-client-instance in `TMDBClient._cache`.

**Dependency wiring**: `app/dependencies.py` exposes `get_config()`,
`get_database()`, `get_tmdb_client()` as `functools.lru_cache`'d singletons,
injected into routes via FastAPI `Depends`. Tests override them per-request
with `app.dependency_overrides[...]` (see `tests/test_api_integration.py`)
rather than touching the real cache — the actual singletons persist for the
life of the Python process.

**Data model** (`app/database.py`, SQLite, WAL mode, no connection pool — a
short-lived connection is opened per call via `Database.connect()`):
- `media_items` — one row per archived file: paths, tmdb_id, media_type,
  season/episode, `watched`, `metadata` (JSON).
- `archive_tracker` — one row per `(tmdb_id, media_type)` being watched for
  new seasons/sequels; `pending_notification` drives the dashboard's
  Notifications tab and browser notifications, cleared by
  `acknowledge_notification()` when the user clicks "Mark Downloaded".
- `operation_log` — audit trail for `archive`/`rename`/`purge`/`tracker_check`
  operations, surfaced via `/api/archive/history` and `/api/logs`.

**Tracker → notification path**: `app/core/tracker.py::check_for_updates()`
(called by `scripts/cron_job.py`, meant to run daily via cron/systemd-timer)
queries TMDB for each tracked title and flags `pending_notification` on
change — that's it server-side. There is no OS-specific notification agent:
the dashboard (`app/static/app.js`) polls `/api/tracker/notifications` every
30s regardless of which tab is active, and fires a browser `Notification` for
any id it hasn't shown before (tracked in `localStorage`), which works from
any OS as long as a dashboard tab is open somewhere.

**Frontend** (`app/static/`): single `index.html` with four tabs (Archive,
Notifications, History, Settings) driven by plain `fetch()` calls in
`app.js` — no build step, no framework. `Ctrl+S` triggers Approve & Archive.
