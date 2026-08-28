"""On-disk memo of ratings already read from files: (path, mtime, size) → (rating, label).

Why: a root-wide "rated photos" collection has to answer "what rating does this
file carry?" for every file under the root. Even the header-only read costs one
file open per photo, which on an HDD is a head seek (~10ms) — 1万 files ≈ 2 min.
With this cache the common re-run is one stat() per file instead: files whose
mtime and size are unchanged are trusted without being opened.

Unrated files are cached too (rating 0) — those are the majority, and not
re-opening them is where the time goes.

Staleness: any tool that rewrites XMP changes the file's mtime/size (Lightroom,
this viewer, exiftool by default), which invalidates the entry. A tool that
deliberately preserves mtime (exiftool -P) would leave a stale rating behind;
`refresh=True` paths (F5) re-read everything and overwrite.

Thread-safe: the collection job stores from a worker thread while the UI thread
records the viewer's own writes.
"""
from __future__ import annotations

import json
import os
import threading
from pathlib import Path

from core.models import Label

_FORMAT_VERSION = 1


class RatingCache:
    def __init__(self, file: Path):
        self.file = file
        self._lock = threading.Lock()
        # str(path) -> [mtime, size, rating, label]
        self._entries: dict[str, list] = {}
        self._dirty = False
        self._load()

    # ---------- persistence ----------
    def _load(self) -> None:
        try:
            raw = json.loads(self.file.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return
        if not isinstance(raw, dict) or raw.get("version") != _FORMAT_VERSION:
            return
        entries = raw.get("entries")
        if not isinstance(entries, dict):
            return
        clean: dict[str, list] = {}
        for key, value in entries.items():
            if (
                isinstance(key, str)
                and isinstance(value, list)
                and len(value) == 4
                and isinstance(value[0], (int, float))
                and isinstance(value[1], int)
                and isinstance(value[2], int)
                and isinstance(value[3], str)
            ):
                clean[key] = value
        self._entries = clean

    def save(self) -> None:
        """Write to disk if anything changed since the last save. Atomic
        (tmp + replace); an unwritable location is not an error — the cache is
        only an accelerator."""
        with self._lock:
            if not self._dirty:
                return
            payload = json.dumps({"version": _FORMAT_VERSION, "entries": self._entries}, ensure_ascii=False)
            self._dirty = False
        tmp = self.file.with_name(self.file.name + ".tmp")
        try:
            self.file.parent.mkdir(parents=True, exist_ok=True)
            tmp.write_text(payload, encoding="utf-8")
            os.replace(tmp, self.file)
        except OSError:
            tmp.unlink(missing_ok=True)
            with self._lock:
                self._dirty = True   # try again next time

    # ---------- lookups ----------
    def lookup(self, path: Path, mtime: float, size: int) -> tuple[int, Label] | None:
        with self._lock:
            entry = self._entries.get(str(path))
        if entry is None or entry[0] != mtime or entry[1] != size:
            return None
        return entry[2], Label.from_xmp(entry[3])

    def store(self, path: Path, mtime: float, size: int, rating: int, label: Label) -> None:
        with self._lock:
            self._entries[str(path)] = [mtime, size, rating, label.value]
            self._dirty = True

    def retain_under(self, root: Path, seen: set[Path]) -> None:
        """Forget entries for files under *root* that a full walk did not see
        (deleted or moved). Entries outside *root* are left alone."""
        prefix = str(root).rstrip("\\/") + os.sep
        keep = {str(p) for p in seen}
        with self._lock:
            doomed = [k for k in self._entries if k.startswith(prefix) and k not in keep]
            for k in doomed:
                del self._entries[k]
            if doomed:
                self._dirty = True

    def __len__(self) -> int:
        with self._lock:
            return len(self._entries)
