from __future__ import annotations

import json
import shutil
from unittest.mock import MagicMock

import pytest

from app.core.archiver import ArchiveError, archive_file
from app.core.renamer import RenamePlan


@pytest.fixture(autouse=True)
def no_network_artwork(monkeypatch):
    """archive_file best-effort-downloads poster art; stub it out so tests
    don't make a real network call to image.tmdb.org."""
    monkeypatch.setattr("app.core.archiver.download_artwork", MagicMock(return_value={}))


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
    plan = _plan(tmp_path, vote_average=7.5, genres=["Drama"])
    media_id = archive_file(db, plan)

    item = db.get_media_item(media_id)
    metadata = json.loads(item["metadata"])
    assert metadata["poster_path"] == "/poster.jpg"
    assert metadata["overview"] == "Plot summary."
    assert metadata["vote_average"] == 7.5
    assert metadata["genres"] == ["Drama"]
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


def test_archive_file_writes_nfo_alongside_dest(db, tmp_path):
    plan = _plan(tmp_path)
    archive_file(db, plan)

    nfo = plan.dest_path.parent / "movie.nfo"
    assert nfo.exists()
    assert "Movie" in nfo.read_text(encoding="utf-8")


def test_archive_file_downloads_artwork_alongside_dest(db, tmp_path, monkeypatch):
    spy = MagicMock(return_value={"poster.jpg": tmp_path / "dest" / "poster.jpg"})
    monkeypatch.setattr("app.core.archiver.download_artwork", spy)
    plan = _plan(tmp_path)

    archive_file(db, plan)

    spy.assert_called_once_with(plan.dest_path.parent, plan.poster_path)


def test_archive_file_raises_on_zero_byte_source(db, tmp_path):
    source = tmp_path / "empty.mkv"
    source.write_bytes(b"")
    plan = _plan(tmp_path, source_path=source)

    with pytest.raises(ArchiveError, match="0 bytes"):
        archive_file(db, plan)

    assert not plan.dest_path.exists()
    ops = db.list_operations(operation_type="archive")
    assert ops[0]["status"] == "failed"


def test_archive_file_raises_on_checksum_mismatch(db, tmp_path, monkeypatch):
    plan = _plan(tmp_path)

    real_copy2 = shutil.copy2

    def corrupting_copy(src, dst, *args, **kwargs):
        real_copy2(src, dst, *args, **kwargs)
        with open(dst, "ab") as f:
            f.write(b"corruption")

    monkeypatch.setattr("app.core.archiver.shutil.copy2", corrupting_copy)

    with pytest.raises(ArchiveError, match="Checksum mismatch"):
        archive_file(db, plan)

    assert not plan.dest_path.exists()  # bad copy is cleaned up
    ops = db.list_operations(operation_type="archive")
    assert ops[0]["status"] == "failed"
