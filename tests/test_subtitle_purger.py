from __future__ import annotations

from app.core.subtitle_purger import purge_subtitles


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
    assert len(result.deleted) == 1


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
