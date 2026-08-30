# Installation Guide

## Docker / Arcane (recommended)

The app is a single generic container — nothing homelab-specific is baked in
at build time. Everything (media paths, TMDB key) is set from the dashboard
after first boot.

1. **Two things persist across restarts and must be real volumes, not left
   as anonymous/ephemeral storage:**
   - `/config` — holds `config.yaml` (your Settings-tab changes), the SQLite
     database, and logs.
   - `/media` — bind-mount your actual media library's root here. The app
     never sees paths outside whatever you mount at `/media`.
2. **Via `docker-compose.yml`** (works as-is with Arcane, which manages
   compose stacks): edit the `/path/on/host/to/media:/media` line to your
   real host path, then:
   ```bash
   docker compose up -d --build
   ```
   Or in Arcane: create a new stack, paste `docker-compose.yml`'s contents,
   edit that one volume line, deploy.
3. **First boot**: open `http://<host-ip>:26431`. Go to **Settings** and set
   the Incoming/Movies/TV directories to subpaths under `/media` matching
   your actual layout (e.g. `/media/incoming`, `/media/movies`, `/media/tv`).
   Optionally set a TMDB API key there too — leave it blank to run in
   scraper-fallback mode.
4. **Test write access**: Settings tab → "Test Permissions". If a path shows
   not writable, either the host directory doesn't exist yet (create it) or
   its ownership doesn't match the container's user. The container runs as
   root by default (simplest for arbitrary host bind-mount ownership in a
   single-user homelab); to lock it down, uncomment `user: "uid:gid"` in
   `docker-compose.yml` to match your media directory's owner — but note the
   app **cannot fix permissions from the web UI**, only report them; ownership
   changes are a compose/host-level action, not something a request should be
   allowed to trigger.
5. **Notifications**: no extra setup — accept the browser's notification
   permission prompt on the dashboard. See `README.md` for how this works.
6. **Reverse proxy**: none required. The app doesn't hardcode absolute URLs
   or assume a specific host/port, so putting Traefik/Caddy/nginx in front of
   it later needs no app changes — just point the proxy at the published
   port (26431, or whatever `docker-compose.yml`'s `ports:` maps it to; the
   container listens on 8000 internally regardless).

## Bare-metal Ubuntu (alternative to Docker)

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
3. **Mount the external HDD** and either point `config.yaml`'s
   `paths.*` at it directly, or leave the defaults and repoint them from the
   Settings tab after first boot — same as the Docker path.
4. **Configure secrets**: copy `.env.example` to `.env` and set `TMDB_API_KEY`
   (optional — the app falls back to a scraper if omitted, and this can also
   be set later from the Settings tab).
5. **Install as a systemd service**:
   ```bash
   sudo ./scripts/install_service.sh
   ```
   This starts the FastAPI server on port 8000 and enables it on boot. Note:
   the tracker check runs in-process on a daily schedule
   (`app/core/scheduler.py`) as long as the server is running — you do
   **not** need `scripts/cron_job.py` for this. It's kept only for anyone who
   prefers an external cron entry instead:
   ```cron
   0 6 * * * /home/<user>/media-manager/.venv/bin/python /home/<user>/media-manager/scripts/cron_job.py
   ```
6. **Schedule daily backups**:
   ```cron
   30 3 * * * /home/<user>/media-manager/scripts/backup.sh
   ```

## Dashboard access + notifications (either deployment)

No client install needed. Open `http://<server-ip>:26431` (Docker — or
`:8000` for a bare-metal install) in any browser. The
dashboard will ask for notification permission on first load — accept it and
you'll get a native OS notification whenever the tracker check flags a new
season or sequel, for as long as that browser tab is open (it can be
minimized/backgrounded).

## Troubleshooting

See `TROUBLESHOOTING.md`.
