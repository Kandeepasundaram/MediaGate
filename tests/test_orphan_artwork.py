from __future__ import annotations

from app.core.orphan_artwork import cleanup_orphaned_artwork, find_orphaned_artwork


def test_find_orphaned_artwork_flags_folder_with_no_video(tmp_path):
    folder = tmp_path / "Movie (2020)"
    folder.mkdir()
    (folder / "poster.jpg").write_bytes(b"x")
    (folder / "movie.nfo").write_text("<movie/>")
    (folder / "Movie (2020).en.srt").write_text("subtitle")

    groups = find_orphaned_artwork(tmp_path)
    assert len(groups) == 1
    assert groups[0].folder == folder
    names = {f.name for f in groups[0].files}
    assert names == {"poster.jpg", "movie.nfo", "Movie (2020).en.srt"}


def test_find_orphaned_artwork_ignores_folder_with_video(tmp_path):
    folder = tmp_path / "Movie (2020)"
    folder.mkdir()
    (folder / "poster.jpg").write_bytes(b"x")
    (folder / "Movie (2020).mkv").write_bytes(b"data")

    assert find_orphaned_artwork(tmp_path) == []


def test_find_orphaned_artwork_ignores_folder_with_no_artwork(tmp_path):
    folder = tmp_path / "Random"
    folder.mkdir()
    (folder / "readme.txt").write_text("hello")

    assert find_orphaned_artwork(tmp_path) == []


def test_find_orphaned_artwork_returns_empty_for_missing_root(tmp_path):
    assert find_orphaned_artwork(tmp_path / "does_not_exist") == []


def test_find_orphaned_artwork_scans_nested_folders(tmp_path):
    folder = tmp_path / "Show" / "Season 01"
    folder.mkdir(parents=True)
    (folder / "poster.jpg").write_bytes(b"x")

    groups = find_orphaned_artwork(tmp_path)
    assert len(groups) == 1
    assert groups[0].folder == folder


def test_cleanup_orphaned_artwork_removes_files_and_returns_count(tmp_path):
    folder = tmp_path / "Movie (2020)"
    folder.mkdir()
    (folder / "poster.jpg").write_bytes(b"x")
    (folder / "movie.nfo").write_text("<movie/>")

    groups = find_orphaned_artwork(tmp_path)
    removed = cleanup_orphaned_artwork(groups)

    assert removed == 2
    assert not (folder / "poster.jpg").exists()
    assert not (folder / "movie.nfo").exists()
    assert folder.exists()  # the folder itself is left in place


def test_cleanup_orphaned_artwork_survives_already_removed_file(tmp_path):
    folder = tmp_path / "Movie (2020)"
    folder.mkdir()
    poster = folder / "poster.jpg"
    poster.write_bytes(b"x")

    groups = find_orphaned_artwork(tmp_path)
    poster.unlink()  # removed out from under it before cleanup runs

    assert cleanup_orphaned_artwork(groups) == 0
