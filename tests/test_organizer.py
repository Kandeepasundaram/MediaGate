from __future__ import annotations

import json

import pytest

from app.core.organizer import OrganizeError, OrganizeScope, organize_file
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


def test_organize_file_skips_nfo_when_disabled(db, tmp_path):
    plan = _plan(tmp_path)
    organize_file(db, plan, write_nfo_files=False)

    nfo = plan.dest_path.parent / "movie.nfo"
    assert not nfo.exists()


def test_organize_file_raises_and_logs_on_missing_source(db, tmp_path):
    plan = _plan(tmp_path, source_path=tmp_path / "does_not_exist.mkv")

    with pytest.raises(OrganizeError):
        organize_file(db, plan)

    ops = db.list_operations(operation_type="rename")
    assert ops[0]["status"] == "failed"


def _movie_in_folder(tmp_path, folder="Old Folder", name="Old.Name.2020.mkv"):
    src = tmp_path / folder / name
    src.parent.mkdir()
    src.write_bytes(b"data")
    return src


def test_scope_folder_only_renames_folder_keeps_file_name(db, tmp_path):
    src = _movie_in_folder(tmp_path)
    (src.parent / "extra.txt").write_text("keep me")
    row_id = db.create_media_item(original_path=str(src), final_path=str(src), title="Old", media_type="movie")
    plan = _plan(tmp_path, source_path=src, dest_path=tmp_path / "Movie (2020)" / "Movie (2020).mkv")

    media_id = organize_file(db, plan, scope=OrganizeScope(file=True, folder=True, sidecars=True))
    assert media_id == row_id  # sanity: full scope still updates in place

    src2 = _movie_in_folder(tmp_path, "Other Folder", "Keep.Me.mkv")
    plan2 = _plan(tmp_path, source_path=src2, dest_path=tmp_path / "Renamed (2020)" / "Renamed (2020).mkv")
    organize_file(db, plan2, scope=OrganizeScope(file=False, folder=True, sidecars=False))

    assert (tmp_path / "Renamed (2020)" / "Keep.Me.mkv").exists()
    assert not (tmp_path / "Other Folder").exists()


def test_scope_file_only_keeps_folder(db, tmp_path):
    src = _movie_in_folder(tmp_path)
    plan = _plan(tmp_path, source_path=src, dest_path=tmp_path / "Movie (2020)" / "Movie (2020).mkv")

    organize_file(db, plan, scope=OrganizeScope(file=True, folder=False, sidecars=False))

    assert (tmp_path / "Old Folder" / "Movie (2020).mkv").exists()
    assert not (tmp_path / "Movie (2020)").exists()


def test_scope_sidecars_off_leaves_subtitles_under_old_name(db, tmp_path):
    src = _movie_in_folder(tmp_path)
    sub = src.parent / "Old.Name.2020.en.srt"
    sub.write_text("subtitle")
    plan = _plan(tmp_path, source_path=src, dest_path=src.parent / "Movie (2020).mkv")

    organize_file(db, plan, scope=OrganizeScope(file=True, folder=False, sidecars=False))

    assert sub.exists()
    assert not (src.parent / "Movie (2020).en.srt").exists()
    assert not (src.parent / "movie.nfo").exists()  # NFO refresh is part of the sidecars scope


def test_scope_sidecars_on_renames_artwork_and_nfo_by_stem(db, tmp_path):
    src = _movie_in_folder(tmp_path)
    (src.parent / "Old.Name.2020.nfo").write_text("<movie/>")
    (src.parent / "Old.Name.2020-poster.jpg").write_bytes(b"img")
    (src.parent / "Old.Name.2020 Extended.srt").write_text("other cut")  # not this video's sidecar
    plan = _plan(tmp_path, source_path=src, dest_path=src.parent / "Movie (2020).mkv")

    organize_file(db, plan, scope=OrganizeScope(file=True, folder=False, sidecars=True))

    assert (src.parent / "Movie (2020).nfo").exists()
    assert (src.parent / "Movie (2020)-poster.jpg").exists()
    assert (src.parent / "Old.Name.2020 Extended.srt").exists()


def test_folder_rename_rebases_other_tracked_rows(db, tmp_path):
    ep1 = tmp_path / "Show" / "Season 1" / "a.mkv"
    ep2 = tmp_path / "Show" / "Season 1" / "b.mkv"
    ep1.parent.mkdir(parents=True)
    ep1.write_bytes(b"1")
    ep2.write_bytes(b"2")
    db.create_media_item(original_path=str(ep1), final_path=str(ep1), title="Show", media_type="tv")
    other_id = db.create_media_item(original_path=str(ep2), final_path=str(ep2), title="Show", media_type="tv")
    plan = _plan(
        tmp_path, source_path=ep1, media_type="tv", season=1, episode=1,
        dest_path=tmp_path / "Show (2020)" / "Season 01" / "Show - S01E01.mkv",
    )

    organize_file(db, plan, scope=OrganizeScope(file=True, folder=True, sidecars=True))

    assert (tmp_path / "Show (2020)" / "Season 01" / "Show - S01E01.mkv").exists()
    assert (tmp_path / "Show (2020)" / "Season 01" / "b.mkv").exists()  # carried along, untouched
    assert db.get_media_item(other_id)["final_path"] == str(tmp_path / "Show (2020)" / "Season 01" / "b.mkv")


def test_protected_root_is_never_renamed(db, tmp_path):
    root = tmp_path / "movies"
    root.mkdir()
    src = root / "Loose.mkv"
    src.write_bytes(b"data")
    plan = _plan(tmp_path, source_path=src, dest_path=tmp_path / "other" / "Loose.mkv")

    organize_file(db, plan, scope=OrganizeScope(file=False, folder=True, sidecars=False), protected_roots={root})

    assert root.exists()  # the root folder itself wasn't renamed away
