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


def test_operation_log(db):
    item_id = db.create_media_item(original_path="a", title="A", media_type="movie")
    db.log_operation("archive", "success", media_id=item_id, details={"a": 1})
    ops = db.list_operations(operation_type="archive")
    assert len(ops) == 1
    assert ops[0]["status"] == "success"
