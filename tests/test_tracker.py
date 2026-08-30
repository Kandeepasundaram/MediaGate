from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

from app.core.tmdb_client import MediaResult
from app.core.tracker import check_for_updates, maybe_auto_track


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


def test_maybe_auto_track_noop_when_disabled(db):
    maybe_auto_track(db, False, tmdb_id=1, media_type="movie", title="Movie")
    assert db.list_tracked() == []


def test_maybe_auto_track_noop_without_tmdb_id(db):
    maybe_auto_track(db, True, tmdb_id=None, media_type="movie", title="Movie")
    assert db.list_tracked() == []


def test_maybe_auto_track_creates_tracker_for_movie(db):
    maybe_auto_track(db, True, tmdb_id=42, media_type="movie", title="Movie")
    row = db.get_tracker(42, "movie")
    assert row is not None
    assert row["title"] == "Movie"


def test_maybe_auto_track_sets_current_season_for_tv(db):
    maybe_auto_track(db, True, tmdb_id=7, media_type="tv", title="Show", season=2)
    row = db.get_tracker(7, "tv")
    assert row["current_season_archived"] == 2


def test_maybe_auto_track_season_never_regresses(db):
    maybe_auto_track(db, True, tmdb_id=7, media_type="tv", title="Show", season=3)
    maybe_auto_track(db, True, tmdb_id=7, media_type="tv", title="Show", season=1)
    row = db.get_tracker(7, "tv")
    assert row["current_season_archived"] == 3


def test_check_for_updates_sends_single_digest_for_multiple_titles(db):
    db.upsert_tracker(tmdb_id=1, media_type="tv", title="Show A", current_season_archived=1)
    db.upsert_tracker(tmdb_id=2, media_type="tv", title="Show B", current_season_archived=1)

    tmdb = MagicMock()
    tmdb.get_tv_details.side_effect = lambda tmdb_id: MediaResult(
        tmdb_id=tmdb_id, title=f"Show {'A' if tmdb_id == 1 else 'B'}", media_type="tv",
        raw={"number_of_seasons": 3},
    )

    with patch("app.core.tracker.requests.post") as mock_post:
        check_for_updates(db, tmdb, webhook_url="https://example.com/hook")

    mock_post.assert_called_once()
    payload = mock_post.call_args.kwargs["json"]
    assert payload["count"] == 2
    assert set(payload["titles"]) == {"Show A", "Show B"}


def test_check_for_updates_skips_snoozed_title(db):
    db.upsert_tracker(tmdb_id=1, media_type="tv", title="Show", current_season_archived=1)
    row = db.get_tracker(1, "tv")
    future = (datetime.now(timezone.utc) + timedelta(days=1)).isoformat()
    db.update_tracker(row["id"], snoozed_until=future)

    tmdb = MagicMock()
    tmdb.get_tv_details.return_value = MediaResult(
        tmdb_id=1, title="Show", media_type="tv", raw={"number_of_seasons": 3}
    )

    pending = check_for_updates(db, tmdb)

    assert pending == 0
    tmdb.get_tv_details.assert_not_called()
    assert db.get_tracker(1, "tv")["pending_notification"] == 0


def test_check_for_updates_resumes_after_snooze_expires(db):
    db.upsert_tracker(tmdb_id=1, media_type="tv", title="Show", current_season_archived=1)
    row = db.get_tracker(1, "tv")
    past = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()
    db.update_tracker(row["id"], snoozed_until=past)

    tmdb = MagicMock()
    tmdb.get_tv_details.return_value = MediaResult(
        tmdb_id=1, title="Show", media_type="tv", raw={"number_of_seasons": 3}
    )

    pending = check_for_updates(db, tmdb)

    assert pending == 1
    tmdb.get_tv_details.assert_called_once()


def test_check_for_updates_respects_per_title_interval(db):
    now_iso = datetime.now(timezone.utc).isoformat()
    db.upsert_tracker(
        tmdb_id=1, media_type="tv", title="Show", current_season_archived=1,
        last_checked=now_iso, check_interval_hours=24.0,
    )

    tmdb = MagicMock()
    tmdb.get_tv_details.return_value = MediaResult(
        tmdb_id=1, title="Show", media_type="tv", raw={"number_of_seasons": 3}
    )

    check_for_updates(db, tmdb)

    tmdb.get_tv_details.assert_not_called()


def test_check_for_updates_runs_when_interval_elapsed(db):
    stale = (datetime.now(timezone.utc) - timedelta(hours=25)).isoformat()
    db.upsert_tracker(
        tmdb_id=1, media_type="tv", title="Show", current_season_archived=1,
        last_checked=stale, check_interval_hours=24.0,
    )

    tmdb = MagicMock()
    tmdb.get_tv_details.return_value = MediaResult(
        tmdb_id=1, title="Show", media_type="tv", raw={"number_of_seasons": 3}
    )

    check_for_updates(db, tmdb)

    tmdb.get_tv_details.assert_called_once()


def test_check_for_updates_handles_per_item_errors(db):
    db.upsert_tracker(tmdb_id=20, media_type="tv", title="Broken")

    tmdb = MagicMock()
    tmdb.get_tv_details.side_effect = RuntimeError("TMDB down")

    pending = check_for_updates(db, tmdb)

    assert pending == 0
    ops = db.list_operations(operation_type="tracker_check")
    assert ops[0]["status"] == "failed"
