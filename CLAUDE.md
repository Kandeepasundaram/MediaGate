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

# Run the tracker check manually (out-of-band; a non-Docker install with
# the server itself not running would otherwise rely on cron for this)
.venv/Scripts/python scripts/cron_job.py

# Docker build + run
docker compose up -d --build
```

There is no separate lint/format tooling configured yet — none was requested.

## Architecture

FastAPI backend (`app/`) + a vanilla JS dashboard (`app/static/`) served from
the same process. Deploy target is a homelab Docker host managed via Arcane
(see `Dockerfile`, `docker-compose.yml`, `config.docker.yaml`,
`docker-entrypoint.sh`); a bare-metal Ubuntu/systemd install is documented as
a fallback in `INSTALL.md` but isn't the primary path. See `phases plan.txt`
for the original phase-by-phase plan and `PROGRESS.md` for what has actually
been built against it, including deviations (the plan assumed Ubuntu+systemd
+cron+a Windows winrt agent; the actual build is a single OS-agnostic
container with an in-process scheduler and browser notifications) and things
written but not live-tested (real Ubuntu systemd install, real TMDB
scraping against themoviedb.org).

The container is generic on purpose: nothing homelab-specific is baked in at
build time. `docker-compose.yml` needs two bind mounts (`/media/movies`,
`/media/tv` — real host paths go in `.env` via `${MOVIES_HOST_PATH}`/
`${TV_HOST_PATH}` substitution, not the compose file itself, since Arcane
treats a git-synced compose file as read-only) and one named volume
(`/config`, for `config.yaml` + the SQLite DB + logs). The TMDB key and
whether to use a separate incoming folder are set from the dashboard's
Settings tab after first boot (see the "Settings API" section below). This
was a deliberate pivot mid-build once the deploy target became
"generic container configured post-install," away from the original plan's
per-host `config.yaml` editing.

**Request flow for the core feature (archive a file)**: `GET /api/scan`
calls `scanner.scan_targets()` over the union of `paths.active_dir`,
`paths.archive_movies`, and `paths.archive_tv` — this supports both a
separate staging folder *and* a library organized in-place (incoming ==
archive destination, common on a single-drive homelab setup), deduping
overlapping roots and excluding anything already recorded in `media_items`
(`Database.list_known_paths()`, both `original_path` and `final_path`) so a
rescan doesn't re-surface a file already archived or an already-organized
copy sitting inside an archive root. Each result is a `ScannedFile` with a
`parsed` (`tmdb_client.parse_filename()`) guess at
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
queries TMDB for each tracked title and flags `pending_notification` on
change — that's it server-side. It's invoked by `app/core/scheduler.py`'s
`run_daily_tracker_check()`, a background `asyncio` task started in
`main.py`'s `lifespan` that sleeps until `tracker.cron_time` (re-reading
config/DB/TMDB-client singletons fresh on every wake, so a Settings-tab
change takes effect on the next scheduled run without a restart) — no host
cron needed. `scripts/cron_job.py` does the same `check_for_updates()` call
for anyone who'd rather drive it from external cron instead. There is no
OS-specific notification agent: the dashboard (`app/static/app.js`) polls
`/api/tracker/notifications` every 30s regardless of which tab is active,
and fires a browser `Notification` for any id it hasn't shown before
(tracked in `localStorage`), which works from any OS as long as a dashboard
tab is open somewhere.

**Settings API** (`app/api/routes/settings.py`): `GET`/`POST /api/settings`
expose a deliberately small editable subset of `AppConfig` —
`paths.active_dir/archive_movies/archive_tv`, `tmdb.api_key`,
`server.cors_origins` (the allowlist lives in `_EDITABLE_KEYS` in
`config_loader.py`). A POST calls `config_loader.update_settings()`, which
merges only those keys into `config.yaml` on disk (`create_dirs=False` —
saving a path does **not** auto-create the directory, unlike the very first
app boot, so a typo doesn't silently mkdir somewhere wrong) and then
`app.dependencies.reset_singletons()` clears the `lru_cache`s so the next
`Depends(get_config)`/etc. call anywhere picks up the change immediately.
`GET /api/settings/permissions-check` write-probes each configured media
path and reports the container's effective uid/gid — read-only diagnostics
only; there's intentionally no "fix permissions" action, since that would
need root and arbitrary-path `chown` triggered by a web request, which is a
compose/host-level decision (`user:` + matching ownership), not an in-app one.
If `TMDB_API_KEY` is set as an env var it always wins over the stored value
and the Settings UI reports the field as locked.

**Frontend** (`app/static/`): single `index.html` with four tabs (Archive,
Notifications, History, Settings) driven by plain `fetch()` calls in
`app.js` — no build step, no framework. `Ctrl+S` triggers Approve & Archive.
The Settings tab's form talks to `/api/settings` and its "Test Permissions"
button to `/api/settings/permissions-check`.
