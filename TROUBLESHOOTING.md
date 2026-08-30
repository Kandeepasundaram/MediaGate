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

## Windows toast notifications never appear
- Confirm `tracker.windows_agent_url` is set on the Ubuntu side and reachable
  (`curl -X POST http://<windows-ip>:8765/notify -d '{"title":"t","body":"b"}'`).
- Confirm Windows Firewall allows inbound TCP on port 8765.
- Confirm the `winrt-Windows.UI.Notifications` / `winrt-Windows.Data.Xml.Dom`
  packages are installed in the Windows agent's virtualenv — without them the
  agent logs the notification instead of showing a toast (check its console
  output / Task Scheduler history).
- Confirm the scheduled task from `scripts/setup_windows.bat` is actually
  running: `schtasks /Query /TN MediaManagerNotificationAgent`.

## SQLite "database is locked"
The database uses WAL mode, so concurrent reads shouldn't block writes. If you
still see lock errors, check for another process (e.g. a manual `sqlite3`
shell) holding a long-running transaction against the same file.

## Cron job runs but nothing happens
`scripts/cron_job.py` only notifies for tracker entries with
`pending_notification=1` and no `notification_sent_at`. Check
`/api/tracker/status` and `/api/tracker/notifications` to see current tracker
state, and the log file for `tracker_check` failures.
