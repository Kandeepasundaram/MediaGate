# Troubleshooting

## Server won't start: "Address already in use"
Another process is bound to the configured port. Change `server.port` in
`config.yaml` or stop the conflicting process (`sudo lsof -i :8000` on Ubuntu).

## Dashboard loads but scan finds no files
- Confirm `paths.incoming_movies`/`incoming_tv` (and `archive_movies`/
  `archive_tv`, all scanned together) in `config.yaml` point at mounted,
  populated drives, not stale/unmounted or genuinely-empty paths.
- Confirm the file extensions match `VIDEO_EXTENSIONS` in `app/core/scanner.py`
  (`.mkv, .mp4, .avi, .mov, .wmv, .flv, .webm`).
- Check the log file (`logging.file`, default `./logs/media_manager.log`) for
  permission errors reading the directory.

## TMDB lookups return no results / wrong titles
- Check `/api/status` — the `tmdb_mode` field shows `api` or `scraper`.
- In scraper mode, if themoviedb.org's page markup has changed, the CSS
  selectors in `app/core/tmdb_scraper.py` may need updating.
- In API mode, verify `TMDB_API_KEY` is set and valid; the client automatically
  falls back to the scraper on any API error (check logs for the fallback
  warning).
- Filenames that don't match the regexes in `app/core/tmdb_client.py::parse_filename`
  will produce a poor guess at the title — rename the source file or extend
  the regex.

## Archive operation fails partway through
Check `/api/archive/history` or the `operation_log` table — failed entries
include an `error_message` (typically a permissions or disk-space issue on the
archive destination). Archiving copies files (`shutil.copy2`), so the original
in the incoming folder is left untouched on both success and failure — safe
to retry.

## Browser notifications never appear
- Check the browser's site permission for the dashboard's origin — it must be
  "Allow", not "Block" or the default "Ask" (a page reload after clicking
  "Allow" in the permission prompt is required the first time).
- Browser notifications only fire while a dashboard tab is open somewhere
  (it can be minimized/backgrounded); closing the browser entirely stops them.
- Check `/api/tracker/notifications` directly — if it's empty, the tracker
  cron job hasn't flagged anything yet (see below), it's not a browser issue.

## SQLite "database is locked"
The database uses WAL mode, so concurrent reads shouldn't block writes. If you
still see lock errors, check for another process (e.g. a manual `sqlite3`
shell) holding a long-running transaction against the same file.

## Tracker never flags anything
Check `/api/tracker/status` and `/api/tracker/notifications` for current
state, and the log file for `tracker_check` failures. In Docker, the daily
check runs in-process (`app/core/scheduler.py`) as long as the container is
running — check the container logs around `tracker.cron_time` (default
`06:00`) for "Scheduled tracker check complete" / "failed" lines. Restarting
the container resets the daily timer (it schedules relative to the next
`cron_time`, not a fixed interval from last run), so frequent restarts can
delay a check by up to a day.

## Docker: settings I saved didn't stick after redeploying the stack
`/config` isn't a persistent volume (or you're using `docker compose down -v`,
which deletes volumes). Check `docker-compose.yml` — `media-manager-config`
must be a named volume or bind mount, and it must survive between deploys.

## Docker: "Test Permissions" shows a path as not writable
The container's user (root by default) can't write to that host directory —
usually an NFS/SMB mount with restrictive permissions, or a bind mount owned
by a different UID after setting `user:` in `docker-compose.yml`. The app
only reports this; it doesn't attempt to `chown` anything. Fix on the host
(`chown`/`chmod` the directory, or match `user:` in compose to its owner) and
redeploy.

## Docker: `pip install` fails during build with an SSL certificate error
This means something on the build host's network path (a proxy, VPN client,
or antivirus doing TLS inspection) is intercepting HTTPS to pypi.org and its
certificate isn't trusted inside the build container — not a problem with
this repo's `Dockerfile`. Confirm with a plain
`docker run --rm python:3.12-slim python -c "import urllib.request; urllib.request.urlopen('https://pypi.org')"`
from the same host; if that also fails, the issue is the host's network, not
the build. Building from a different network (or the actual Arcane host)
should work without changes.
