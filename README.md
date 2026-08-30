# Media Manager

Self-hosted, manual-control complement to an automated *arr stack (Radarr/Sonarr):
where those handle the automated download-to-library pipeline, Media Manager is
for picking specific titles to rename/clean/re-organize by hand, tracking your
own watch state, and browsing what's archived — not for competing with the
automated import. It scans separate incoming folders for movies and TV (which
can be the same as the archive folder if your library is organized in-place),
fetches metadata from TMDB, renames and archives movies/TV episodes, purges
non-English subtitles, and tracks shows/movies for new-season and sequel
releases. A web dashboard (served by the same FastAPI app) lets you review and
approve archive operations, browse your library as movie/TV poster galleries
with a watched toggle, and hand-pick specific files in the archive folders
(tracked or not) to re-run through TMDB matching or delete outright — from
any machine on the LAN, with browser-native notifications when the tracker
finds something new.

## Architecture

- **Backend**: FastAPI + SQLite, one Docker container (see `docker-compose.yml`) with the media library and app state bind-mounted/volumed in.
- **Dashboard**: vanilla HTML/CSS/JS single-page app served from `app/static/`, with Movies/TV gallery tabs (`GET /api/library/movies`/`tv`) showing TMDB posters and a manual watched toggle (`POST /api/library/{id}/watched`), and a Browse & Clean Up tab (`GET /api/library/browse`, `POST /api/library/delete-file`) for manual cleanup of anything sitting in the archive folders — independent of automated *arr imports.
- **TMDB integration**: hybrid client (`app/core/tmdb_client.py`) — uses the official
  API when `TMDB_API_KEY` is set, otherwise falls back to scraping themoviedb.org.
- **Notifications**: browser `Notification` API in the dashboard (`app/static/app.js`)
  — no OS-specific agent. Any machine with the dashboard open in a browser gets a
  native notification when the tracker check flags a new season/sequel.
- **Scheduling**: an in-process daily job (`app/core/scheduler.py`) runs the tracker
  check — no host cron needed when running in Docker. `scripts/cron_job.py` still
  exists for a non-Docker install that prefers external cron/systemd-timer instead.
- **Settings**: media paths, the TMDB key, and CORS origins are editable at runtime
  from the dashboard's Settings tab (`GET`/`POST /api/settings`), persisted back to
  `config.yaml` — no rebuild or restart required. A `TMDB_API_KEY` env var, if set,
  takes precedence and locks that field in the UI.

See `phases plan.txt` for the original phased build plan and `CLAUDE.md` for
architecture notes aimed at AI coding agents working in this repo.

## Quick start (Docker / homelab)

```bash
cp .env.example .env   # set MOVIES_HOST_PATH / TV_HOST_PATH to your real folders
docker compose up -d --build
```

Host paths go in `.env`, not `docker-compose.yml` — keeps them out of git
and out of Arcane's read-only synced compose file. Everything else (TMDB
key, whether you want a separate incoming folder) is configured from the
dashboard's **Settings** tab after first boot, no rebuild needed.
Dashboard's published on port **26431** (container listens on 8000
internally; change the `ports:` line if you want a different published
port). See `INSTALL.md` for the Arcane-specific
walkthrough.

## Quick start (development)

```bash
python -m venv .venv
.venv/Scripts/pip install -r requirements.txt   # .venv/bin/pip on Linux/macOS
.venv/Scripts/python -m uvicorn app.main:app --reload
```

Open http://localhost:8000 for the dashboard, http://localhost:8000/docs for the
auto-generated API reference.

Run the test suite with:

```bash
.venv/Scripts/python -m pytest
```

For production deployment on Ubuntu, see `INSTALL.md`. For all configuration
options, see `CONFIG.md`. For common problems, see `TROUBLESHOOTING.md`.
