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
- **Phase 7 (Testing)**: 56 tests, all passing
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

## Live deployment to the real homelab (2026-08-30)

Deployed for real to the user's Arcane instance (`192.168.29.187:3552`),
not just tested locally. Notes worth keeping:

- Repo visibility: turned out to already be public (checked via `gh api
  repos/.../MediaGate --jq '.private, .visibility'` — the earlier
  `--private` flag at repo creation apparently didn't take, or something
  changed it after; not investigated further since public was the agreed
  outcome anyway). Arcane's git sync auth type was set to "None".
- Arcane's git-based Project deploy needs "Sync Files" enabled (not just the
  compose file) — `build: .` requires the Dockerfile and app source to
  actually be present next to the synced compose file, which the default
  (compose-file-only) sync doesn't provide.
- Hit a real `git push` hang from Windows Git Credential Manager needing
  interactive re-auth mid-session (invisible popup, no error until
  `GIT_TERMINAL_PROMPT=0` surfaced it). Fixed with `gh auth setup-git` to
  make git use the already-authenticated `gh` CLI instead of GCM. Caused a
  real problem: a commit (port 26431 change) had been made but never
  pushed, so Arcane's first sync silently deployed the stale version
  (port 8000) — caught by checking the synced commit hash against `git log`,
  not by anything failing loudly.
- **The pypi.org SSL/TLS interception hit repeatedly on the dev machine
  during local testing did NOT occur on the actual Arcane host** — the real
  `docker compose`-driven build completed cleanly end to end, confirming
  that issue was specific to the dev machine's network as suspected, not
  the `Dockerfile`.
- Deployed successfully: build succeeded, container reached `Running`,
  dashboard loaded live at `http://192.168.29.187:26431/`, `/api/status`
  responded correctly.

## Real-world config gap found during setup, and the fix

The user's actual media layout doesn't have a separate "incoming/staging"
folder — movies and TV live in two already-separate host folders
(`/mnt/data1t/movies`, `/mnt/data1t/tv`) and files should be organized
*in place* within them, not staged elsewhere and copied out. The app as
originally built assumed one mixed incoming folder distinct from the
archive destinations, so `GET /api/scan` only ever looked at
`paths.active_dir` — the two archive folders were write-only as far as
scanning was concerned.

Fixed by making `GET /api/scan` scan the **union** of `active_dir`,
`archive_movies`, and `archive_tv` (`scanner.scan_targets()`, deduping
overlapping roots), and excluding anything already recorded in `media_items`
as either an `original_path` or a `final_path` (`Database.list_known_paths()`)
so a rescan doesn't keep re-offering an already-archived raw file or
re-detecting its own organized copy as "new." This means `active_dir`
no longer has to be a real, distinct folder — pointing all three path
settings at wherever the files actually live now works. Chose this over
the two alternatives that came up: doing nothing (making the user manually
swap `active_dir` between a movies pass and a TV pass) or changing
archiver's copy-to-move semantics (bigger, separate decision — see
Phase 3.4's original checksum-deferred rationale — deliberately not
touched here since it wasn't asked for and duplicating raw+organized
copies is a known, disclosed trade-off of "copy, don't move").

6 new tests cover this (`scan_targets` dedup/exclusion in isolation,
`list_known_paths` in isolation, and a full API-level
scan → archive → rescan-excludes-both-copies flow). 56 tests total, all
passing.

## Second config-model correction: no shared incoming bucket at all

The union-scan fix above (single `active_dir` scanned alongside both archive
roots) was still wrong in a subtler way: it implied movies and TV *could*
share one generic incoming folder, when in reality the user's setup has (and
any real *arr-adjacent layout would have) **no shared bucket at all** —
movies and TV each have their own incoming path, just like they each have
their own archive path. Corrected by replacing `paths.active_dir` with
`paths.incoming_movies` and `paths.incoming_tv`, so the schema now has four
symmetric fields (`incoming_movies`, `incoming_tv`, `archive_movies`,
`archive_tv`) instead of three asymmetric ones. `config.docker.yaml`
defaults `incoming_movies == archive_movies` and `incoming_tv == archive_tv`
(the "organize in place" case, matching this deployment), but a genuinely
separate staging folder per type is equally well-supported now — it wasn't
representable at all under the old three-field schema without conflating
movie and TV incoming files into one directory.

Touched: `config_loader.py` (`PathsConfig`, `_DEFAULT_CONFIG`,
`_EDITABLE_KEYS`), `models.py`/`settings.py` (Settings API request/response),
`scan.py` (union now covers 4 paths), `config.docker.yaml`,
`docker-compose.yml` comments, the Settings tab (now 4 path fields, was 3),
and all the docs/tests that referenced `active_dir`. 57 tests passing
(net +1: a new permissions-check dedup test for incoming==archive).

Also discovered mid-walkthrough: this homelab already runs a full Radarr +
Sonarr + Plex/Jellyfin stack against these exact folders (confirmed via
Arcane's container Storage tab — Radarr's `/movies` mount and Sonarr's `/tv`
mount are the identical host paths). Flagged the functional overlap
directly rather than silently proceeding; the user confirmed they want to
keep both running (Media Manager for a different, unspecified job) rather
than reconsider scope. Worth keeping in mind if Media Manager's future
direction is unclear — it is not filling a gap Radarr/Sonarr leave open for
the core organize/rename workflow, so whatever unique value it's meant to
provide here hasn't been articulated yet.

