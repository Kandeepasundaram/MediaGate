# Installation Guide

## Docker / Arcane (recommended)

The app is a single generic container — nothing homelab-specific is baked in
at build time. Everything (media paths, TMDB key) is set from the dashboard
after first boot.

1. **Two things persist across restarts and must be real volumes, not left
   as anonymous/ephemeral storage:**
   - `/config` — holds `config.yaml` (your Settings-tab changes), the SQLite
     database, and logs.
   - `/media/movies`, `/media/tv` — bind-mount your actual movie/TV folders
     here. Whatever's already in them gets scanned in place; you don't need
     a separate staging folder (see `paths.active_dir` in `CONFIG.md` if you
     want one anyway).
2. **Set the real host paths in `.env`, not `docker-compose.yml`** — the
   compose file is git-managed (Arcane treats it read-only once deployed
   from a repo), so host-specific paths live in `.env` instead, which stays
   editable:
   ```bash
   cp .env.example .env
   # edit .env: MOVIES_HOST_PATH=/your/real/movies/path
   #            TV_HOST_PATH=/your/real/tv/path
   docker compose up -d --build
   ```
   In Arcane: create a Project "From Git Repo" pointing at this repo (add it
   under Customization → Git Repositories first if it's not already there;
   auth type "None" works for a public repo), enable **Sync Files** (`build:
   .` needs the Dockerfile and app source alongside the compose file, not
   just the compose file itself), sync, then edit the project's `.env` panel
   with your real `MOVIES_HOST_PATH`/`TV_HOST_PATH`, then start it.
3. **First boot**: open `http://<host-ip>:26431`. Go to **Settings** and
   optionally set a TMDB API key — leave it blank to run in scraper-fallback
   mode. The Incoming/Movies/TV path fields default to `/media/incoming`
   (unmounted, harmless), `/media/movies`, `/media/tv` — leave the last two
   as-is to match the bind mounts above.
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
