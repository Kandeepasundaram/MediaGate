from __future__ import annotations

from unittest.mock import MagicMock

from app.core.opensubtitles_client import SubtitleMatch
from app.core.subtitle_purger import fetch_missing_subtitle, missing_keep_language, purge_subtitles


def _make_subs(root):
    root.mkdir(parents=True, exist_ok=True)
    (root / "Movie.srt").write_text("no-lang-tag, treated as default")
    (root / "Movie.en.srt").write_text("english")
    (root / "Movie.eng.srt").write_text("english alt")
    (root / "Movie.fr.srt").write_text("french")
    (root / "Movie.spa.ass").write_text("spanish")


def test_dry_run_does_not_delete(tmp_path):
    _make_subs(tmp_path)
    result = purge_subtitles(tmp_path, dry_run=True)

    assert len(result.kept) == 3
    assert len(result.deleted) == 2
    assert all(p.exists() for p in result.deleted)


def test_purge_deletes_non_english_only(tmp_path):
    _make_subs(tmp_path)
    result = purge_subtitles(tmp_path, dry_run=False)

    remaining = {p.name for p in tmp_path.iterdir()}
    assert remaining == {"Movie.srt", "Movie.en.srt", "Movie.eng.srt"}
    assert len(result.deleted) == 2


def test_custom_keep_languages(tmp_path):
    (tmp_path / "Movie.fr.srt").write_text("french")
    (tmp_path / "Movie.en.srt").write_text("english")

    result = purge_subtitles(tmp_path, keep_languages=["fr"], dry_run=False)

    remaining = {p.name for p in tmp_path.iterdir()}
    assert remaining == {"Movie.fr.srt"}


def test_missing_keep_language_untagged_covers_only_first_language(tmp_path):
    tmp_path.mkdir(exist_ok=True)
    (tmp_path / "Movie.srt").write_text("untagged, assumed default")

    # Untagged covers "en" (index 0), but "fr" still has no matching sibling.
    assert missing_keep_language(tmp_path, "Movie", ["en", "fr"]) == "fr"


def test_missing_keep_language_reports_first_uncovered(tmp_path):
    tmp_path.mkdir(exist_ok=True)
    (tmp_path / "Movie.en.srt").write_text("english")

    assert missing_keep_language(tmp_path, "Movie", ["en", "fr"]) == "fr"


def test_missing_keep_language_none_when_all_present(tmp_path):
    tmp_path.mkdir(exist_ok=True)
    (tmp_path / "Movie.en.srt").write_text("english")
    (tmp_path / "Movie.fr.srt").write_text("french")

    assert missing_keep_language(tmp_path, "Movie", ["en", "fr"]) is None


def test_missing_keep_language_untagged_does_not_cover_second_language(tmp_path):
    tmp_path.mkdir(exist_ok=True)
    (tmp_path / "Movie.srt").write_text("untagged")

    assert missing_keep_language(tmp_path, "Movie", ["fr", "en"]) == "en"


def test_missing_keep_language_no_folder():
    from pathlib import Path
    assert missing_keep_language(Path("/does/not/exist"), "Movie", ["en"]) is None


def test_missing_keep_language_no_keep_languages(tmp_path):
    tmp_path.mkdir(exist_ok=True)
    assert missing_keep_language(tmp_path, "Movie", []) is None


def test_fetch_missing_subtitle_saves_downloaded_content(tmp_path):
    tmp_path.mkdir(exist_ok=True)
    client = MagicMock()
    client.find_subtitle.return_value = SubtitleMatch(file_id=42, language="fr")
    client.download_subtitle.return_value = b"1\n00:00:01,000 --> 00:00:02,000\nBonjour\n"

    dest = fetch_missing_subtitle(client, tmp_path, "Movie", tmdb_id=123, media_type="movie", language="fr")

    assert dest == tmp_path / "Movie.fr.srt"
    assert dest.read_bytes() == b"1\n00:00:01,000 --> 00:00:02,000\nBonjour\n"
    client.find_subtitle.assert_called_once_with(123, "fr", "movie", None, None)
    client.download_subtitle.assert_called_once_with(42)


def test_fetch_missing_subtitle_returns_none_when_no_match(tmp_path):
    tmp_path.mkdir(exist_ok=True)
    client = MagicMock()
    client.find_subtitle.return_value = None

    dest = fetch_missing_subtitle(client, tmp_path, "Movie", tmdb_id=123, media_type="movie", language="fr")

    assert dest is None
    client.download_subtitle.assert_not_called()


def test_fetch_missing_subtitle_returns_none_when_download_fails(tmp_path):
    tmp_path.mkdir(exist_ok=True)
    client = MagicMock()
    client.find_subtitle.return_value = SubtitleMatch(file_id=42, language="fr")
    client.download_subtitle.return_value = None

    dest = fetch_missing_subtitle(client, tmp_path, "Movie", tmdb_id=123, media_type="movie", language="fr")

    assert dest is None
    assert not (tmp_path / "Movie.fr.srt").exists()


def test_fetch_missing_subtitle_passes_season_episode_for_tv(tmp_path):
    tmp_path.mkdir(exist_ok=True)
    client = MagicMock()
    client.find_subtitle.return_value = SubtitleMatch(file_id=1, language="en")
    client.download_subtitle.return_value = b"data"

    fetch_missing_subtitle(
        client, tmp_path, "Show.S01E02", tmdb_id=9, media_type="tv", language="en", season=1, episode=2
    )

    client.find_subtitle.assert_called_once_with(9, "en", "tv", 1, 2)


def test_cleanup_empty_dirs(tmp_path):
    sub_dir = tmp_path / "Show" / "Season 01"
    sub_dir.mkdir(parents=True)
    (sub_dir / "ep.fr.srt").write_text("french")

    purge_subtitles(tmp_path, dry_run=False, cleanup_empty_dirs=True)

    assert not sub_dir.exists()
    assert not (tmp_path / "Show").exists()


def test_no_subtitles_present(tmp_path):
    result = purge_subtitles(tmp_path, dry_run=False)
    assert result.kept == []
    assert result.deleted == []


def test_missing_directory_returns_empty(tmp_path):
    result = purge_subtitles(tmp_path / "does_not_exist")
    assert result.kept == []
    assert result.deleted == []
