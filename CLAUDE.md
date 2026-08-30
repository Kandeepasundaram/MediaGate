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
whether to use a separate incoming folder per type are set from the
dashboard's Settings tab after first boot (see the "Settings API" section
below). This was a deliberate pivot mid-build once the deploy target became
"generic container configured post-install," away from the original plan's
per-host `config.yaml` editing.

Movies and TV always have their **own** incoming and archive path — there is
no shared/generic incoming bucket for both types (an earlier version of this
app had one `active_dir` field, which was wrong: it implied movies and TV
could share a staging folder, which doesn't match how a real library is
organized). `config.docker.yaml` defaults `incoming_movies == archive_movies`
and `incoming_tv == archive_tv`, i.e. no separate staging step, since that's
the common case for a library an *arr stack already organizes in place.

**Request flow for the core feature (archive a file)**: `GET /api/scan`
calls `scanner.scan_targets()` over the union of `paths.incoming_movies`,
`paths.incoming_tv`, `paths.archive_movies`, and `paths.archive_tv` — this
supports both a separate staging folder per type *and* a library organized
in-place (incoming == archive destination per type, common on a
single-drive homelab setup), deduping overlapping roots and excluding
anything already recorded in `media_items` (`Database.list_known_paths()`,
both `original_path` and `final_path`) so a rescan doesn't re-surface a file
already archived or an already-organized copy sitting inside an archive
root. Each result is a `ScannedFile` with a
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
  season/episode, `watched`, `metadata` (JSON — holds `poster_path`,
  `overview`, `episode_title` set by `archiver.archive_file()` from the
  `RenamePlan` at archive time; this is what backs the Movies/TV gallery
  tabs, not a general-purpose bag for anything else).
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
`paths.incoming_movies/incoming_tv/archive_movies/archive_tv`,
`tmdb.api_key`, `server.cors_origins` (the allowlist lives in `_EDITABLE_KEYS` in
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

**Library API / gallery tabs** (`app/api/routes/library.py`): this is Media
Manager's actual reason to exist alongside Radarr/Sonarr, which already
handle automated import. `GET /api/library/movies`/`tv` first call
`library_adopt.adopt_new_files()` (scans `archive_movies`/`archive_tv`,
registers any file not yet in `media_items` — **no copy/move, no network
call**, `final_path` is just set to wherever the file already is) and only
then return everything tracked — so a library Radarr/Sonarr already
organized shows up without ever being run through the archive/preview flow.
Newly-adopted rows start with `tmdb_id NULL` and empty `metadata`; a
background task (`app/core/metadata_backfill.py`, started in `main.py`'s
`lifespan` alongside the tracker scheduler) works through them one at a
time via `Database.list_unmatched_media_items()` — TMDB lookups only, still
no file operations — filling in `tmdb_id`/canonical title/poster/overview.
A failed lookup still sets `match_attempted_at` (not just a successful one)
so a title with no real TMDB match gets retried on a 6-hour cooldown instead
of every backfill cycle. The dashboard polls `GET /api/library/metadata-status`
after each gallery load and shows a "fetching metadata for N more..." hint,
re-loading the gallery every 8s while `pending > 0` so posters fill in
without a manual refresh.

`POST /api/library/{id}/watched` is manual watch-state tracking, independent
of the `watchstate` container this homelab also runs. TV episodes come back
as a flat list (one row per episode) — grouping into one card per show
happens client-side in `app.js::groupEpisodesByShow()`, keyed by `title`
(not `tmdb_id`, since that's simpler and title collisions are rare in a
personal library). Posters render straight from the TMDB CDN
(`https://image.tmdb.org/t/p/w342{poster_path}`) client-side — the backend
never proxies or downloads images (the written-but-unwired
`renamer.download_artwork()`/`write_nfo()` from the original plan still
aren't called anywhere; the gallery made them unnecessary for the poster use
case, though NFO writing could still be worth wiring up separately later for
Plex/Jellyfin metadata, unrelated to this app's own UI).

**Browse & Clean Up tab** (`GET /api/library/browse`, `POST
/api/library/delete-file`): a third, distinct view of the library, added for
manual cleanup of files that predate this app or were imported by
Radarr/Sonarr directly. Unlike `/api/scan` (excludes anything already
known) and `/api/library/movies`/`tv` (DB-only), `browse` runs
`scanner.scan_directory()` straight against `archive_movies`/`archive_tv`
with **no exclusion filter** — every video file shows up, tracked or not,
cross-referenced against `media_items` by `final_path`
(`Database.get_media_item_by_final_path()`) to set `tracked`/`media_id`.
Selecting rows and clicking "Re-run Archive Match" just feeds those paths
into the existing `/api/archive/preview` → `/api/archive/confirm` flow
(`app.js::previewPaths()`, factored out of `scanAndPreview()` for this
reuse) — there's no special "move" logic for already-organized files, so a
re-match produces a second, corrected copy and leaves the original in place
for you to clean up with the Delete button, consistent with the rest of the
app's "copy, don't move" archiving.

`delete-file` is the one genuinely destructive endpoint in the app: it
`unlink()`s a file and, if it was tracked, removes its `media_items` row —
guarded by requiring the resolved path to fall inside one of the four
configured incoming/archive directories (`400` otherwise), since this is a
LAN dashboard with no auth and a bad request or client bug shouldn't be able
to touch anything outside the library. Deleting a tracked item logs the
`operation_type='delete'` entry to `operation_log` **before** removing the
`media_items` row, not after — `operation_log.media_id` is a foreign key,
and logging first while the row still exists avoids a constraint violation
either direction. This required a real migration: `operation_log`'s
`operation_type` CHECK didn't originally allow `'delete'`, and its `media_id`
FK didn't have `ON DELETE SET NULL`, so deleting a media item that already
had history entries would violate the constraint on the delete itself. Both
are fixed together in `_migration_v2` in `app/database.py` via SQLite's
table-rebuild pattern (no in-place `ALTER` for CHECK/FK constraints) — the
first schema migration that's actually had to run against a real (not
fresh) database, since it landed after the first live deploy.
`_migration_v3` (adds `media_items.match_attempted_at` for the metadata
backfill above) is the simpler case: a plain nullable column add, which
SQLite *can* do with an in-place `ALTER TABLE ... ADD COLUMN`, no rebuild
needed. `SCHEMA_VERSION` is now `3`.

**Frontend** (`app/static/`): single `index.html` with seven tabs (Movies,
TV, Browse & Clean Up, Archive, Notifications, History, Settings) driven by
plain `fetch()` calls in `app.js` — no build step, no framework. `Ctrl+S`
triggers Approve & Archive. The Settings tab's form talks to
`/api/settings` and its "Test Permissions" button to
`/api/settings/permissions-check`. Movies/TV tabs are the two gallery views
described above; Browse & Clean Up is the raw-filesystem view.
