# Installation Guide

## Ubuntu server (backend)

1. **Prerequisites**: Python 3.8+, pip, git, sqlite3, build-essential.
   ```bash
   sudo apt update
   sudo apt install -y python3 python3-venv python3-pip git sqlite3 build-essential
   ```
2. **Clone and set up the virtual environment**:
   ```bash
   git clone <repo-url> ~/media-manager
   cd ~/media-manager
   python3 -m venv .venv
   .venv/bin/pip install -r requirements.txt
   ```
3. **Mount the external HDD** and make sure the paths in `config.yaml`
   (`paths.active_dir`, `paths.archive_movies`, `paths.archive_tv`) point at it.
4. **Configure secrets**: copy `.env.example` to `.env` and set `TMDB_API_KEY`
   (optional — the app falls back to a scraper if omitted).
5. **Install as a systemd service**:
   ```bash
   sudo ./scripts/install_service.sh
   ```
   This starts the FastAPI server on port 8000 and enables it on boot.
6. **Schedule the tracker cron job** (checks for new seasons/sequels daily):
   ```cron
   0 6 * * * /home/<user>/media-manager/.venv/bin/python /home/<user>/media-manager/scripts/cron_job.py
   ```
7. **Schedule daily backups**:
   ```cron
   30 3 * * * /home/<user>/media-manager/scripts/backup.sh
   ```

## Windows (or any) client — dashboard access + notifications

No install needed. Open `http://<server-ip>:8000` in any browser. The
dashboard will ask for notification permission on first load — accept it and
you'll get a native OS notification whenever the tracker cron job flags a new
season or sequel, for as long as that browser tab is open (it can be
minimized/backgrounded).

## Troubleshooting

See `TROUBLESHOOTING.md`.
