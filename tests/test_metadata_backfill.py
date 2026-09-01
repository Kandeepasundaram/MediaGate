from __future__ import annotations

import json
from unittest.mock import MagicMock

from app.core.metadata_backfill import match_one, refresh_vote_average_one
from app.core.tmdb_client import MediaResult


def test_match_one_returns_false_when_queue_empty(db):
    tmdb = MagicMock()
    assert match_one(db, tmdb) is False
    tmdb.search_movie.assert_not_called()


def test_match_one_updates_item_on_match(db):
    item_id = db.create_media_item(
        original_path="/x", final_path="/x", title="Some Movie", year=2020, media_type="movie"
    )
    tmdb = MagicMock()
    tmdb.search_movie.return_value = [
        MediaResult(tmdb_id=42, title="Some Movie", media_type="movie", year=2020,
                    poster_path="/p.jpg", overview="Plot.")
    ]

    found = match_one(db, tmdb)

    assert found is True
    item = db.get_media_item(item_id)
    assert item["tmdb_id"] == 42
    assert item["match_attempted_at"] is not None
    import json
    assert json.loads(item["metadata"])["poster_path"] == "/p.jpg"


def test_match_one_marks_attempted_without_match(db):
    item_id = db.create_media_item(
        original_path="/x", final_path="/x", title="Nonexistent Movie", media_type="movie"
    )
    tmdb = MagicMock()
    tmdb.search_movie.return_value = []

    found = match_one(db, tmdb)

    assert found is True
    item = db.get_media_item(item_id)
    assert item["tmdb_id"] is None
    assert item["match_attempted_at"] is not None


def test_match_one_uses_search_tv_for_tv_items(db):
    db.create_media_item(
        original_path="/x", final_path="/x", title="Show", media_type="tv", season_number=1, episode_number=1
    )
    tmdb = MagicMock()
    tmdb.search_tv.return_value = []

    match_one(db, tmdb)

    tmdb.search_tv.assert_called_once_with("Show")
    tmdb.search_movie.assert_not_called()


def test_match_one_respects_retry_cooldown(db):
    """Once an item has been attempted (no match), it shouldn't be retried
    immediately -- list_unmatched_media_items enforces a cooldown."""
    db.create_media_item(original_path="/x", final_path="/x", title="Nope", media_type="movie")
    tmdb = MagicMock()
    tmdb.search_movie.return_value = []

    assert match_one(db, tmdb) is True  # first attempt
    assert match_one(db, tmdb) is False  # cooldown active, nothing to do
    assert tmdb.search_movie.call_count == 1


def test_refresh_vote_average_one_returns_false_when_queue_empty(db):
    tmdb = MagicMock()
    assert refresh_vote_average_one(db, tmdb) is False
    tmdb.refresh_movie_details.assert_not_called()


def test_refresh_vote_average_one_fills_in_missing_rating(db):
    """A row matched before vote_average existed has tmdb_id set but no
    vote_average key in its metadata -- this is the legacy-library case."""
    item_id = db.create_media_item(
        original_path="/x", final_path="/x", title="Old Movie", media_type="movie",
        tmdb_id=42, metadata={"poster_path": "/old.jpg", "width": 1920, "height": 1080},
    )
    tmdb = MagicMock()
    tmdb.refresh_movie_details.return_value = MediaResult(
        tmdb_id=42, title="Old Movie", media_type="movie", year=2020,
        poster_path="/new.jpg", overview="Plot.", raw={"vote_average": 7.5},
    )

    found = refresh_vote_average_one(db, tmdb)

    assert found is True
    item = db.get_media_item(item_id)
    meta = json.loads(item["metadata"])
    assert meta["vote_average"] == 7.5
    assert meta["width"] == 1920 and meta["height"] == 1080  # ffprobe fields preserved
    assert item["match_attempted_at"] is not None


def test_refresh_vote_average_one_skips_rows_that_already_have_it(db):
    db.create_media_item(
        original_path="/x", final_path="/x", title="Rated Movie", media_type="movie",
        tmdb_id=7, metadata={"vote_average": 8.1},
    )
    tmdb = MagicMock()

    assert refresh_vote_average_one(db, tmdb) is False
    tmdb.refresh_movie_details.assert_not_called()


def test_refresh_vote_average_one_uses_tv_details_for_tv_items(db):
    db.create_media_item(
        original_path="/x", final_path="/x", title="Old Show", media_type="tv",
        tmdb_id=99, season_number=1, episode_number=1, metadata={},
    )
    tmdb = MagicMock()
    tmdb.refresh_tv_details.return_value = MediaResult(
        tmdb_id=99, title="Old Show", media_type="tv", raw={"vote_average": 6.0},
    )

    refresh_vote_average_one(db, tmdb)

    tmdb.refresh_tv_details.assert_called_once_with(99)
    tmdb.refresh_movie_details.assert_not_called()


def test_refresh_vote_average_one_respects_retry_cooldown_on_failed_refresh(db):
    db.create_media_item(
        original_path="/x", final_path="/x", title="Gone Movie", media_type="movie",
        tmdb_id=13, metadata={},
    )
    tmdb = MagicMock()
    tmdb.refresh_movie_details.return_value = None

    assert refresh_vote_average_one(db, tmdb) is True  # first attempt, marks match_attempted_at
    assert refresh_vote_average_one(db, tmdb) is False  # cooldown active
    assert tmdb.refresh_movie_details.call_count == 1
