from __future__ import annotations

import json


def test_init_db_creates_tables(db):
    tables = {r["name"] for r in db.fetch_all("SELECT name FROM sqlite_master WHERE type='table'")}
    assert {"media_items", "archive_tracker", "operation_log", "schema_meta", "api_tokens"} <= tables


def test_create_and_list_api_tokens(db):
    db.create_api_token("phone", "secret-abc")
    db.create_api_token("laptop", "secret-def")

    tokens = db.list_api_tokens()
    assert {t["name"] for t in tokens} == {"phone", "laptop"}
    assert all(t["last_used_at"] is None for t in tokens)


def test_get_api_token_by_value(db):
    db.create_api_token("phone", "secret-abc")

    row = db.get_api_token_by_value("secret-abc")
    assert row["name"] == "phone"
    assert db.get_api_token_by_value("wrong-value") is None


def test_touch_api_token_sets_last_used_at(db):
    token_id = db.create_api_token("phone", "secret-abc")
    db.touch_api_token(token_id)

    row = db.get_api_token_by_value("secret-abc")
    assert row["last_used_at"] is not None


def test_delete_api_token_removes_it(db):
    token_id = db.create_api_token("phone", "secret-abc")
    db.delete_api_token(token_id)

    assert db.list_api_tokens() == []
    assert db.get_api_token_by_value("secret-abc") is None


def test_init_db_creates_indexes(db):
    indexes = {r["name"] for r in db.fetch_all("SELECT name FROM sqlite_master WHERE type='index'")}
    assert {
        "idx_media_items_tmdb_id",
        "idx_media_items_final_path",
        "idx_media_items_media_type",
        "idx_operation_log_created_at",
    } <= indexes


def test_maintenance_checkpoint_and_vacuum_runs_without_error(db):
    db.create_media_item(original_path="x", title="T", media_type="movie")
    db.maintenance_checkpoint_and_vacuum()
    assert db.get_media_item(1)["title"] == "T"


def test_create_and_get_media_item(db):
    item_id = db.create_media_item(
        original_path="/incoming/movie.mkv",
        title="Test Movie",
        year=2020,
        media_type="movie",
    )
    item = db.get_media_item(item_id)
    assert item["title"] == "Test Movie"
    assert item["year"] == 2020
    assert item["watched"] == 0


def test_update_media_item(db):
    item_id = db.create_media_item(original_path="x", title="T", media_type="movie")
    db.update_media_item(item_id, watched=1, final_path="/archive/T (2020)/T (2020).mkv")
    item = db.get_media_item(item_id)
    assert item["watched"] == 1
    assert item["final_path"].endswith(".mkv")


def test_list_media_items_filters_by_type(db):
    db.create_media_item(original_path="a", title="A", media_type="movie")
    db.create_media_item(original_path="b", title="B", media_type="tv")
    movies = db.list_media_items(media_type="movie")
    assert len(movies) == 1
    assert movies[0]["title"] == "A"


def test_upsert_tracker_insert_then_update(db):
    db.upsert_tracker(tmdb_id=1, media_type="tv", title="Show", current_season_archived=1)
    row = db.get_tracker(1, "tv")
    assert row["current_season_archived"] == 1

    db.upsert_tracker(tmdb_id=1, media_type="tv", title="Show", latest_known_season=2, pending_notification=1)
    row = db.get_tracker(1, "tv")
    assert row["latest_known_season"] == 2
    assert row["pending_notification"] == 1


def test_acknowledge_notification_clears_flag(db):
    db.upsert_tracker(tmdb_id=5, media_type="movie", title="M", pending_notification=1)
    row = db.get_tracker(5, "movie")
    db.acknowledge_notification(row["id"])
    row = db.get_tracker(5, "movie")
    assert row["pending_notification"] == 0
    assert row["notification_sent_at"] is not None


