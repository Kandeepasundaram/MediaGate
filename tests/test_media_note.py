from __future__ import annotations

import yaml

from app.core.media_note import build_movie_note, build_tv_note
from app.core.omdb_client import OMDbFullResult


def _parse_frontmatter(note_text: str) -> dict:
    assert note_text.startswith("---\n")
    _, frontmatter, _ = note_text.split("---\n", 2)
    return yaml.safe_load(frontmatter)


def _omdb_result(**overrides) -> OMDbFullResult:
    defaults = dict(
        title="Aquaman and the Lost Kingdom",
        year="2023",
        imdb_id="tt9663764",
        plot="Black Manta seeks revenge on Aquaman for his father's death.",
        genres=["Action", "Adventure", "Fantasy"],
        director=["James Wan"],
        writer=["David Leslie Johnson-McGoldrick (screenplay by)", "James Wan", "Jason Momoa"],
        actors=["Jason Momoa", "Patrick Wilson", "Yahya Abdul-Mateen II"],
        runtime="124 min",
        imdb_rating=5.9,
        poster_url="https://example.com/poster.jpg",
        released="22 Dec 2023",
    )
    defaults.update(overrides)
    return OMDbFullResult(**defaults)


def test_build_movie_note_with_omdb_data_produces_valid_yaml_frontmatter():
    note = build_movie_note(
        title="Aquaman and the Lost Kingdom",
        year=2023,
        imdb_id="tt9663764",
        tmdb_id=572802,
        watched=True,
        omdb=_omdb_result(),
    )
    fm = _parse_frontmatter(note)

    assert fm["type"] == "movie"
    assert fm["title"] == "Aquaman and the Lost Kingdom"
    assert fm["englishTitle"] == "Aquaman and the Lost Kingdom"
    assert fm["year"] == "2023"
    assert fm["dataSource"] == "OMDbAPI"
    assert fm["url"] == "https://www.imdb.com/title/tt9663764/"
    assert fm["id"] == "tt9663764"
    assert fm["genres"] == ["Action", "Adventure", "Fantasy"]
    assert fm["director"] == ["James Wan"]
    # writer annotation ("(screenplay by)") is stripped
    assert fm["writer"] == ["David Leslie Johnson-McGoldrick", "James Wan", "Jason Momoa"]
    assert fm["studio"] == ["N/A"]
    assert fm["duration"] == "124 min"
    assert fm["onlineRating"] == 6  # 5.9 rounded
    assert fm["actors"] == ["Jason Momoa", "Patrick Wilson", "Yahya Abdul-Mateen II"]
    assert fm["image"] == "https://example.com/poster.jpg"
    assert fm["released"] is True
    assert fm["premiere"] == "12/22/2023"
    assert fm["watched"] is True
    assert fm["lastWatched"] == ""
    assert fm["tags"] == ["mediaDB/tv/movie"]
    assert note.rstrip().endswith("![poster|520](https://example.com/poster.jpg)")


def test_build_movie_note_falls_back_to_tmdb_when_no_omdb_data():
    note = build_movie_note(
        title="Some Movie",
        year=2020,
        imdb_id=None,
        tmdb_id=42,
        watched=False,
        tmdb_overview="A TMDB-sourced plot.",
        tmdb_genres=["Drama"],
        tmdb_poster_url="https://image.tmdb.org/t/p/original/poster.jpg",
        tmdb_vote_average=7.4,
        omdb=None,
    )
    fm = _parse_frontmatter(note)

    assert fm["dataSource"] == "TMDB"
    assert fm["plot"] == "A TMDB-sourced plot."
    assert fm["genres"] == ["Drama"]
    assert fm["director"] == ["N/A"]
    assert fm["writer"] == ["N/A"]
    assert fm["actors"] == ["N/A"]
    assert fm["duration"] == "N/A"
    assert fm["onlineRating"] == 7  # 7.4 rounded
    assert fm["url"] == "https://www.themoviedb.org/movie/42"
    assert fm["id"] == "tmdb-42"
    assert fm["watched"] is False


def test_build_movie_note_handles_missing_rating_and_year():
    note = build_movie_note(
        title="Mystery Movie", year=None, imdb_id=None, tmdb_id=None, watched=False, omdb=None,
    )
    fm = _parse_frontmatter(note)
    assert fm["year"] == ""
    assert fm["onlineRating"] is None  # blank scalar parses as None
    assert fm["url"] is None
    assert fm["id"] is None


def test_build_movie_note_escapes_colon_in_title_and_plot():
    note = build_movie_note(
        title="Movie: Subtitle",
        year=2021,
        imdb_id="tt1",
        tmdb_id=1,
        watched=False,
        omdb=_omdb_result(title="Movie: Subtitle", plot="A plot with a colon: right here."),
    )
    fm = _parse_frontmatter(note)
    assert fm["title"] == "Movie: Subtitle"
    assert fm["plot"] == "A plot with a colon: right here."


def test_build_movie_note_escapes_double_quotes_in_plot():
    note = build_movie_note(
        title="Movie",
        year=2021,
        imdb_id="tt1",
        tmdb_id=1,
        watched=False,
        omdb=_omdb_result(plot='He said "hello" to everyone.'),
    )
    fm = _parse_frontmatter(note)
    assert fm["plot"] == 'He said "hello" to everyone.'


