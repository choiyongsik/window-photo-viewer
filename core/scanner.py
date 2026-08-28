from __future__ import annotations

import os
import re
import stat
from collections.abc import Iterator
from pathlib import Path

from core.models import MediaItem, kind_for

_NUM_RE = re.compile(r"(\d+)")
# Windows marks files hidden with an attribute rather than a leading dot; 0 elsewhere,
# which makes the mask test below a no-op on other platforms.
_HIDDEN_MASK = getattr(stat, "FILE_ATTRIBUTE_HIDDEN", 0)


def natural_key(name: str) -> list[int | str]:
    """'IMG_10' sorts after 'IMG_2'. Case-insensitive on the text parts."""
    return [int(tok) if tok.isdigit() else tok.lower() for tok in _NUM_RE.split(name)]


def is_hidden(entry: Path) -> bool:
    """Leading '.' or the Windows hidden attribute. A stat() failure counts as
    not hidden -- the caller decides what to do with an unreadable entry."""
    if entry.name.startswith("."):
        return True
    if not _HIDDEN_MASK:
        return False
    try:
        st = entry.stat()
    except OSError:
        return False
    return bool(getattr(st, "st_file_attributes", 0) & _HIDDEN_MASK)


def iter_media_folders(root: Path) -> Iterator[Path]:
    """*root* and every non-hidden directory below it, depth-first, siblings in
    natural order. Directories that vanish or can't be read are skipped silently;
    a missing root yields nothing."""
    for dirpath, dirnames, _filenames in os.walk(root, onerror=lambda _e: None):
        here = Path(dirpath)
        # In-place edit prunes the walk: hidden dirs are never descended into.
        dirnames[:] = sorted(
            (d for d in dirnames if not is_hidden(here / d)),
            key=natural_key,
        )
        yield here


def scan(folder: Path) -> list[MediaItem]:
    """Non-recursive listing of supported media in *folder*, naturally sorted by file name.

    Ratings/labels/EXIF are NOT read here — see core.metadata.populate.
    """
    if not folder.exists():
        raise FileNotFoundError(folder)
    if not folder.is_dir():
        raise NotADirectoryError(folder)

    items: list[MediaItem] = []
    for entry in folder.iterdir():
        if not entry.is_file() or entry.name.startswith("."):
            continue
        kind = kind_for(entry)
        if kind is None:
            continue
        st = entry.stat()
        if _HIDDEN_MASK and getattr(st, "st_file_attributes", 0) & _HIDDEN_MASK:
            continue
        items.append(MediaItem(path=entry, kind=kind, mtime=st.st_mtime, size=st.st_size))
    items.sort(key=lambda i: natural_key(i.path.name))
    return items