def test_list_known_paths_includes_original_and_final(db, tmp_path):
    original = tmp_path / "movie.mkv"
    final = tmp_path / "Movie (2020)" / "Movie (2020).mkv"
    original.write_bytes(b"1")
    final.parent.mkdir()
    final.write_bytes(b"2")

    db.create_media_item(
        original_path=str(original),
        final_path=str(final),
        title="Movie",
        media_type="movie",
    )

    known = db.list_known_paths()
    assert str(original.resolve()) in known
    assert str(final.resolve()) in known


def test_list_known_paths_empty_when_no_items(db):
    assert db.list_known_paths() == set()


def test_operation_log(db):
    item_id = db.create_media_item(original_path="a", title="A", media_type="movie")
    db.log_operation("archive", "success", media_id=item_id, details={"a": 1})
    ops = db.list_operations(operation_type="archive")
    assert len(ops) == 1
    assert ops[0]["status"] == "success"


def test_list_operations_filters_by_status(db):
    db.log_operation("archive", "success", details={"a": 1})
    db.log_operation("archive", "failed", error_message="boom")

    assert len(db.list_operations(status="success")) == 1
    assert len(db.list_operations(status="failed")) == 1
    assert db.list_operations(status="failed")[0]["error_message"] == "boom"


def test_list_operations_filters_by_date_range(db):
    db.execute_query(
        "INSERT INTO operation_log (operation_type, status, created_at) VALUES (?, ?, ?)",
        ("archive", "success", "2026-01-05T00:00:00+00:00"),
    )
    db.execute_query(
        "INSERT INTO operation_log (operation_type, status, created_at) VALUES (?, ?, ?)",
        ("archive", "success", "2026-02-10T00:00:00+00:00"),
    )

    assert len(db.list_operations(since="2026-01-01", until="2026-01-31")) == 1
    assert len(db.list_operations(since="2026-02-01")) == 1
    assert len(db.list_operations(until="2026-01-31")) == 1
    assert len(db.list_operations()) == 2


def test_list_operations_combines_filters(db):
    db.execute_query(
        "INSERT INTO operation_log (operation_type, status, created_at) VALUES (?, ?, ?)",
        ("archive", "failed", "2026-01-05T00:00:00+00:00"),
    )
    db.execute_query(
        "INSERT INTO operation_log (operation_type, status, created_at) VALUES (?, ?, ?)",
        ("archive", "success", "2026-01-05T00:00:00+00:00"),
    )
    db.execute_query(
        "INSERT INTO operation_log (operation_type, status, created_at) VALUES (?, ?, ?)",
        ("purge", "failed", "2026-01-05T00:00:00+00:00"),
    )

    assert len(db.list_operations(operation_type="archive", status="failed")) == 1


def test_list_unmatched_media_items_excludes_matched(db):
    db.create_media_item(original_path="a", title="A", media_type="movie", tmdb_id=1)
    unmatched_id = db.create_media_item(original_path="b", title="B", media_type="movie")

    unmatched = db.list_unmatched_media_items(limit=10)
    assert [r["id"] for r in unmatched] == [unmatched_id]


def test_list_unmatched_media_items_prioritizes_never_attempted(db):
    attempted_id = db.create_media_item(original_path="a", title="A", media_type="movie")
    db.update_media_item(attempted_id, match_attempted_at="2026-01-01T00:00:00+00:00")
    never_attempted_id = db.create_media_item(original_path="b", title="B", media_type="movie")

    unmatched = db.list_unmatched_media_items(limit=10)
    assert unmatched[0]["id"] == never_attempted_id
    assert unmatched[1]["id"] == attempted_id


def test_list_unmatched_media_items_respects_cooldown(db):
    from datetime import datetime, timezone

    item_id = db.create_media_item(original_path="a", title="A", media_type="movie")
    db.update_media_item(item_id, match_attempted_at=datetime.now(timezone.utc).isoformat())

    assert db.list_unmatched_media_items(retry_cooldown_hours=6.0, limit=10) == []
    assert len(db.list_unmatched_media_items(retry_cooldown_hours=0.0, limit=10)) == 1