def test_build_movie_note_reformats_release_date():
    note = build_movie_note(
        title="Movie", year=2021, imdb_id="tt1", tmdb_id=1, watched=False,
        omdb=_omdb_result(released="01 Jan 2021"),
    )
    fm = _parse_frontmatter(note)
    assert fm["premiere"] == "01/01/2021"


def test_build_movie_note_blank_premiere_when_release_date_unknown():
    note = build_movie_note(
        title="Movie", year=2021, imdb_id="tt1", tmdb_id=1, watched=False,
        omdb=_omdb_result(released="N/A"),
    )
    fm = _parse_frontmatter(note)
    assert fm["premiere"] is None  # blank scalar


def _tv_omdb_result(**overrides) -> OMDbFullResult:
    defaults = dict(
        title="9-1-1",
        year="2018–",
        imdb_id="tt7235466",
        plot="Explores the high-pressure experiences of first responders.",
        genres=["Action", "Drama", "Thriller"],
        director=["N/A"],
        writer=["Brad Falchuk", "Tim Minear", "Ryan Murphy"],
        actors=["Angela Bassett", "Peter Krause", "Oliver Stark"],
        runtime="43 min",
        imdb_rating=7.9,
        poster_url="https://example.com/poster.jpg",
        released="03 Jan 2018",
    )
    defaults.update(overrides)
    return OMDbFullResult(**defaults)


def test_build_tv_note_with_omdb_data_matches_reference_shape():
    note = build_tv_note(
        title="9-1-1", imdb_id="tt7235466", tmdb_id=75219, watched=False,
        episode_count=120, last_watched_season=6, omdb=_tv_omdb_result(),
    )
    fm = _parse_frontmatter(note)

    assert fm["type"] == "series"
    assert fm["title"] == "9-1-1"
    assert fm["year"] == "2018–"  # raw OMDb year, unquoted en-dash string
    assert fm["dataSource"] == "OMDbAPI"
    assert fm["url"] == "https://www.imdb.com/title/tt7235466/"
    assert fm["id"] == "tt7235466"
    assert fm["genres"] == ["Action", "Drama", "Thriller"]
    assert "director" not in fm  # series notes never have a director key
    assert fm["writer"] == ["Brad Falchuk", "Tim Minear", "Ryan Murphy"]
    assert fm["studio"] is None  # blank, not a ["N/A"] list like the movie note
    assert fm["episodes"] == 120
    assert fm["duration"] == "43 min"
    assert fm["onlineRating"] == 7.9  # kept at OMDb's own precision, not rounded
    assert fm["actors"] == ["Angela Bassett", "Peter Krause", "Oliver Stark"]
    assert fm["released"] is True
    assert fm["airing"] is True
    assert fm["airedFrom"] == "01/03/2018"
    assert fm["airedTo"] == "unknown"
    assert fm["watched"] is False
    assert fm["lastWatched"] == "S6"
    assert fm["personalRating"] == 0
    assert fm["tags"] == ["mediaDB/tv/series"]
    assert note.rstrip().endswith("![poster|520](https://example.com/poster.jpg)")


def test_build_tv_note_ended_show_reports_airing_false():
    note = build_tv_note(
        title="Show", imdb_id="tt1", tmdb_id=1, watched=False,
        episode_count=50, last_watched_season=None, omdb=_tv_omdb_result(year="2015–2020"),
    )
    fm = _parse_frontmatter(note)
    assert fm["airing"] is False
    assert fm["airedTo"] == "unknown"  # OMDb never gives an exact end date either way


def test_build_tv_note_blank_last_watched_when_nothing_watched():
    note = build_tv_note(
        title="Show", imdb_id="tt1", tmdb_id=1, watched=False,
        episode_count=10, last_watched_season=None, omdb=_tv_omdb_result(),
    )
    fm = _parse_frontmatter(note)
    assert fm["lastWatched"] is None  # blank scalar


def test_build_tv_note_falls_back_to_tmdb_when_no_omdb_data():
    note = build_tv_note(
        title="Some Show", imdb_id=None, tmdb_id=99, watched=True,
        episode_count=24, last_watched_season=2,
        tmdb_overview="A TMDB-sourced plot.", tmdb_genres=["Comedy"],
        tmdb_poster_url="https://image.tmdb.org/t/p/original/poster.jpg",
        tmdb_vote_average=8.2, omdb=None,
    )
    fm = _parse_frontmatter(note)

    assert fm["dataSource"] == "TMDB"
    assert fm["plot"] == "A TMDB-sourced plot."
    assert fm["genres"] == ["Comedy"]
    assert "director" not in fm
    assert fm["writer"] == ["N/A"]
    assert fm["actors"] == ["N/A"]
    assert fm["duration"] == "N/A"
    assert fm["onlineRating"] == 8.2
    assert fm["url"] == "https://www.themoviedb.org/tv/99"
    assert fm["id"] == "tmdb-99"
    assert fm["watched"] is True
    assert fm["lastWatched"] == "S2"
    # no OMDb year data -- best-effort default is "still airing, unknown end"
    assert fm["airing"] is True
    assert fm["airedTo"] == "unknown"
