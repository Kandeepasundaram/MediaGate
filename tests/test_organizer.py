from __future__ import annotations

import json

import pytest

from app.core.organizer import OrganizeError, organize_file
from app.core.renamer import RenamePlan


def _plan(tmp_path, **overrides) -> RenamePlan:
    source = tmp_path / "source.mkv"
    if "source_path" not in overrides:
        source.write_bytes(b"data")
    defaults = dict(
        source_path=source,
        dest_path=tmp_path / "dest" / "Movie (2020).mkv",
        media_type="movie",
        tmdb_id=42,
        title="Movie",
        year=2020,
        poster_path="/poster.jpg",
        overview="Plot summary.",
    )
    defaults.update(overrides)
    return RenamePlan(**defaults)


def test_organize_file_moves_and_creates_row_for_untracked_file(db, tmp_path):
    plan = _plan(tmp_path)
    media_id = organize_file(db, plan)

    assert not plan.source_path.exists()
    assert plan.dest_path.exists()
    item = db.get_media_item(media_id)
    assert item["final_path"] == str(plan.dest_path)
    assert item["original_path"] == str(plan.source_path)
    assert json.loads(item["metadata"])["poster_path"] == "/poster.jpg"


def test_organize_file_updates_existing_row_in_place(db, tmp_path):
    source = tmp_path / "source.mkv"
    source.write_bytes(b"data")
    existing_id = db.create_media_item(
        original_path=str(source), final_path=str(source), title="Old Title", media_type="movie"
    )

    plan = _plan(tmp_path, source_path=source)
    media_id = organize_file(db, plan)

    assert media_id == existing_id  # same row updated, not duplicated
    assert len(db.list_media_items(media_type="movie")) == 1
    item = db.get_media_item(existing_id)
    assert item["title"] == "Movie"
    assert item["final_path"] == str(plan.dest_path)


def test_organize_file_noop_move_when_already_at_destination(db, tmp_path):
    source = tmp_path / "Movie (2020).mkv"
    source.write_bytes(b"data")
    plan = _plan(tmp_path, source_path=source, dest_path=source)

    media_id = organize_file(db, plan)

    assert source.exists()  # untouched, no move attempted
    item = db.get_media_item(media_id)
    assert item["final_path"] == str(source)


def test_organize_file_moves_sibling_subtitles(db, tmp_path):
    plan = _plan(tmp_path)
    sub = plan.source_path.parent / f"{plan.source_path.stem}.en.srt"
    sub.write_text("subtitle")

    organize_file(db, plan)

    assert not sub.exists()
    # Renamed to match the video's new base name, not left under its old
    # one -- Plex/Jellyfin match subtitles to a video by shared file stem.
    assert (plan.dest_path.parent / f"{plan.dest_path.stem}.en.srt").exists()


def test_organize_file_renames_multiple_sibling_subtitles(db, tmp_path):
    plan = _plan(tmp_path)
    en_sub = plan.source_path.parent / f"{plan.source_path.stem}.en.srt"
    en_sub.write_text("subtitle")
    plain_sub = plan.source_path.parent / f"{plan.source_path.stem}.srt"
    plain_sub.write_text("subtitle")

    organize_file(db, plan)

    assert (plan.dest_path.parent / f"{plan.dest_path.stem}.en.srt").exists()
    assert (plan.dest_path.parent / f"{plan.dest_path.stem}.srt").exists()


def test_organize_file_writes_nfo_alongside_dest(db, tmp_path):
    plan = _plan(tmp_path)
    organize_file(db, plan)

    nfo = plan.dest_path.parent / "movie.nfo"
    assert nfo.exists()
    assert "Movie" in nfo.read_text(encoding="utf-8")


def test_organize_file_raises_and_logs_on_missing_source(db, tmp_path):
    plan = _plan(tmp_path, source_path=tmp_path / "does_not_exist.mkv")

    with pytest.raises(OrganizeError):
        organize_file(db, plan)

    ops = db.list_operations(operation_type="rename")
    assert ops[0]["status"] == "failed"