def test_list_unmatched_media_items_excludes_manual_override(db):
    overridden_id = db.create_media_item(original_path="a", title="A", media_type="movie", manual_override=1)
    unmatched_id = db.create_media_item(original_path="b", title="B", media_type="movie")

    unmatched = db.list_unmatched_media_items(limit=10)
    assert [r["id"] for r in unmatched] == [unmatched_id]
    assert overridden_id not in [r["id"] for r in unmatched]


def test_count_unmatched_media_items_filters_by_type(db):
    db.create_media_item(original_path="a", title="A", media_type="movie")
    db.create_media_item(original_path="b", title="B", media_type="tv")

    assert db.count_unmatched_media_items() == 2
    assert db.count_unmatched_media_items("movie") == 1
    assert db.count_unmatched_media_items("tv") == 1


def test_set_tracker_muted_excludes_from_pending_notifications(db):
    db.upsert_tracker(tmdb_id=7, media_type="tv", title="Show", pending_notification=1)
    row = db.get_tracker(7, "tv")
    assert len(db.list_pending_notifications()) == 1

    db.set_tracker_muted(row["id"], True)
    assert db.list_pending_notifications() == []
    assert db.get_tracker_by_id(row["id"])["muted"] == 1

    db.set_tracker_muted(row["id"], False)
    assert len(db.list_pending_notifications()) == 1


def test_reset_failed_match_attempts_clears_only_failed_items(db):
    never_attempted = db.create_media_item(original_path="a", title="A", media_type="movie")
    failed = db.create_media_item(original_path="b", title="B", media_type="movie")
    db.update_media_item(failed, match_attempted_at="2026-01-01T00:00:00+00:00")
    matched = db.create_media_item(original_path="c", title="C", media_type="movie", tmdb_id=1)
    overridden = db.create_media_item(original_path="d", title="D", media_type="movie", manual_override=1)
    db.update_media_item(overridden, match_attempted_at="2026-01-01T00:00:00+00:00")

    reset_count = db.reset_failed_match_attempts()

    assert reset_count == 1
    assert db.get_media_item(never_attempted)["match_attempted_at"] is None
    assert db.get_media_item(failed)["match_attempted_at"] is None
    assert db.get_media_item(matched)["tmdb_id"] == 1
    assert db.get_media_item(overridden)["match_attempted_at"] is not None


def test_reset_failed_match_attempts_filters_by_media_type(db):
    movie_failed = db.create_media_item(original_path="a", title="A", media_type="movie")
    db.update_media_item(movie_failed, match_attempted_at="2026-01-01T00:00:00+00:00")
    tv_failed = db.create_media_item(original_path="b", title="B", media_type="tv")
    db.update_media_item(tv_failed, match_attempted_at="2026-01-01T00:00:00+00:00")

    reset_count = db.reset_failed_match_attempts("movie")

    assert reset_count == 1
    assert db.get_media_item(movie_failed)["match_attempted_at"] is None
    assert db.get_media_item(tv_failed)["match_attempted_at"] is not None


def test_log_notification_and_list_history(db):
    db.upsert_tracker(tmdb_id=1, media_type="tv", title="Show")
    tracker_row = db.get_tracker(1, "tv")

    db.log_notification(tracker_row["id"], 1, "tv", "Show", "New season available")
    db.log_notification(None, None, "movie", "Movie", "Sequel found")

    history = db.list_notification_history()
    assert len(history) == 2
    assert history[0]["message"] == "Sequel found"  # most recent first


def test_get_operation_returns_row_by_id(db):
    op_id = db.log_operation("archive", "success", details={"a": 1})
    assert db.get_operation(op_id)["operation_type"] == "archive"
    assert db.get_operation(999999) is None


