"""Builds destination paths and (optionally) artwork/NFO for archived media."""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from pathlib import Path

import requests

from app.config_loader import RenamingConfig
from app.core.tmdb_client import MediaResult, TMDBClient, genres_for, vote_average_for

logger = logging.getLogger(__name__)


class CollisionSkipped(Exception):
    """Raised for collision_policy="skip" when the computed destination
    already exists -- callers (the /api/archive/preview route) already
    catch per-file exceptions and surface them as a preview error, so this
    needs no special handling beyond a clear message."""


_DEFAULT_RENAMING = RenamingConfig()


def _format_template(template: str, default: str, context: dict) -> str:
    """Renders a user-configurable naming template, falling back to the
    built-in default on any bad token/format-spec rather than failing the
    whole preview -- a typo'd Settings template shouldn't block archiving."""
    try:
        return template.format(**context)
    except (KeyError, ValueError, IndexError) as exc:
        logger.warning("Invalid naming template %r (%s); using default", template, exc)
        return default.format(**context)


_INVALID_CHARS = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
TMDB_IMAGE_BASE = "https://image.tmdb.org/t/p/original"


def sanitize_filename(name: str) -> str:
    cleaned = _INVALID_CHARS.sub("", name).strip().rstrip(".")
    return cleaned or "untitled"


@dataclass
class RenamePlan:
    source_path: Path
    dest_path: Path
    media_type: str
    tmdb_id: int | None
    title: str
    year: int | None = None
    season: int | None = None
    episode: int | None = None
    episode_title: str | None = None
    air_date: str | None = None
    poster_path: str | None = None
    overview: str = ""
    vote_average: float | None = None
    genres: list[str] = field(default_factory=list)


def plan_movie_rename(
    source: Path,
    archive_root: Path,
    media: MediaResult,
    ext: str | None = None,
    renaming: RenamingConfig | None = None,
    part: str | None = None,
) -> RenamePlan:
    """`part` ("CD1", "Part2", "Disc1", ... -- see tmdb_client._PART_PATTERN)
    is appended to the filename, not the folder: a multi-part rip is still
    one movie sharing one folder, but each part needs a distinct filename
    or the second one archived would just collide with (and, depending on
    collision_policy, silently overwrite or get an unhelpful "(2)" suffix
    on) the first. Movies have no user-configurable file template (see
    RenamingConfig's own docstring), so this is a fixed " - {part}" suffix
    rather than another template token.
    """
    renaming = renaming or _DEFAULT_RENAMING
    ext = ext or source.suffix
    title = sanitize_filename(media.title)
    year = media.year
    context = {"title": title, "year": year or "", "year_suffix": f" ({year})" if year else ""}
    folder_name = sanitize_filename(_format_template(renaming.movie_folder, _DEFAULT_RENAMING.movie_folder, context))
    part_suffix = f" - {part}" if part else ""
    file_name = f"{folder_name}{part_suffix}{ext}"
    dest = archive_root / folder_name / file_name
    dest = _avoid_collision(dest, renaming.collision_policy)
    return RenamePlan(
        source_path=source,
        dest_path=dest,
        media_type="movie",
        tmdb_id=media.tmdb_id,
        title=media.title,
        year=year,
        poster_path=media.poster_path,
        overview=media.overview,
        vote_average=vote_average_for(media),
        genres=genres_for(media),
    )


def plan_tv_rename(
    source: Path,
    archive_root: Path,
    media: MediaResult,
    season: int,
    episode: int,
    episode_title: str | None = None,
    air_date: str | None = None,
    ext: str | None = None,
    renaming: RenamingConfig | None = None,
    absolute_episode: int | None = None,
    part: str | None = None,
) -> RenamePlan:
    """`absolute_episode` -- cumulative episode number across seasons (see
    tmdb_client.compute_absolute_episode) -- backs the `{absolute_episode}`
    naming-template token some anime-style layouts use instead of SxxExx.
    Falls back to the plain in-season `episode` number when the caller
    couldn't compute one (e.g. no full TV details fetched, or TMDB's own
    season data is incomplete), so an unset template token never breaks
    the rest of the filename.
    """
    renaming = renaming or _DEFAULT_RENAMING
    ext = ext or source.suffix
    show_name = sanitize_filename(media.title)
    code = f"S{season:02d}E{episode:02d}"
    clean_episode_title = sanitize_filename(episode_title) if episode_title else ""
    context = {
        "show_name": show_name, "season": season, "episode": episode, "code": code,
        "episode_title": clean_episode_title,
        "episode_title_suffix": f" - {clean_episode_title}" if clean_episode_title else "",
        "absolute_episode": absolute_episode if absolute_episode is not None else episode,
        "part_suffix": f" - {part}" if part else "",
        "air_date": air_date or "",
    }
    season_folder = sanitize_filename(_format_template(renaming.tv_season_folder, _DEFAULT_RENAMING.tv_season_folder, context))
    file_name = sanitize_filename(_format_template(renaming.tv_file, _DEFAULT_RENAMING.tv_file, context)) + ext
    dest = archive_root / show_name / season_folder / file_name
    dest = _avoid_collision(dest, renaming.collision_policy)
    return RenamePlan(
        source_path=source,
        dest_path=dest,
        media_type="tv",
        tmdb_id=media.tmdb_id,
        title=media.title,
        season=season,
        episode=episode,
        episode_title=episode_title,
        air_date=air_date,
        poster_path=media.poster_path,
        overview=media.overview,
        vote_average=vote_average_for(media),
        genres=genres_for(media),
    )


