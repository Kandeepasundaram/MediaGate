from __future__ import annotations

from app.core.scanner import scan_directory, scan_targets


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


def test_scan_targets_dedupes_overlapping_roots(tmp_path):
    movies = tmp_path / "movies"
    movies.mkdir()
    (movies / "movie.2020.mkv").write_bytes(b"1")

    # active_dir and archive_movies point at the same folder (organize-in-place setup)
    results = scan_targets([movies, movies, tmp_path])

    assert len(results) == 1
    assert results[0].path.name == "movie.2020.mkv"


def test_scan_targets_excludes_known_paths(tmp_path):
    movies = tmp_path / "movies"
    movies.mkdir()
    raw = movies / "movie.2020.mkv"
    raw.write_bytes(b"1")
    organized_dir = movies / "Movie (2020)"
    organized_dir.mkdir()
    organized = organized_dir / "Movie (2020).mkv"
    organized.write_bytes(b"2")

    known = {str(raw.resolve())}
    results = scan_targets([movies], known_paths=known)

    assert len(results) == 1
    assert results[0].path.name == "Movie (2020).mkv"


def test_scan_targets_excludes_all_known_returns_empty(tmp_path):
    movies = tmp_path / "movies"
    movies.mkdir()
    raw = movies / "movie.2020.mkv"
    raw.write_bytes(b"1")

    results = scan_targets([movies], known_paths={str(raw.resolve())})

    assert results == []