def test_count_failed_match_items_requires_attempt(db):
    db.create_media_item(original_path="a", title="A", media_type="movie")  # never attempted
    failed_id = db.create_media_item(original_path="b", title="B", media_type="movie")
    db.update_media_item(failed_id, match_attempted_at="2026-01-01T00:00:00+00:00")

    assert db.count_failed_match_items() == 1
    assert db.count_failed_match_items("movie") == 1
    assert db.count_failed_match_items("tv") == 0


def test_log_operation_accepts_delete_type(db):
    db.log_operation("delete", "success", details={"path": "/some/file.mkv"})
    ops = db.list_operations(operation_type="delete")
    assert len(ops) == 1


def test_get_media_item_by_final_path(db):
    item_id = db.create_media_item(
        original_path="a", final_path="/archive/Movie (2020)/Movie (2020).mkv", title="Movie", media_type="movie"
    )
    found = db.get_media_item_by_final_path("/archive/Movie (2020)/Movie (2020).mkv")
    assert found["id"] == item_id
    assert db.get_media_item_by_final_path("/nope") is None


def test_migrations_upgrade_v1_database_to_current(tmp_path):
    from app.database import Database, _now

    db_path = tmp_path / "old.db"
    db = Database(db_path)

    # Simulate a pre-migration (v1) database: operation_log's CHECK constraint
    # doesn't yet allow 'delete'.
    with db.connect() as conn:
        conn.executescript(
            """
            CREATE TABLE schema_meta (version INTEGER NOT NULL);
            INSERT INTO schema_meta (version) VALUES (1);
            CREATE TABLE media_items (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                original_path TEXT NOT NULL,
                title TEXT NOT NULL,
                year INTEGER,
                tmdb_id INTEGER,
                media_type TEXT NOT NULL CHECK (media_type IN ('movie', 'tv')),
                season_number INTEGER,
                episode_number INTEGER,
                final_path TEXT,
                archived_at TEXT,
                watched INTEGER NOT NULL DEFAULT 0,
                metadata TEXT,
                created_at TEXT NOT NULL
            );
            CREATE TABLE operation_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                operation_type TEXT NOT NULL CHECK (operation_type IN ('archive', 'rename', 'purge', 'tracker_check')),
                media_id INTEGER REFERENCES media_items(id),
                details TEXT,
                status TEXT NOT NULL CHECK (status IN ('success', 'failed', 'pending')),
                error_message TEXT,
                created_at TEXT NOT NULL
            );
            CREATE TABLE archive_tracker (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                tmdb_id INTEGER NOT NULL,
                media_type TEXT NOT NULL CHECK (media_type IN ('movie', 'tv')),
                title TEXT NOT NULL,
                current_season_archived INTEGER,
                latest_known_season INTEGER,
                movie_release_status TEXT,
                last_checked TEXT,
                pending_notification INTEGER NOT NULL DEFAULT 0,
                notification_sent_at TEXT,
                created_at TEXT NOT NULL,
                UNIQUE (tmdb_id, media_type)
            );
            """
        )
        conn.execute(
            "INSERT INTO operation_log (operation_type, status, created_at) VALUES ('archive', 'success', ?)",
            (_now(),),
        )

    db.migrate()

    version = db.fetch_one("SELECT version FROM schema_meta")["version"]
    assert version == 19

    # Pre-existing row survived the table rebuild.
    ops = db.list_operations(operation_type="archive")
    assert len(ops) == 1

    # And 'delete' is now a valid operation_type.
    db.log_operation("delete", "success", details={"path": "/x"})
    assert len(db.list_operations(operation_type="delete")) == 1

    # v3's match_attempted_at column exists and is usable.
    item_id = db.create_media_item(original_path="x", title="T", media_type="movie")
    db.update_media_item(item_id, match_attempted_at="2026-01-01T00:00:00+00:00")
    assert db.get_media_item(item_id)["match_attempted_at"] == "2026-01-01T00:00:00+00:00"

    # v10's api_tokens table exists and is usable.
    db.create_api_token("phone", "secret-abc")
    assert db.get_api_token_by_value("secret-abc")["name"] == "phone"

    # v4's archive_tracker.muted column exists and defaults to unmuted.
    db.upsert_tracker(tmdb_id=1, media_type="tv", title="Show")
    row = db.get_tracker(1, "tv")
    assert row["muted"] == 0

    # v5's media_items.imdb_id column exists and is usable.
    db.update_media_item(item_id, imdb_id="tt0111161")
    assert db.get_media_item(item_id)["imdb_id"] == "tt0111161"

    # v6's indexes exist after upgrading from v1.
    indexes = {r["name"] for r in db.fetch_all("SELECT name FROM sqlite_master WHERE type='index'")}
    assert "idx_media_items_tmdb_id" in indexes

    # v7's media_items.manual_override column exists and defaults to unset.
    assert db.get_media_item(item_id)["manual_override"] == 0

    # v8's archive_tracker.snoozed_until/check_interval_hours columns exist.
    tracker_row = db.get_tracker(1, "tv")
    db.update_tracker(tracker_row["id"], snoozed_until="2026-01-01T00:00:00+00:00", check_interval_hours=12.0)
    updated = db.get_tracker(1, "tv")
    assert updated["snoozed_until"] == "2026-01-01T00:00:00+00:00"
    assert updated["check_interval_hours"] == 12.0

    # v9's notification_history table exists and is usable.
    db.log_notification(tracker_row["id"], 1, "tv", "Show", "New season available")
    assert len(db.list_notification_history()) == 1

    # v11's media_items.tags column exists and is usable.
    db.update_media_item(item_id, tags=["Favorites"])
    assert db.get_media_item(item_id)["tags"] == '["Favorites"]'

    # v12's storage_snapshots table exists and is usable.
    db.record_storage_snapshot("Movies", 100, 200)
    assert len(db.list_storage_snapshots("Movies")) == 1

    # v13's api_tokens.scope column exists and defaults to read_write for
    # tokens that predate the column, and is usable for new ones.
    db.create_api_token("legacy-device", "legacy-token-value")
    legacy_token = db.get_api_token_by_value("legacy-token-value")
    assert legacy_token["scope"] == "read_write"
    db.create_api_token("readonly-device", "readonly-token-value", scope="read_only")
    assert db.get_api_token_by_value("readonly-token-value")["scope"] == "read_only"

    # v14's viewers/viewer_watched_items tables exist and are usable.
    viewer_id = db.create_viewer("Alex")
    db.set_viewer_watched(viewer_id, item_id, True)
    assert db.is_viewer_watched(viewer_id, item_id) is True

    # v18's archive_tracker.poster_path/overview columns exist and are usable.
    db.update_tracker(tracker_row["id"], poster_path="/poster.jpg", overview="Synopsis")
    backfilled = db.get_tracker(1, "tv")
    assert backfilled["poster_path"] == "/poster.jpg"
    assert backfilled["overview"] == "Synopsis"


