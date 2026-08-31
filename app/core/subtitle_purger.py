"""Deletes non-English subtitle files, with dry-run support and empty-folder cleanup."""
from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger(__name__)

SUBTITLE_EXTENSIONS = {".srt", ".ass", ".ssa"}
DEFAULT_KEEP_LANGUAGES = {"en", "eng", "english"}


@dataclass
class PurgeResult:
    kept: list[Path]
    deleted: list[Path]
    dry_run: bool


def _language_tag(path: Path) -> str | None:
    """None means untagged (e.g. `Movie.srt`), which _is_english and
    missing_keep_language both treat as "covers the default/first
    language" rather than "no language at all"."""
    stem_parts = path.stem.lower().split(".")
    return stem_parts[-1] if len(stem_parts) > 1 else None


def missing_keep_language(folder: Path, video_stem: str, keep_languages: list[str]) -> str | None:
    """First configured keep-language this video has no sibling subtitle
    for -- used to decide what (if anything) to auto-fetch from
    OpenSubtitles before the purge step runs. An untagged subtitle counts
    as covering only the *first* keep language, mirroring _is_english's
    own "untagged = assumed default" convention."""
    if not folder.exists() or not keep_languages:
        return None
    existing_tags: set[str] = set()
    has_untagged = False
    for sub in folder.glob(f"{video_stem}*"):
        if not (sub.is_file() and sub.suffix.lower() in SUBTITLE_EXTENSIONS):
            continue
        tag = _language_tag(sub)
        if tag is None:
            has_untagged = True
        else:
            existing_tags.add(tag)

    for i, lang in enumerate(keep_languages):
        lang = lang.lower()
        if lang in existing_tags:
            continue
        if i == 0 and has_untagged:
            continue
        return lang
    return None


def _is_english(path: Path, keep_languages: set[str]) -> bool:
    """A subtitle like `Movie.en.srt` or `Movie.english.srt` is kept.
    A plain `Movie.srt` with no language tag is kept too (assumed default/English).
    """
    stem_parts = path.stem.lower().split(".")
    if len(stem_parts) == 1:
        return True
    tag = stem_parts[-1]
    return tag in keep_languages


def purge_subtitles(
    root: Path | str,
    keep_languages: list[str] | None = None,
    dry_run: bool = False,
    cleanup_empty_dirs: bool = True,
) -> PurgeResult:
    root = Path(root)
    keep = {lang.lower() for lang in (keep_languages or DEFAULT_KEEP_LANGUAGES)}

    kept: list[Path] = []
    deleted: list[Path] = []

    if not root.exists():
        return PurgeResult(kept=kept, deleted=deleted, dry_run=dry_run)

    subtitle_files = [p for p in root.rglob("*") if p.is_file() and p.suffix.lower() in SUBTITLE_EXTENSIONS]

    for sub in subtitle_files:
        if _is_english(sub, keep):
            kept.append(sub)
            continue
        deleted.append(sub)
        if not dry_run:
            try:
                sub.unlink()
                logger.info("Deleted non-English subtitle: %s", sub)
            except OSError as exc:
                logger.error("Failed to delete %s: %s", sub, exc)
                deleted.remove(sub)

    if cleanup_empty_dirs and not dry_run:
        _remove_empty_dirs(root)

    return PurgeResult(kept=kept, deleted=deleted, dry_run=dry_run)


def fetch_missing_subtitle(
    client,
    folder: Path,
    video_stem: str,
    tmdb_id: int,
    media_type: str,
    language: str,
    season: int | None = None,
    episode: int | None = None,
) -> Path | None:
    """Searches OpenSubtitles for `language` (already established as
    missing by missing_keep_language) and saves the best match next to the
    video as `{video_stem}.{language}.srt`. None on no match, a failed
    download, or the client being unconfigured -- best-effort, same as
    every other optional-API-key integration in this app."""
    match = client.find_subtitle(tmdb_id, language, media_type, season, episode)
    if match is None:
        return None
    content = client.download_subtitle(match.file_id)
    if not content:
        return None
    dest = folder / f"{video_stem}.{language}.srt"
    try:
        dest.write_bytes(content)
    except OSError as exc:
        logger.error("Failed to save fetched subtitle %s: %s", dest, exc)
        return None
    logger.info("Fetched missing %s subtitle: %s", language, dest)
    return dest


def _remove_empty_dirs(root: Path) -> None:
    for dirpath in sorted(root.rglob("*"), key=lambda p: len(p.parts), reverse=True):
        if dirpath.is_dir():
            try:
                next(dirpath.iterdir())
            except StopIteration:
                dirpath.rmdir()
                logger.info("Removed empty directory: %s", dirpath)
            except OSError:
                pass
