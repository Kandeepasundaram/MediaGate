"""Obsidian "Media DB" plugin-style note generation for archived movies and
TV shows -- download or save-into-folder, split out of library.py (see that
module's own docstring) since notes are a self-contained concern with no
overlap with the gallery/browse/maintenance routes.
"""
from __future__ import annotations

from pathlib import Path
from urllib.parse import quote

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import Response

from app.api.routes.library_common import _metadata_dict
from app.core.media_note import build_movie_note, build_tv_note
from app.core.omdb_client import OMDbClient
from app.core.renamer import TMDB_IMAGE_BASE, sanitize_filename
from app.database import Database
from app.dependencies import get_database, get_omdb_client
from app.models import NoteSaveResponse

router = APIRouter(prefix="/api/library", tags=["library"])


class NoteError(Exception):
    """Raised by _generate_movie_note/_generate_tv_note when a note can't
    be built -- the four note routes below catch this and translate it to
    the matching HTTPException, so these generation helpers stay
    HTTP-agnostic, the same core-raises/route-translates convention
    ArchiveError and OrganizeError already use elsewhere in this app."""

    def __init__(self, detail: str, status_code: int = 404):
        super().__init__(detail)
        self.detail = detail
        self.status_code = status_code


def _generate_movie_note(item_id: int, db: Database, omdb: OMDbClient) -> tuple[str, str]:
    """Returns (markdown_text, filename). Shared by the download and
    save-to-folder routes below so they can never drift out of sync with
    each other. Movies only -- the Obsidian Media DB plugin's frontmatter
    shape for a TV show/episode is different enough (season/episode
    fields, a show-level vs episode-level note) that reusing this
    movie-shaped template for TV would just produce a note the plugin
    doesn't recognize correctly.
    """
    item = db.get_media_item(item_id)
    if item is None:
        raise NoteError("Media item not found")
    if item["media_type"] != "movie":
        raise NoteError("Notes are only generated for movies", status_code=400)

    meta = _metadata_dict(item)
    poster_path = meta.get("poster_path")
    tmdb_poster_url = f"{TMDB_IMAGE_BASE}{poster_path}" if poster_path else ""

    omdb_data = omdb.get_full_details(item["imdb_id"]) if item["imdb_id"] else None

    markdown = build_movie_note(
        title=item["title"],
        year=item["year"],
        imdb_id=item["imdb_id"],
        tmdb_id=item["tmdb_id"],
        watched=bool(item["watched"]),
        tmdb_overview=meta.get("overview", ""),
        tmdb_genres=meta.get("genres") or [],
        tmdb_poster_url=tmdb_poster_url,
        tmdb_vote_average=meta.get("vote_average"),
        omdb=omdb_data,
    )
    display_title = (omdb_data.title if omdb_data else item["title"]) or item["title"]
    base_name = f"{display_title} ({item['year']})" if item["year"] else display_title
    filename = f"{sanitize_filename(base_name)}.md"
    return markdown, filename


def _content_disposition(filename: str) -> str:
    """HTTP headers are Latin-1 only -- a title with an en dash, curly
    quote, or any non-Latin1 character (all routine in a real TV/movie
    title) would otherwise raise inside Starlette's own header encoding
    and 500 the whole download. filename= carries an ASCII-safe fallback
    for older clients; filename*= (RFC 5987/6266) carries the real UTF-8
    name, which every current browser prefers when both are present."""
    ascii_fallback = filename.encode("ascii", "replace").decode("ascii")
    encoded = quote(filename, safe="")
    return f'attachment; filename="{ascii_fallback}"; filename*=UTF-8\'\'{encoded}'


def _note_download_response(markdown: str, filename: str) -> Response:
    """Shared by both download routes below -- the download shape (plain
    text/markdown body, RFC 5987 Content-Disposition) never differs
    between a movie note and a show note."""
    return Response(
        content=markdown,
        media_type="text/markdown; charset=utf-8",
        headers={"Content-Disposition": _content_disposition(filename)},
    )


def _write_note_to_folder(markdown: str, filename: str, folder: Path, *, what: str) -> NoteSaveResponse:
    """Shared by both save-to-folder routes below. `what` (e.g. "Movie",
    "Show") only shapes the 404 message -- which folder is passed in, and
    what "no longer exists" would mean for it, is entirely the caller's
    concern (a movie's own folder vs. two levels up from an episode)."""
    if not folder.is_dir():
        raise HTTPException(status_code=404, detail=f"{what} folder no longer exists: {folder}")
    dest = folder / filename
    try:
        dest.write_text(markdown, encoding="utf-8")
    except OSError as exc:
        raise HTTPException(status_code=500, detail=f"Failed to write note: {exc}") from exc
    return NoteSaveResponse(path=str(dest))