def test_create_and_list_viewers(db):
    db.create_viewer("Alex")
    db.create_viewer("Sam")
    viewers = db.list_viewers()
    assert [v["name"] for v in viewers] == ["Alex", "Sam"]  # alphabetical


def test_create_viewer_duplicate_name_raises(db):
    import sqlite3
    import pytest

    db.create_viewer("Alex")
    with pytest.raises(sqlite3.IntegrityError):
        db.create_viewer("Alex")


def test_set_viewer_watched_toggles_and_reports_correctly(db):
    item_id = db.create_media_item(original_path="x", title="Movie", media_type="movie")
    viewer_id = db.create_viewer("Alex")

    assert db.is_viewer_watched(viewer_id, item_id) is False
    db.set_viewer_watched(viewer_id, item_id, True)
    assert db.is_viewer_watched(viewer_id, item_id) is True
    assert db.list_viewer_watched_ids(viewer_id) == {item_id}

    db.set_viewer_watched(viewer_id, item_id, False)
    assert db.is_viewer_watched(viewer_id, item_id) is False
    assert db.list_viewer_watched_ids(viewer_id) == set()


def test_set_viewer_watched_is_independent_per_viewer(db):
    item_id = db.create_media_item(original_path="x", title="Movie", media_type="movie")
    alex_id = db.create_viewer("Alex")
    sam_id = db.create_viewer("Sam")

    db.set_viewer_watched(alex_id, item_id, True)

    assert db.is_viewer_watched(alex_id, item_id) is True
    assert db.is_viewer_watched(sam_id, item_id) is False
    # the shared global flag is untouched by a per-viewer write
    assert db.get_media_item(item_id)["watched"] == 0


