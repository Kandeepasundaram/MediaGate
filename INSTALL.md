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

## Windows client (dashboard access + notifications)

1. Open `http://<ubuntu-host-ip>:8000` in a browser to use the dashboard — no
   client install needed for that part.
2. For toast notifications, on the Windows machine:
   ```powershell
   python -m venv .venv
   .venv\Scripts\pip install -r requirements.txt
   .venv\Scripts\pip install winrt-Windows.UI.Notifications winrt-Windows.Data.Xml.Dom
   ```
3. Run `scripts\setup_windows.bat` as Administrator to register the agent
   (`scripts/windows_toast.py`) as a Task Scheduler entry that starts on logon.
4. Set `tracker.windows_agent_url` in `config.yaml` on the Ubuntu side to
   `http://<windows-machine-ip>:8765/notify` so the cron job can reach it.
5. Allow inbound TCP 8765 through Windows Firewall for the notification agent.

## Troubleshooting

See `TROUBLESHOOTING.md`.