## Redeploy gotcha: restart/force-recreate do NOT rebuild the image

Discovered while pushing the `active_dir` → `incoming_movies`/`incoming_tv`
fix to the already-running Arcane deployment. After `Sync from Git` pulled
the new commit, neither the project's "restart" icon (does exactly that —
restarts the existing container on its existing image) nor "Force recreate
containers" (recreates the container, still from the existing image) picked
up the new `app/` source. The build log for a recreate showed
`#12 [6/10] COPY app ./app` as `CACHED` even though the workspace files had
genuinely changed. **The only thing that actually rebuilds the image for a
git-synced `build: .` project is stopping the project fully and starting it
again** (Stop → Start/Deploy) — that path's Activity Center log shows real
`docker build` steps for a from-scratch or content-changed build. Verified
the fix actually landed by checking `GET /api/settings`'s response shape
directly (new fields present) rather than trusting the build log alone —
worth doing that verification every time, since "the deploy succeeded" and
"the new code is running" turned out to be separable claims here.

**For any future redeploy of this project in Arcane: Sync from Git, then
Stop, then Start/Deploy — never just restart or force-recreate.**

After that, set `incoming_movies`/`incoming_tv` to `/media/movies`/`/media/tv`
via `POST /api/settings` (the persisted `config.yaml` predates the schema
change and only had `archive_movies`/`archive_tv` populated) and confirmed
`GET /api/scan` found **409 real files** in the actual Radarr/Sonarr-managed
library — first real signal the deployed app can see production data, not
just empty test mounts.

## Scope clarified: manual control + watch tracking, not competing with *arr

Answer to the "what's this actually for" question raised above: manual
watch-state tracking (independent of the `watchstate` container this
homelab already runs — a deliberately separate, simpler mechanism scoped to
this app), a TV status view, hand-picking specific titles for
rename/clean/reorganize outside the Radarr/Sonarr automated pipeline, and
reports on what's archived/watched. First piece built: **Movies and TV
gallery tabs** with a manual watched toggle — the other three (a real
reports view, TV status UI beyond the tracker's pending-only list, and a
"browse everything, not just newly-scanned files" cleanup workflow) were
explicitly deferred by the user in favor of the gallery first.

Implementation:
- `RenamePlan` gained `poster_path`/`overview` (was computed by
  `plan_movie_rename`/`plan_tv_rename` already, via the `MediaResult`
  they're given, but previously discarded — `archiver.archive_file()` now
  persists them into `media_items.metadata` as JSON). This closes a
  pre-existing gap: `renamer.download_artwork()`/`write_nfo()` were written
  per the original plan but never actually called from anywhere; storing
  `poster_path` in the DB and rendering it client-side from the TMDB CDN
  turned out to be all the gallery needed, so those two functions are still
  unwired (fine for now — NFO writing is a separate, unrelated concern for
  Plex/Jellyfin metadata, not blocking this feature).
- New `app/api/routes/library.py`: `GET /api/library/movies`/`tv` (reads
  `media_items`, not a filesystem scan — deliberately distinct from
  `/api/scan`) and `POST /api/library/{id}/watched`.
- Dashboard: two new tabs (Movies, TV) as the default-active views, ahead of
  "Ready to Archive" in the nav. Movies render as a flat poster grid; TV
  episodes come back flat from the API and get grouped into one card per
  show **client-side**, by title, with an expandable episode list each with
  its own watched checkbox. Posters load directly from
  `image.tmdb.org` — no backend image proxy needed.
- 9 new tests (`test_archiver.py`, `test_library.py`, plus poster/overview
  assertions added to existing `test_renamer.py` cases). 66 total, passing.
- Manually verified against a live local server with seeded fake data
  (couldn't use real TMDB data — no API key, and scraper mode's live network
  access is unreliable in this dev environment): real poster image rendered
  from the TMDB CDN, watched checkbox persisted through a full API
  round-trip, TV grouping and per-episode expand/collapse worked correctly.

## Recommended next steps (not done here)

1. Get a real TMDB API key and re-verify search/detail calls end to end (can now be set via the Settings tab, no `.env` needed) — also needed to verify the gallery's poster rendering against real (not manually seeded) archived data.
2. Manually click through the dashboard in a real browser doing a real archive against the live `/mnt/data1t/movies` and `/mnt/data1t/tv` mounts now that `GET /api/scan` finds the 409 real files there — not yet tried an actual preview/confirm against real (not synthetic) media, so the gallery is still empty on the live deployment (it only reflects what *this app* has archived, and nothing has been archived through it yet).
3. Build the three deferred pieces from the scope clarification above: a real reports view, a TV status view beyond the tracker's pending-only list, and a manual library browser for cleanup (not just newly-scanned files).
4. Decide whether to keep `scripts/install_service.sh`/`deploy.sh`/`backup.sh` (systemd-based, Ubuntu-only) around for a non-Docker install path or delete them now that Docker/Arcane is the primary, proven target.
5. Consider publishing a prebuilt image (GHCR) so Arcane can pull instead of building from source each deploy — would also sidestep both the network-specific build issue and the stop/start-to-rebuild gotcha above.
