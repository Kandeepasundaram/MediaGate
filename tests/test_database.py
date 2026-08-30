from __future__ import annotations


def test_init_db_creates_tables(db):
    tables = {r["name"] for r in db.fetch_all("SELECT name FROM sqlite_master WHERE type='table'")}
    assert {"media_items", "archive_tracker", "operation_log", "schema_meta"} <= tables


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
    assert version == 6

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
