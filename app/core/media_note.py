"""Builds an Obsidian "Media DB" plugin-style markdown note for an archived
movie -- YAML frontmatter (title, plot, cast/crew, ratings, poster, ...)
plus a poster embed, matching the shape that plugin writes for a movie
entry. OMDb (when configured) is the primary data source, since it's the
only one of this app's two TMDB/OMDb integrations that actually returns
director/writer/actors/runtime; TMDB-only metadata is used as a fallback
with those specific fields left as "N/A", the same placeholder OMDb itself
uses for a field it doesn't have.
"""
from __future__ import annotations

import re
from datetime import datetime

from app.core.omdb_client import OMDbFullResult

_YAML_UNSAFE = re.compile(r'[:#\[\]{}&*!|>\'"%@`]|^[-?]|^\s|\s$')


def _yaml_str(value: str | None) -> str:
    """Bare when safe, double-quoted-and-escaped otherwise -- movie titles
    and plots routinely contain colons ("Title: Subtitle") which would
    otherwise silently corrupt the YAML structure if left unquoted."""
    if not value:
        return ""
    if _YAML_UNSAFE.search(value):
        escaped = value.replace("\\", "\\\\").replace('"', '\\"')
        return f'"{escaped}"'
    return value


def _yaml_list(items: list[str]) -> str:
    if not items:
        return ""
    return "\n" + "\n".join(f"  - {_yaml_str(i)}" for i in items)


def _reformat_release_date(raw: str) -> str:
    """OMDb's "22 Dec 2023" -> "12/22/2023". Falls back to the raw string
    (or "") on anything that doesn't parse -- a note with a slightly odd
    premiere field is far better than one that fails to generate."""
    if not raw or raw == "N/A":
        return ""
    try:
        return datetime.strptime(raw, "%d %b %Y").strftime("%m/%d/%Y")
    except ValueError:
        return raw


def _strip_writer_annotation(name: str) -> str:
    """OMDb's Writer field often trails a role annotation, e.g.
    "David Leslie Johnson-McGoldrick (screenplay by)" -- stripped so the
    note lists plain names, matching how the reference note looks."""
    return re.sub(r"\s*\([^)]*\)\s*$", "", name).strip()


def _tv_airing_status(year_field: str) -> tuple[bool, str]:
    """Best-effort from OMDb's own Year field for a series -- "2018–" (an
    en dash with nothing after it) means still airing; "2018–2023" means
    ended. OMDb doesn't give an exact end-air date either way (no field
    for it), so airedTo is always "unknown" rather than guessing one from
    just the year, matching the reference note's own convention."""
    if not year_field:
        return True, "unknown"
    if re.search(r"[–-]\d{4}", year_field):
        return False, "unknown"
    return True, "unknown"


def build_movie_note(
    *,
    title: str,
    year: int | None,
    imdb_id: str | None,
    tmdb_id: int | None,
    watched: bool,
    tmdb_overview: str = "",
    tmdb_genres: list[str] | None = None,
    tmdb_poster_url: str = "",
    tmdb_vote_average: float | None = None,
    omdb: OMDbFullResult | None = None,
) -> str:
    """Builds the note's full text (frontmatter + poster embed). Every
    field the reference note has is always present in the output, even
    when this app has no data for it -- "N/A" (OMDb's own convention) for
    list fields, blank for free-text ones -- so the note's shape never
    depends on which data source happened to be available.
    """
    year_str = str(year) if year else ""

    if omdb is not None:
        data_source = "OMDbAPI"
        plot = omdb.plot
        genres = omdb.genres
        director = omdb.director
        writer = [_strip_writer_annotation(w) for w in omdb.writer]
        actors = omdb.actors
        duration = omdb.runtime
        rating = round(omdb.imdb_rating) if omdb.imdb_rating is not None else ""
        poster = omdb.poster_url or tmdb_poster_url
        premiere = _reformat_release_date(omdb.released)
        note_title = omdb.title or title
    else:
        data_source = "TMDB"
        plot = tmdb_overview
        genres = tmdb_genres or ["N/A"]
        director = ["N/A"]
        writer = ["N/A"]
        actors = ["N/A"]
        duration = "N/A"
        rating = round(tmdb_vote_average) if tmdb_vote_average is not None else ""
        poster = tmdb_poster_url
        premiere = ""
        note_title = title

    if imdb_id:
        url = f"https://www.imdb.com/title/{imdb_id}/"
        note_id = imdb_id
    elif tmdb_id is not None:
        url = f"https://www.themoviedb.org/movie/{tmdb_id}"
        note_id = f"tmdb-{tmdb_id}"
    else:
        url, note_id = "", ""

    lines = [
        "---",
        "type: movie",
        "subType: ",
        f"title: {_yaml_str(note_title)}",
        f"englishTitle: {_yaml_str(note_title)}",
        f'year: "{year_str}"',
        f"dataSource: {data_source}",
        f"url: {url}",
        f"id: {note_id}",
        f"plot: {_yaml_str(plot)}",
        f"genres:{_yaml_list(genres)}",
        f"director:{_yaml_list(director)}",
        f"writer:{_yaml_list(writer)}",
        "studio:\n  - N/A",
        f"duration: {duration}",
        f"onlineRating: {rating}",
        f"actors:{_yaml_list(actors)}",
        f"image: {poster}",
        "released: true",
        "streamingServices: ",
        f"premiere: {premiere}",
        f"watched: {'true' if watched else 'false'}",
        'lastWatched: ""',
        "personalRating: ",
        "tags:\n  - mediaDB/tv/movie",
        "---",
        f"![poster|520]({poster})",
        "",
    ]
    return "\n".join(lines)


