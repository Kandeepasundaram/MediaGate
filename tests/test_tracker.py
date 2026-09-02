from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

from app.core.tmdb_client import MediaResult
from app.core.tracker import check_for_updates, check_tv_show, maybe_auto_track, send_digest
from app.core.tvmaze_client import TVmazeShowInfo


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


def test_check_for_updates_fires_discord_on_newly_pending(db):
    db.upsert_tracker(tmdb_id=1, media_type="tv", title="Show", current_season_archived=1)
    tmdb = MagicMock()
    tmdb.get_tv_details.return_value = MediaResult(tmdb_id=1, title="Show", media_type="tv", raw={"number_of_seasons": 3})

    with patch("app.core.tracker.requests.post") as mock_post:
        check_for_updates(db, tmdb, discord_webhook_url="https://discord.com/api/webhooks/x")

    mock_post.assert_called_once()
    assert mock_post.call_args.args[0] == "https://discord.com/api/webhooks/x"
    assert "Show" in mock_post.call_args.kwargs["json"]["content"]


def test_check_for_updates_fires_telegram_on_newly_pending(db):
    db.upsert_tracker(tmdb_id=1, media_type="tv", title="Show", current_season_archived=1)
    tmdb = MagicMock()
    tmdb.get_tv_details.return_value = MediaResult(tmdb_id=1, title="Show", media_type="tv", raw={"number_of_seasons": 3})

    with patch("app.core.tracker.requests.post") as mock_post:
        check_for_updates(db, tmdb, telegram_bot_token="bot-token", telegram_chat_id="12345")

    mock_post.assert_called_once()
    assert mock_post.call_args.args[0] == "https://api.telegram.org/botbot-token/sendMessage"
    assert mock_post.call_args.kwargs["json"]["chat_id"] == "12345"
    assert "Show" in mock_post.call_args.kwargs["json"]["text"]


def test_check_for_updates_skips_telegram_without_both_token_and_chat_id(db):
    db.upsert_tracker(tmdb_id=1, media_type="tv", title="Show", current_season_archived=1)
    tmdb = MagicMock()
    tmdb.get_tv_details.return_value = MediaResult(tmdb_id=1, title="Show", media_type="tv", raw={"number_of_seasons": 3})

    with patch("app.core.tracker.requests.post") as mock_post:
        check_for_updates(db, tmdb, telegram_bot_token="bot-token")  # no chat_id

    mock_post.assert_not_called()


def test_check_for_updates_fires_pushover_on_newly_pending(db):
    db.upsert_tracker(tmdb_id=1, media_type="tv", title="Show", current_season_archived=1)
    tmdb = MagicMock()
    tmdb.get_tv_details.return_value = MediaResult(tmdb_id=1, title="Show", media_type="tv", raw={"number_of_seasons": 3})

    with patch("app.core.tracker.requests.post") as mock_post:
        check_for_updates(db, tmdb, pushover_api_token="app-token", pushover_user_key="user-key")

    mock_post.assert_called_once()
    assert mock_post.call_args.args[0] == "https://api.pushover.net/1/messages.json"
    assert mock_post.call_args.kwargs["data"]["token"] == "app-token"
    assert mock_post.call_args.kwargs["data"]["user"] == "user-key"
    assert "Show" in mock_post.call_args.kwargs["data"]["message"]


def test_check_for_updates_fires_all_configured_channels_together(db):
    db.upsert_tracker(tmdb_id=1, media_type="tv", title="Show", current_season_archived=1)
    tmdb = MagicMock()
    tmdb.get_tv_details.return_value = MediaResult(tmdb_id=1, title="Show", media_type="tv", raw={"number_of_seasons": 3})

    with patch("app.core.tracker.requests.post") as mock_post:
        check_for_updates(
            db, tmdb,
            webhook_url="https://example.com/hook",
            discord_webhook_url="https://discord.com/api/webhooks/x",
            telegram_bot_token="bot-token", telegram_chat_id="12345",
            pushover_api_token="app-token", pushover_user_key="user-key",
        )

    assert mock_post.call_count == 4


