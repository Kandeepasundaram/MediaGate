# Configuration Reference

All settings live in `config.yaml`. It is created with defaults on first run
if missing — from `_DEFAULT_CONFIG` in `app/config_loader.py` for a bare
install, or from `config.docker.yaml` (absolute `/media`, `/config` paths)
when running in Docker, seeded by `docker-entrypoint.sh`. Secrets should go
in `.env` / an env var (see `.env.example`) rather than being committed to
`config.yaml`.

**Runtime-editable via the Settings tab / `GET`/`POST /api/settings`:**
`paths.active_dir`, `paths.archive_movies`, `paths.archive_tv`,
`tmdb.api_key`, `server.cors_origins`. A POST there rewrites `config.yaml`
in place (comments and formatting are not preserved — the file is
YAML-serialized fresh each save) and busts the app's cached config/DB/TMDB
singletons immediately, no restart needed. Everything else below is
file-only — edit `config.yaml` directly and restart.

## `paths`
| Key | Default | Description |
|---|---|---|
| `active_dir` | `./sample_media/incoming` | Directory scanned for new media to archive. |
| `archive_movies` | `./sample_media/archive/movies` | Root folder movies are archived into. |
| `archive_tv` | `./sample_media/archive/tv` | Root folder TV episodes are archived into. |

Editing these via the Settings API does **not** auto-create the directory if
it doesn't exist (unlike the very first app boot, which does) — use
`GET /api/settings/permissions-check` (wired to the Settings tab's "Test
Permissions" button) to confirm a path exists and is writable before relying
on it.

## `database`
| Key | Default | Description |
|---|---|---|
| `path` | `./data/media_manager.db` | SQLite file location. |

## `tmdb`
| Key | Default | Description |
|---|---|---|
| `api_key` | `""` | TMDB API key. Editable from the Settings tab; if the `TMDB_API_KEY` env var is set it always wins and the Settings UI shows the field as locked. |
| `language` | `en-US` | Language for TMDB API responses. |

## `subtitles`
| Key | Default | Description |
|---|---|---|
| `keep_languages` | `["en", "eng", "english"]` | Subtitle language tags to keep; matched against the last dot-separated segment of the filename stem (e.g. `Movie.en.srt`). Untagged files (`Movie.srt`) are always kept. |
| `delete_extensions` | `[".srt", ".ass", ".ssa"]` | Subtitle file extensions considered for purging. |

## `tracker`
| Key | Default | Description |
|---|---|---|
| `cron_time` | `06:00` | 24h `HH:MM` time the in-process daily tracker check (`app/core/scheduler.py`) runs at. Not editable via the Settings API yet — edit `config.yaml` and restart. |
| `notification_ttl_days` | `30` | Not yet enforced by code — reserved for future notification expiry. |

## `logging`
| Key | Default | Description |
|---|---|---|
| `level` | `INFO` | Python logging level. |
| `file` | `./logs/media_manager.log` | Log file path. |

## `server`
| Key | Default | Description |
|---|---|---|
| `host` | `0.0.0.0` | Not currently read by any startup path — informational only. The actual bind address is hardcoded (`0.0.0.0`) in `docker-entrypoint.sh` / whatever `uvicorn` invocation you use. |
| `port` | `8000` | Same caveat — `docker-entrypoint.sh` hardcodes `--port 8000`. To publish the app on a different port in Docker, change `docker-compose.yml`'s `ports:` mapping (host-side) rather than this value; the container always listens on 8000 internally. |
| `cors_origins` | `["*"]` | Allowed CORS origins for the dashboard API. The dashboard is same-origin with the API by default, so this rarely needs changing. Editable from the Settings tab. |

## Environment variable overrides

| Variable | Overrides |
|---|---|
| `TMDB_API_KEY` | `tmdb.api_key` |
| `MEDIA_MANAGER_CONFIG` | Path to `config.yaml` itself (default: `config.yaml` in the working directory). |