@router.get("/{item_id}/note")
def download_movie_note(
    item_id: int, db: Database = Depends(get_database), omdb: OMDbClient = Depends(get_omdb_client)
) -> Response:
    """Downloads the generated note without touching the archive folder --
    for saving it anywhere the user wants (e.g. an existing Obsidian vault
    outside this app's own media paths)."""
    try:
        markdown, filename = _generate_movie_note(item_id, db, omdb)
    except NoteError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc
    return _note_download_response(markdown, filename)


@router.post("/{item_id}/note/save", response_model=NoteSaveResponse)
def save_movie_note(
    item_id: int, db: Database = Depends(get_database), omdb: OMDbClient = Depends(get_omdb_client)
) -> NoteSaveResponse:
    """Writes the generated note directly into the movie's own archive
    folder, alongside the video file -- for a vault that watches the
    library's own folders rather than a separate notes directory."""
    try:
        markdown, filename = _generate_movie_note(item_id, db, omdb)
    except NoteError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc
    item = db.get_media_item(item_id)
    if not item["final_path"]:
        raise HTTPException(status_code=400, detail="This item has no archived file on disk")
    folder = Path(item["final_path"]).parent
    return _write_note_to_folder(markdown, filename, folder, what="Movie")


def _generate_tv_note(tmdb_id: int, db: Database, omdb: OMDbClient) -> tuple[str, str, list[dict]]:
    """Show-level counterpart of _generate_movie_note. Keyed by tmdb_id,
    not a single media_items row: a show is however many per-episode rows
    share that tmdb_id, so this aggregates across all of them (episode
    count, whether every owned episode is watched, the highest season with
    a watched episode) before building one note for the whole show.
    Returns (markdown_text, filename, episode_rows) -- the caller needs
    episode_rows too, to find the show's own folder for the save route.
    """
    episodes = [r for r in db.list_media_items(media_type="tv") if r["tmdb_id"] == tmdb_id]
    if not episodes:
        raise NoteError("No episodes found for this show")

    first = episodes[0]
    meta = _metadata_dict(first)
    poster_path = meta.get("poster_path")
    tmdb_poster_url = f"{TMDB_IMAGE_BASE}{poster_path}" if poster_path else ""

    watched = all(bool(r["watched"]) for r in episodes)
    watched_seasons = [r["season_number"] for r in episodes if r["watched"] and r["season_number"] is not None]
    last_watched_season = max(watched_seasons) if watched_seasons else None

    omdb_data = omdb.get_full_details(first["imdb_id"]) if first["imdb_id"] else None

    markdown = build_tv_note(
        title=first["title"],
        imdb_id=first["imdb_id"],
        tmdb_id=tmdb_id,
        watched=watched,
        episode_count=len(episodes),
        last_watched_season=last_watched_season,
        tmdb_overview=meta.get("overview", ""),
        tmdb_genres=meta.get("genres") or [],
        tmdb_poster_url=tmdb_poster_url,
        tmdb_vote_average=meta.get("vote_average"),
        omdb=omdb_data,
    )
    display_title = (omdb_data.title if omdb_data else first["title"]) or first["title"]
    year_field = omdb_data.year if omdb_data else None
    base_name = f"{display_title} ({year_field})" if year_field else display_title
    filename = f"{sanitize_filename(base_name)}.md"
    return markdown, filename, episodes


@router.get("/tv-shows/{tmdb_id}/note")
def download_tv_note(
    tmdb_id: int, db: Database = Depends(get_database), omdb: OMDbClient = Depends(get_omdb_client)
) -> Response:
    """TV counterpart of download_movie_note -- one note for the whole
    show, aggregated across every archived episode sharing this tmdb_id."""
    try:
        markdown, filename, _ = _generate_tv_note(tmdb_id, db, omdb)
    except NoteError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc
    return _note_download_response(markdown, filename)


@router.post("/tv-shows/{tmdb_id}/note/save", response_model=NoteSaveResponse)
def save_tv_note(
    tmdb_id: int, db: Database = Depends(get_database), omdb: OMDbClient = Depends(get_omdb_client)
) -> NoteSaveResponse:
    """Writes the show-level note into the show's own folder -- two levels
    up from any episode's file (Show/Season NN/episode.ext), not the
    season folder itself, so it sits alongside the show as a whole rather
    than inside whichever season happened to be picked."""
    try:
        markdown, filename, episodes = _generate_tv_note(tmdb_id, db, omdb)
    except NoteError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc
    episode_with_file = next((e for e in episodes if e["final_path"]), None)
    if episode_with_file is None:
        raise HTTPException(status_code=400, detail="This show has no archived episodes on disk")
    folder = Path(episode_with_file["final_path"]).parent.parent
    return _write_note_to_folder(markdown, filename, folder, what="Show")
