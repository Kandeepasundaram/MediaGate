from __future__ import annotations

from unittest.mock import MagicMock, patch

from app.core.tmdb_client import MediaResult
from app.core.tracker import check_for_updates


def test_check_for_updates_flags_new_tv_season(db):
    db.upsert_tracker(tmdb_id=1, media_type="tv", title="Show", current_season_archived=1)

    tmdb = MagicMock()
    tmdb.get_tv_details.return_value = MediaResult(
        tmdb_id=1, title="Show", media_type="tv", raw={"number_of_seasons": 3}
    )

    pending = check_for_updates(db, tmdb)

    assert pending == 1
    row = db.get_tracker(1, "tv")
    assert row["latest_known_season"] == 3
    assert row["pending_notification"] == 1


def test_check_for_updates_no_change_when_season_current(db):
    db.upsert_tracker(tmdb_id=2, media_type="tv", title="Show2", current_season_archived=2)

    tmdb = MagicMock()
    tmdb.get_tv_details.return_value = MediaResult(
        tmdb_id=2, title="Show2", media_type="tv", raw={"number_of_seasons": 2}
    )

    pending = check_for_updates(db, tmdb)

    assert pending == 0
    row = db.get_tracker(2, "tv")
    assert row["pending_notification"] == 0


def test_check_for_updates_flags_movie_collection_addition(db):
    db.upsert_tracker(tmdb_id=10, media_type="movie", title="Movie", movie_release_status=None)

    tmdb = MagicMock()
    tmdb.get_movie_details.return_value = MediaResult(
        tmdb_id=10, title="Movie", media_type="movie", raw={"belongs_to_collection": {"id": 99}}
    )
    tmdb.get_collection_movies.return_value = [
        MediaResult(tmdb_id=10, title="Movie", media_type="movie"),
        MediaResult(tmdb_id=11, title="Movie 2", media_type="movie"),
    ]

    pending = check_for_updates(db, tmdb)

    assert pending == 1
    row = db.get_tracker(10, "movie")
    assert row["pending_notification"] == 1
    assert "1 related" in row["movie_release_status"]


def test_check_for_updates_fires_webhook_on_newly_pending(db):
    db.upsert_tracker(tmdb_id=1, media_type="tv", title="Show", current_season_archived=1)

    tmdb = MagicMock()
    tmdb.get_tv_details.return_value = MediaResult(
        tmdb_id=1, title="Show", media_type="tv", raw={"number_of_seasons": 3}
    )

    with patch("app.core.tracker.requests.post") as mock_post:
        check_for_updates(db, tmdb, webhook_url="https://example.com/hook")

    mock_post.assert_called_once()
    assert mock_post.call_args.args[0] == "https://example.com/hook"
    assert mock_post.call_args.kwargs["json"]["title"] == "Show"


def test_check_for_updates_does_not_refire_webhook_when_already_pending(db):
    db.upsert_tracker(tmdb_id=1, media_type="tv", title="Show", current_season_archived=1, pending_notification=1)

    tmdb = MagicMock()
    tmdb.get_tv_details.return_value = MediaResult(
        tmdb_id=1, title="Show", media_type="tv", raw={"number_of_seasons": 3}
    )

    with patch("app.core.tracker.requests.post") as mock_post:
        check_for_updates(db, tmdb, webhook_url="https://example.com/hook")

    mock_post.assert_not_called()


def test_check_for_updates_does_not_fire_webhook_for_muted_title(db):
    db.upsert_tracker(tmdb_id=1, media_type="tv", title="Show", current_season_archived=1)
    row = db.get_tracker(1, "tv")
    db.set_tracker_muted(row["id"], True)

    tmdb = MagicMock()
    tmdb.get_tv_details.return_value = MediaResult(
        tmdb_id=1, title="Show", media_type="tv", raw={"number_of_seasons": 3}
    )

    with patch("app.core.tracker.requests.post") as mock_post:
        check_for_updates(db, tmdb, webhook_url="https://example.com/hook")

    mock_post.assert_not_called()


def test_check_for_updates_handles_per_item_errors(db):
    db.upsert_tracker(tmdb_id=20, media_type="tv", title="Broken")

    tmdb = MagicMock()
    tmdb.get_tv_details.side_effect = RuntimeError("TMDB down")

    pending = check_for_updates(db, tmdb)

    assert pending == 0
    ops = db.list_operations(operation_type="tracker_check")
    assert ops[0]["status"] == "failed"