TV_PATTERN_PRESETS: list[dict[str, str]] = [
    {"id": "default", "label": "Default (Show - S01E02 - Title)",
     "tv_season_folder": "Season {season:02d}", "tv_file": "{show_name} - {code}{episode_title_suffix}"},
    {"id": "code-only", "label": "Code only (S01E02)",
     "tv_season_folder": "Season {season:02d}", "tv_file": "{code}"},
    {"id": "code-title", "label": "Show - S01E02 - Title (title required)",
     "tv_season_folder": "Season {season:02d}", "tv_file": "{show_name} - {code} - {episode_title}"},
    {"id": "x-style", "label": "Show - 1x02 - Title",
     "tv_season_folder": "Season {season}", "tv_file": "{show_name} - {season}x{episode:02d}{episode_title_suffix}"},
    {"id": "absolute", "label": "Anime absolute (Show - 001)",
     "tv_season_folder": "Season {season:02d}", "tv_file": "{show_name} - {absolute_episode:03d}{episode_title_suffix}"},
    {"id": "air-date", "label": "Air date (Show - 2020-01-31 - Title)",
     "tv_season_folder": "Season {season:02d}", "tv_file": "{show_name} - {air_date}{episode_title_suffix}"},
    {"id": "short-folder", "label": "Short folder (S01)",
     "tv_season_folder": "S{season:02d}", "tv_file": "{show_name} - {code}{episode_title_suffix}"},
    {"id": "code-first", "label": "Code first (S01E02 - Show - Title)",
     "tv_season_folder": "Season {season:02d}", "tv_file": "{code} - {show_name}{episode_title_suffix}"},
]

TV_TEMPLATE_TOKENS = [
    "show_name", "season", "episode", "code", "episode_title", "episode_title_suffix",
    "absolute_episode", "part_suffix", "air_date",
]


def validate_tv_templates(season_folder: str, file_template: str) -> str | None:
    """Returns a human-readable error if either TV template can't be
    rendered (bad token / format spec), else None. plan_tv_rename silently
    falls back to the default on such errors; the rename simulator needs to
    flag them instead."""
    sample = {
        "show_name": "Show", "season": 1, "episode": 2, "code": "S01E02",
        "episode_title": "Title", "episode_title_suffix": " - Title",
        "absolute_episode": 2, "part_suffix": "", "air_date": "2020-01-31",
    }
    for label, template in (("Season folder", season_folder), ("File name", file_template)):
        try:
            template.format(**sample)
        except KeyError as exc:
            return f"{label}: unknown token {{{exc.args[0]}}}"
        except (ValueError, IndexError) as exc:
            return f"{label}: invalid format ({exc})"
    return None


def _avoid_collision(dest: Path, policy: str = "suffix") -> Path:
    if not dest.exists():
        return dest
    if policy == "overwrite":
        return dest
    if policy == "skip":
        raise CollisionSkipped(f"Destination already exists (collision policy is 'skip'): {dest}")
    stem, suffix, parent = dest.stem, dest.suffix, dest.parent
    counter = 2
    while True:
        candidate = parent / f"{stem} ({counter}){suffix}"
        if not candidate.exists():
            return candidate
        counter += 1


def download_artwork(dest_folder: Path, poster_path: str | None, backdrop_path: str | None = None) -> dict[str, Path]:
    """Best-effort poster/fanart download. Failures are logged, not raised."""
    dest_folder.mkdir(parents=True, exist_ok=True)
    saved: dict[str, Path] = {}
    for name, image_path in (("poster.jpg", poster_path), ("fanart.jpg", backdrop_path)):
        if not image_path:
            continue
        try:
            resp = requests.get(f"{TMDB_IMAGE_BASE}{image_path}", timeout=10)
            resp.raise_for_status()
            target = dest_folder / name
            target.write_bytes(resp.content)
            saved[name] = target
        except requests.RequestException as exc:
            logger.warning("Artwork download failed for %s: %s", image_path, exc)
    return saved


def write_nfo(dest_folder: Path, media: MediaResult) -> Path:
    nfo_path = dest_folder / (f"{media.media_type}.nfo")
    root_tag = "movie" if media.media_type == "movie" else "tvshow"
    nfo_path.write_text(
        f"<{root_tag}>\n"
        f"  <title>{media.title}</title>\n"
        f"  <year>{media.year or ''}</year>\n"
        f"  <tmdbid>{media.tmdb_id or ''}</tmdbid>\n"
        f"  <plot>{media.overview}</plot>\n"
        f"</{root_tag}>\n",
        encoding="utf-8",
    )
    return nfo_path


def fetch_episode_title(client: TMDBClient, tv_tmdb_id: int, season: int, episode: int) -> str | None:
    """Best-effort episode title lookup; only works in API mode (tmdbv3api)."""
    if client.mode != "api":
        return None
    try:
        from tmdbv3api import Episode

        ep = Episode().details(tv_tmdb_id, season, episode)
        return getattr(ep, "name", None)
    except Exception as exc:
        logger.warning("Episode title lookup failed for tmdb_id=%s S%02dE%02d: %s", tv_tmdb_id, season, episode, exc)
        return None
