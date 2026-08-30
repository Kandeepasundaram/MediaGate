from __future__ import annotations


def test_init_db_creates_tables(db):
    tables = {r["name"] for r in db.fetch_all("SELECT name FROM sqlite_master WHERE type='table'")}
    assert {"media_items", "archive_tracker", "operation_log", "schema_meta"} <= tables


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


def test_migration_v2_upgrades_v1_database_to_allow_delete(tmp_path):
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
            """
        )
        conn.execute(
            "INSERT INTO operation_log (operation_type, status, created_at) VALUES ('archive', 'success', ?)",
            (_now(),),
        )

    db.migrate()

    version = db.fetch_one("SELECT version FROM schema_meta")["version"]
    assert version == 2

    # Pre-existing row survived the table rebuild.
    ops = db.list_operations(operation_type="archive")
    assert len(ops) == 1

    # And 'delete' is now a valid operation_type.
    db.log_operation("delete", "success", details={"path": "/x"})
    assert len(db.list_operations(operation_type="delete")) == 1
