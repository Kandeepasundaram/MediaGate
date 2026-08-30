# Configuration Reference

All settings live in `config.yaml` at the project root. It is created with
defaults on first run if missing. Secrets should go in `.env` (see
`.env.example`) rather than being committed to `config.yaml`.

## `paths`
| Key | Default | Description |
|---|---|---|
| `active_dir` | `./sample_media/incoming` | Directory scanned for new media to archive. |
| `archive_movies` | `./sample_media/archive/movies` | Root folder movies are archived into. |
| `archive_tv` | `./sample_media/archive/tv` | Root folder TV episodes are archived into. |

## `database`
| Key | Default | Description |
|---|---|---|
| `path` | `./data/media_manager.db` | SQLite file location. |

## `tmdb`
| Key | Default | Description |
|---|---|---|
| `api_key` | `""` | TMDB API key. Prefer the `TMDB_API_KEY` env var over committing this here. |
| `language` | `en-US` | Language for TMDB API responses. |

## `subtitles`
| Key | Default | Description |
|---|---|---|
| `keep_languages` | `["en", "eng", "english"]` | Subtitle language tags to keep; matched against the last dot-separated segment of the filename stem (e.g. `Movie.en.srt`). Untagged files (`Movie.srt`) are always kept. |
| `delete_extensions` | `[".srt", ".ass", ".ssa"]` | Subtitle file extensions considered for purging. |

## `tracker`
| Key | Default | Description |
|---|---|---|
| `cron_time` | `06:00` | Documented schedule for the tracker cron job (the actual schedule is set in crontab, this is just a record of intent). |
| `notification_ttl_days` | `30` | Not yet enforced by code — reserved for future notification expiry. |

## `logging`
| Key | Default | Description |
|---|---|---|
| `level` | `INFO` | Python logging level. |
| `file` | `./logs/media_manager.log` | Log file path. |

## `server`
| Key | Default | Description |
|---|---|---|
| `host` | `0.0.0.0` | Bind address for uvicorn. |
| `port` | `8000` | Bind port for uvicorn. |
| `cors_origins` | `["*"]` | Allowed CORS origins for the dashboard API. Restrict this once the Windows client IPs are known. |

## Environment variable overrides

| Variable | Overrides |
|---|---|
| `TMDB_API_KEY` | `tmdb.api_key` |
| `MEDIA_MANAGER_CONFIG` | Path to `config.yaml` itself (default: `config.yaml` in the working directory). |
