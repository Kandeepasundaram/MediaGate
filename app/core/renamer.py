"""Builds destination paths and (optionally) artwork/NFO for archived media."""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from pathlib import Path

import requests

from app.core.tmdb_client import MediaResult, TMDBClient, genres_for, vote_average_for

logger = logging.getLogger(__name__)

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
    poster_path: str | None = None
    overview: str = ""
    vote_average: float | None = None
    genres: list[str] = field(default_factory=list)


def plan_movie_rename(source: Path, archive_root: Path, media: MediaResult, ext: str | None = None) -> RenamePlan:
    ext = ext or source.suffix
    title = sanitize_filename(media.title)
    year = media.year
    folder_name = f"{title} ({year})" if year else title
    file_name = f"{folder_name}{ext}"
    dest = archive_root / folder_name / file_name
    dest = _avoid_collision(dest)
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
    ext: str | None = None,
) -> RenamePlan:
    ext = ext or source.suffix
    show_name = sanitize_filename(media.title)
    season_folder = f"Season {season:02d}"
    code = f"S{season:02d}E{episode:02d}"
    if episode_title:
        file_name = f"{show_name} - {code} - {sanitize_filename(episode_title)}{ext}"
    else:
        file_name = f"{show_name} - {code}{ext}"
    dest = archive_root / show_name / season_folder / file_name
    dest = _avoid_collision(dest)
    return RenamePlan(
        source_path=source,
        dest_path=dest,
        media_type="tv",
        tmdb_id=media.tmdb_id,
        title=media.title,
        season=season,
        episode=episode,
        episode_title=episode_title,
        poster_path=media.poster_path,
        overview=media.overview,
        vote_average=vote_average_for(media),
        genres=genres_for(media),
    )


def _avoid_collision(dest: Path) -> Path:
    if not dest.exists():
        return dest
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
