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
- **Windows notification agent**: `scripts/windows_toast.py`, a tiny HTTP server that
  turns POSTs from the Ubuntu cron job into Windows toast notifications.

See `phases plan.txt` for the original phased build plan and `CLAUDE.md` for
architecture notes aimed at AI coding agents working in this repo.

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
