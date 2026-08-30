from __future__ import annotations

from app.core.scanner import scan_directory


def test_scan_directory_finds_videos_and_matches_subtitles(tmp_path):
    movie_dir = tmp_path / "Movie Folder"
    movie_dir.mkdir()
    (movie_dir / "movie.file.mkv").write_bytes(b"0" * 100)
    (movie_dir / "movie.file.en.srt").write_text("english sub")
    (movie_dir / "movie.file.fr.srt").write_text("french sub")
    (movie_dir / "readme.txt").write_text("not media")

    results = scan_directory(tmp_path)

    assert len(results) == 1
    scanned = results[0]
    assert scanned.path.name == "movie.file.mkv"
    assert scanned.size_bytes == 100
    assert len(scanned.subtitles) == 2
    assert scanned.parsed.media_type == "movie"


def test_scan_directory_recursive_and_ignores_non_video(tmp_path):
    (tmp_path / "a").mkdir()
    (tmp_path / "a" / "show.s01e01.mkv").write_bytes(b"1")
    (tmp_path / "b").mkdir()
    (tmp_path / "b" / "show.s01e02.mp4").write_bytes(b"2")
    (tmp_path / "notes.txt").write_text("skip me")

    results = scan_directory(tmp_path)

    assert len(results) == 2
    assert {r.parsed.season for r in results} == {1}
    assert {r.parsed.episode for r in results} == {1, 2}


def test_scan_missing_directory_returns_empty(tmp_path):
    assert scan_directory(tmp_path / "nope") == []
