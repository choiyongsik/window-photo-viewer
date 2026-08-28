"""Root-wide "rated photos" collection: every media file under a root whose
rating is 1..5, gathered into one list. Pure filesystem + metadata work, no Qt.

Cost model: one header-only XMP read per file (see core.metadata._read_jpeg) and
one directory listing per folder. EXIF is read only for the files that turn out
to be rated, since only those become visible items.
"""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from core import metadata, scanner
from core.models import MediaItem, MediaKind
from core.rating_cache import RatingCache


@dataclass(frozen=True)
class CollectProgress:
    folders: int
    files: int
    rated: int


def collect_rated(
    root: Path,
    *,
    is_cancelled: Callable[[], bool] = lambda: False,
    on_progress: Callable[[CollectProgress], None] | None = None,
    cache: RatingCache | None = None,
    refresh: bool = False,
) -> list[MediaItem] | None:
    """Rated (rating >= 1) items under *root*, in folder-walk order then file
    order. Rejects (-1) are not "rated" here. Returns None when *is_cancelled*
    reports True between folders. *on_progress* is called after each folder.

    With *cache*, files whose mtime/size match a cached entry are not opened
    (see core.rating_cache); *refresh* re-reads every file and refills the cache.
    A completed walk also drops cache entries for files under *root* that no
    longer exist."""
    result: list[MediaItem] = []
    seen: set[Path] = set()
    folders = files = 0
    for folder in scanner.iter_media_folders(root):
        if is_cancelled():
            return None
        try:
            items = scanner.scan(folder)
        except OSError:
            continue
        folders += 1
        files += len(items)
        for item in items:
            seen.add(item.path)
            rating, label = metadata.read_rating_label_cached(
                item.path, item.kind, item.mtime, item.size, cache, refresh=refresh
            )
            if rating < 1:
                continue
            item.rating, item.label = rating, label
            if item.kind is MediaKind.IMAGE:
                item.exif = metadata.read_exif_summary(item.path)
            result.append(item)
        if on_progress is not None:
            on_progress(CollectProgress(folders=folders, files=files, rated=len(result)))
    if cache is not None:
        cache.retain_under(root, seen)
        cache.save()
    return result
