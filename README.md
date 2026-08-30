# Media Manager

Self-hosted media management system: scans an incoming directory, fetches metadata
from TMDB, renames and archives movies/TV episodes to an external drive, purges
non-English subtitles, and tracks shows/movies for new-season and sequel releases.
A web dashboard (served by the same FastAPI app) lets you review and approve
archive operations from any machine on the LAN, and a small Windows agent turns
tracker alerts into native toast notifications.

## Architecture

- **Backend**: FastAPI + SQLite, runs on the Ubuntu host that has the media drive mounted.
- **Dashboard**: vanilla HTML/CSS/JS single-page app served from `app/static/`.
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
docker compose up -d --build
```

Edit `docker-compose.yml` first to point the `/media` bind mount at your
actual media library root — everything else (incoming/movie/TV subpaths,
TMDB key) is configured from the dashboard's **Settings** tab after first
boot, no `.env` or rebuild needed. See `INSTALL.md` for the Arcane-specific
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
