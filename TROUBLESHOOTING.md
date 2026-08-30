# Troubleshooting

## Server won't start: "Address already in use"
Another process is bound to the configured port. Change `server.port` in
`config.yaml` or stop the conflicting process (`sudo lsof -i :8000` on Ubuntu).

## Dashboard loads but scan finds no files
- Confirm `paths.active_dir` in `config.yaml` points at the mounted drive, not
  a stale/unmounted path.
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
in `active_dir` is left untouched on both success and failure — safe to retry.

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

## Cron job runs but nothing happens
`scripts/cron_job.py` only notifies for tracker entries with
`pending_notification=1` and no `notification_sent_at`. Check
`/api/tracker/status` and `/api/tracker/notifications` to see current tracker
state, and the log file for `tracker_check` failures.