def test_delete_viewer_cascades_watched_rows(db):
    item_id = db.create_media_item(original_path="x", title="Movie", media_type="movie")
    viewer_id = db.create_viewer("Alex")
    db.set_viewer_watched(viewer_id, item_id, True)

    db.delete_viewer(viewer_id)

    assert db.get_viewer(viewer_id) is None
    rows = db.fetch_all("SELECT * FROM viewer_watched_items WHERE viewer_id = ?", (viewer_id,))
    assert rows == []


def test_record_storage_snapshot_dedupes_within_same_day(db):
    db.record_storage_snapshot("Movies", 100, 200)
    db.record_storage_snapshot("Movies", 150, 200)
    snapshots = db.list_storage_snapshots("Movies")
    assert len(snapshots) == 1
    assert snapshots[0]["used_bytes"] == 150


def test_list_storage_snapshots_filters_by_label_and_window(db):
    from datetime import datetime, timedelta, timezone

    db.record_storage_snapshot("Movies", 100, 200)
    db.record_storage_snapshot("TV", 50, 200)
    old = (datetime.now(timezone.utc) - timedelta(days=200)).isoformat()
    db.execute_query(
        "UPDATE storage_snapshots SET created_at = ? WHERE label = ?", (old, "TV"),
    )

    movies_snapshots = db.list_storage_snapshots("Movies", since_days=90)
    tv_snapshots = db.list_storage_snapshots("TV", since_days=90)
    assert len(movies_snapshots) == 1
    assert tv_snapshots == []


def test_sync_tv_show_inserts_with_default_watching_status(db):
    db.sync_tv_show(75219, "9-1-1", imdb_id="tt7235466", poster_path="/p.jpg", overview="plot", genres=["Drama"])
    show = db.get_tv_show(75219)
    assert show["title"] == "9-1-1"
    assert show["status"] == "watching"
    assert json.loads(show["genres"]) == ["Drama"]


def test_sync_tv_show_never_overwrites_an_existing_status(db):
    db.sync_tv_show(75219, "9-1-1")
    db.set_tv_show_status(75219, "ended")
    db.sync_tv_show(75219, "9-1-1", overview="updated plot")
    show = db.get_tv_show(75219)
    assert show["status"] == "ended"
    assert show["overview"] == "updated plot"


def test_sync_tv_show_keeps_prior_value_when_new_field_is_blank(db):
    db.sync_tv_show(75219, "9-1-1", imdb_id="tt7235466", poster_path="/p.jpg", overview="plot")
    db.sync_tv_show(75219, "9-1-1", imdb_id=None, poster_path=None, overview="")
    show = db.get_tv_show(75219)
    assert show["imdb_id"] == "tt7235466"
    assert show["poster_path"] == "/p.jpg"
    assert show["overview"] == "plot"


def test_list_tv_shows_orders_by_title(db):
    db.sync_tv_show(2, "Zeta Show")
    db.sync_tv_show(1, "Alpha Show")
    titles = [s["title"] for s in db.list_tv_shows()]
    assert titles == ["Alpha Show", "Zeta Show"]