def test_check_for_updates_digest_mode_suppresses_realtime_notification(db):
    db.upsert_tracker(tmdb_id=1, media_type="tv", title="Show", current_season_archived=1)
    tmdb = MagicMock()
    tmdb.get_tv_details.return_value = MediaResult(tmdb_id=1, title="Show", media_type="tv", raw={"number_of_seasons": 3})

    with patch("app.core.tracker.requests.post") as mock_post:
        pending = check_for_updates(db, tmdb, webhook_url="https://example.com/hook", digest_mode=True)

    mock_post.assert_not_called()
    assert pending == 1  # still flagged as pending -- just not pushed in real time
    row = db.get_tracker(1, "tv")
    assert row["pending_notification"] == 1


def test_send_digest_covers_all_pending_titles(db):
    db.upsert_tracker(tmdb_id=1, media_type="tv", title="Show A", current_season_archived=1, pending_notification=1)
    db.upsert_tracker(tmdb_id=2, media_type="movie", title="Movie B", pending_notification=1, movie_release_status="new release detected")

    with patch("app.core.tracker.requests.post") as mock_post:
        count = send_digest(db, webhook_url="https://example.com/hook")

    assert count == 2
    mock_post.assert_called_once()
    payload = mock_post.call_args.kwargs["json"]
    assert payload["count"] == 2
    assert set(payload["titles"]) == {"Show A", "Movie B"}


def test_send_digest_excludes_muted_titles(db):
    db.upsert_tracker(tmdb_id=1, media_type="tv", title="Show", current_season_archived=1, pending_notification=1)
    row = db.get_tracker(1, "tv")
    db.set_tracker_muted(row["id"], True)

    with patch("app.core.tracker.requests.post") as mock_post:
        count = send_digest(db, webhook_url="https://example.com/hook")

    assert count == 0
    mock_post.assert_not_called()


def test_send_digest_returns_zero_and_sends_nothing_when_no_pending(db):
    with patch("app.core.tracker.requests.post") as mock_post:
        assert send_digest(db, webhook_url="https://example.com/hook") == 0
    mock_post.assert_not_called()


def test_send_digest_fires_all_configured_channels(db):
    db.upsert_tracker(tmdb_id=1, media_type="tv", title="Show", current_season_archived=1, pending_notification=1)

    with patch("app.core.tracker.requests.post") as mock_post:
        send_digest(
            db,
            webhook_url="https://example.com/hook",
            discord_webhook_url="https://discord.com/api/webhooks/x",
            telegram_bot_token="bot-token", telegram_chat_id="12345",
            pushover_api_token="app-token", pushover_user_key="user-key",
        )

    assert mock_post.call_count == 4


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


def test_upsert_tracker_defaults_category_to_watching(db):
    db.upsert_tracker(tmdb_id=50, media_type="movie", title="Movie")
    assert db.get_tracker(50, "movie")["category"] == "watching"


def test_maybe_auto_track_moves_watched_title_back_to_watching_even_when_disabled(db):
    db.upsert_tracker(tmdb_id=51, media_type="movie", title="Movie", category="watched")
    maybe_auto_track(db, False, tmdb_id=51, media_type="movie", title="Movie")
    assert db.get_tracker(51, "movie")["category"] == "watching"


def test_maybe_auto_track_leaves_non_watched_category_alone(db):
    db.upsert_tracker(tmdb_id=52, media_type="movie", title="Movie", category="interested")
    maybe_auto_track(db, False, tmdb_id=52, media_type="movie", title="Movie")
    assert db.get_tracker(52, "movie")["category"] == "interested"


def test_check_for_updates_logs_notification_history(db):
    db.upsert_tracker(tmdb_id=1, media_type="tv", title="Show", current_season_archived=1)

    tmdb = MagicMock()
    tmdb.get_tv_details.return_value = MediaResult(
        tmdb_id=1, title="Show", media_type="tv", raw={"number_of_seasons": 3}
    )

    check_for_updates(db, tmdb)

    history = db.list_notification_history()
    assert len(history) == 1
    assert history[0]["title"] == "Show"