def build_tv_note(
    *,
    title: str,
    imdb_id: str | None,
    tmdb_id: int | None,
    watched: bool,
    episode_count: int,
    last_watched_season: int | None,
    tmdb_overview: str = "",
    tmdb_genres: list[str] | None = None,
    tmdb_poster_url: str = "",
    tmdb_vote_average: float | None = None,
    omdb: OMDbFullResult | None = None,
) -> str:
    """Show-level counterpart of build_movie_note -- one note per show, not
    per episode, aggregated by the caller across that show's media_items
    rows (episode_count, last_watched_season, the show-wide `watched`
    flag). Two shape differences from the movie note, both matching the
    reference series note exactly: no `director` key at all (OMDb doesn't
    return one for a series), and the rating/year fields aren't
    reformatted the way the movie note's are (year kept as OMDb's raw
    "2018–" string, rating kept at OMDb's own precision instead of
    rounded) -- these evidently aren't post-processed by the plugin for a
    series the way they are for a movie.
    """
    if omdb is not None:
        data_source = "OMDbAPI"
        plot = omdb.plot
        genres = omdb.genres
        writer = [_strip_writer_annotation(w) for w in omdb.writer]
        actors = omdb.actors
        duration = omdb.runtime
        rating = omdb.imdb_rating if omdb.imdb_rating is not None else ""
        poster = omdb.poster_url or tmdb_poster_url
        year_field = omdb.year or ""
        premiere = _reformat_release_date(omdb.released)
        note_title = omdb.title or title
    else:
        data_source = "TMDB"
        plot = tmdb_overview
        genres = tmdb_genres or ["N/A"]
        writer = ["N/A"]
        actors = ["N/A"]
        duration = "N/A"
        rating = tmdb_vote_average if tmdb_vote_average is not None else ""
        poster = tmdb_poster_url
        year_field = ""
        premiere = ""
        note_title = title

    airing, aired_to = _tv_airing_status(year_field)

    if imdb_id:
        url = f"https://www.imdb.com/title/{imdb_id}/"
        note_id = imdb_id
    elif tmdb_id is not None:
        url = f"https://www.themoviedb.org/tv/{tmdb_id}"
        note_id = f"tmdb-{tmdb_id}"
    else:
        url, note_id = "", ""

    last_watched = f"S{last_watched_season}" if last_watched_season else ""

    lines = [
        "---",
        "type: series",
        "subType: ",
        f"title: {_yaml_str(note_title)}",
        f"englishTitle: {_yaml_str(note_title)}",
        f"year: {_yaml_str(year_field)}",
        f"dataSource: {data_source}",
        f"url: {url}",
        f"id: {note_id}",
        f"plot: {_yaml_str(plot)}",
        f"genres:{_yaml_list(genres)}",
        f"writer:{_yaml_list(writer)}",
        "studio: ",
        f"episodes: {episode_count}",
        f"duration: {duration}",
        f"onlineRating: {rating}",
        f"actors:{_yaml_list(actors)}",
        f"image: {poster}",
        "released: true",
        "streamingServices: ",
        f"airing: {'true' if airing else 'false'}",
        f"airedFrom: {premiere}",
        f"airedTo: {aired_to}",
        f"watched: {'true' if watched else 'false'}",
        f"lastWatched: {last_watched}",
        "personalRating: 0",
        "tags:\n  - mediaDB/tv/series",
        "---",
        f"![poster|520]({poster})",
        "",
    ]
    return "\n".join(lines)
