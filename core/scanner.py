from __future__ import annotations

import re
from pathlib import Path

from core.models import MediaItem, kind_for

_NUM_RE = re.compile(r"(\d+)")


def natural_key(name: str) -> list[int | str]:
    """'IMG_10' sorts after 'IMG_2'. Case-insensitive on the text parts."""
    return [int(tok) if tok.isdigit() else tok.lower() for tok in _NUM_RE.split(name)]


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
        items.append(MediaItem(path=entry, kind=kind, mtime=st.st_mtime, size=st.st_size))
    items.sort(key=lambda i: natural_key(i.path.name))
    return items