def test_check_for_updates_does_not_log_when_not_newly_pending(db):
    db.upsert_tracker(tmdb_id=2, media_type="tv", title="Show2", current_season_archived=2)

    tmdb = MagicMock()
    tmdb.get_tv_details.return_value = MediaResult(
        tmdb_id=2, title="Show2", media_type="tv", raw={"number_of_seasons": 2}
    )

    check_for_updates(db, tmdb)

    assert db.list_notification_history() == []


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


def test_check_tv_show_without_tvmaze_leaves_next_episode_air_date_unset(db):
    db.upsert_tracker(tmdb_id=30, media_type="tv", title="Show30", current_season_archived=1)
    row = db.get_tracker(30, "tv")

    tmdb = MagicMock()
    tmdb.get_tv_details.return_value = MediaResult(
        tmdb_id=30, title="Show30", media_type="tv", raw={"number_of_seasons": 1}
    )

    check_tv_show(db, tmdb, row)  # tvmaze omitted -- must not raise or call get_external_imdb_id

    tmdb.get_external_imdb_id.assert_not_called()
    assert db.get_tracker(30, "tv")["next_episode_air_date"] is None


def test_check_tv_show_enriches_next_episode_air_date_from_tvmaze(db):
    db.upsert_tracker(tmdb_id=31, media_type="tv", title="Show31", current_season_archived=1)
    row = db.get_tracker(31, "tv")

    tmdb = MagicMock()
    tmdb.get_tv_details.return_value = MediaResult(
        tmdb_id=31, title="Show31", media_type="tv", raw={"number_of_seasons": 1}
    )
    tmdb.get_external_imdb_id.return_value = "tt0000031"

    tvmaze = MagicMock()
    tvmaze.enabled = True
    tvmaze.get_show_info_by_imdb.return_value = TVmazeShowInfo(
        tvmaze_id=1, status="Running", network="AMC",
        next_episode_air_date="2026-09-10", next_episode_code="S02E01",
    )

    check_tv_show(db, tmdb, row, tvmaze=tvmaze)

    tvmaze.get_show_info_by_imdb.assert_called_once_with("tt0000031")
    assert db.get_tracker(31, "tv")["next_episode_air_date"] == "2026-09-10"


def test_check_tv_show_skips_tvmaze_lookup_when_disabled(db):
    db.upsert_tracker(tmdb_id=32, media_type="tv", title="Show32", current_season_archived=1)
    row = db.get_tracker(32, "tv")

    tmdb = MagicMock()
    tmdb.get_tv_details.return_value = MediaResult(
        tmdb_id=32, title="Show32", media_type="tv", raw={"number_of_seasons": 1}
    )
    tvmaze = MagicMock()
    tvmaze.enabled = False

    check_tv_show(db, tmdb, row, tvmaze=tvmaze)

    tmdb.get_external_imdb_id.assert_not_called()
    tvmaze.get_show_info_by_imdb.assert_not_called()


def test_message_for_appends_next_episode_air_date_when_present(db):
    db.upsert_tracker(tmdb_id=33, media_type="tv", title="Show33", current_season_archived=1)
    tmdb = MagicMock()
    tmdb.get_tv_details.return_value = MediaResult(
        tmdb_id=33, title="Show33", media_type="tv", raw={"number_of_seasons": 2}
    )
    tmdb.get_external_imdb_id.return_value = "tt0000033"
    tvmaze = MagicMock()
    tvmaze.enabled = True
    tvmaze.get_show_info_by_imdb.return_value = TVmazeShowInfo(
        tvmaze_id=1, status="Running", network=None,
        next_episode_air_date="2026-10-01", next_episode_code="S03E01",
    )

    with patch("app.core.tracker.requests.post"):
        check_for_updates(db, tmdb, webhook_url="https://example.com/hook", tvmaze=tvmaze)

    history = db.list_notification_history()
    assert "2026-10-01" in history[0]["message"]
