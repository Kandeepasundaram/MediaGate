from __future__ import annotations

import json

import pytest

from app.core.archiver import ArchiveError, archive_file
from app.core.renamer import RenamePlan


def _plan(tmp_path, **overrides) -> RenamePlan:
    source = tmp_path / "source.mkv"
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


def test_archive_file_persists_poster_and_overview_in_metadata(db, tmp_path):
    plan = _plan(tmp_path)
    media_id = archive_file(db, plan)

    item = db.get_media_item(media_id)
    metadata = json.loads(item["metadata"])
    assert metadata["poster_path"] == "/poster.jpg"
    assert metadata["overview"] == "Plot summary."
    assert item["final_path"] == str(plan.dest_path)
    assert item["original_path"] == str(plan.source_path)


def test_archive_file_copies_not_moves(db, tmp_path):
    plan = _plan(tmp_path)
    archive_file(db, plan)

    assert plan.source_path.exists()
    assert plan.dest_path.exists()


def test_archive_file_raises_on_copy_failure(db, tmp_path):
    plan = _plan(tmp_path, source_path=tmp_path / "does_not_exist.mkv")

    with pytest.raises(ArchiveError):
        archive_file(db, plan)

    ops = db.list_operations(operation_type="archive")
    assert ops[0]["status"] == "failed"
