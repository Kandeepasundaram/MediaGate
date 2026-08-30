from __future__ import annotations

from unittest.mock import MagicMock

from app.core.metadata_backfill import match_one
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
