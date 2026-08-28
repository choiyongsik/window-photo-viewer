"""QThreadPool jobs. Each job reports through a shared WorkerSignals instance
(signals emitted from a worker thread are queued to the receiver's thread by Qt)."""
from __future__ import annotations

import threading
import time
from pathlib import Path

from PySide6.QtCore import QObject, QRunnable, Signal
from PySide6.QtGui import QImage, QImageReader

from core import metadata, scanner
from core.collection import CollectProgress, collect_rated
from core.models import Label, MediaItem, MediaKind
from core.rating_cache import RatingCache
from core.thumbnails import ThumbnailCache, ThumbnailError


class WorkerSignals(QObject):
    """Result signals carry the MediaItem object (not an index) so that results arriving
    after the item list was replaced can be recognised as stale by the receiver."""

    # Scan results carry the folder they came from: the scan pool is FIFO with one
    # thread, so opening B while A is still scanning would otherwise bind A's items
    # to B's path.
    scan_finished = Signal(object, object)  # list[MediaItem], Path (scanned folder)
    scan_failed = Signal(str, object)       # error text, Path (scanned folder)
    thumbnail_ready = Signal(object, str)  # MediaItem, cached thumbnail path
    thumbnail_failed = Signal(object, str)
    image_ready = Signal(object, QImage)
    image_failed = Signal(object, str)
    write_finished = Signal(object, str)   # MediaItem, "" on success else error text
    # Root-wide rated collection. Both carry the job itself (job.root is the root it
    # walked), so the receiver can tell a stale/cancelled job's result from the
    # current one's by identity.
    collect_progress = Signal(object, object)  # RatedCollectJob, CollectProgress
    collect_finished = Signal(object, object)  # RatedCollectJob, list[MediaItem] | None (cancelled/failed)


class ScanJob(QRunnable):
    """Lists *folder* and fills in rating/label/EXIF. With *cache*, files already
    known (same mtime/size) are not opened for their rating; *refresh* (F5) reads
    every file again so ratings edited by other tools are picked up."""

    def __init__(self, folder: Path, signals: WorkerSignals, cache: RatingCache | None = None, *, refresh: bool = False):
        super().__init__()
        self.folder, self.signals = folder, signals
        self.cache, self.refresh = cache, refresh

    def run(self) -> None:
        try:
            items = scanner.scan(self.folder)
            for item in items:
                metadata.populate(item, self.cache, refresh=self.refresh)
        except Exception as exc:
            self.signals.scan_failed.emit(f"{self.folder}: {exc}", self.folder)
            return
        self.signals.scan_finished.emit(items, self.folder)


class RatedCollectJob(QRunnable):
    """Walks *root* and gathers every rated (1..5) item -- see core.collection.
    cancel() may be called from any thread; the job then stops at the next folder
    boundary and reports None. Progress emits are throttled so a root with
    thousands of tiny folders does not flood the UI thread."""

    PROGRESS_INTERVAL = 0.25   # seconds between progress emits

    def __init__(self, root: Path, signals: WorkerSignals, cache: RatingCache | None = None, *, refresh: bool = False):
        super().__init__()
        self.root, self.signals = root, signals
        self.cache, self.refresh = cache, refresh
        self._cancel = threading.Event()
        self._last_progress = 0.0

    def cancel(self) -> None:
        self._cancel.set()

    def _on_progress(self, progress: CollectProgress) -> None:
        now = time.monotonic()
        if progress.folders == 1 or now - self._last_progress >= self.PROGRESS_INTERVAL:
            self._last_progress = now
            self.signals.collect_progress.emit(self, progress)

    def run(self) -> None:
        try:
            items = collect_rated(
                self.root,
                is_cancelled=self._cancel.is_set,
                on_progress=self._on_progress,
                cache=self.cache,
                refresh=self.refresh,
            )
        except Exception:
            items = None
        self.signals.collect_finished.emit(self, items)


class ThumbnailJob(QRunnable):
    def __init__(self, item: MediaItem, cache: ThumbnailCache, signals: WorkerSignals):
        super().__init__()
        self.item, self.cache, self.signals = item, cache, signals

    def run(self) -> None:
        try:
            path = self.cache.get_or_create(self.item)
        except ThumbnailError as exc:
            self.signals.thumbnail_failed.emit(self.item, str(exc))
            return
        self.signals.thumbnail_ready.emit(self.item, str(path))


class ImageLoadJob(QRunnable):
    def __init__(self, item: MediaItem, signals: WorkerSignals):
        super().__init__()
        self.item, self.signals = item, signals
        # Set the moment the job leaves the queue. The window cancels queued decodes
        # with QThreadPool.clear() when the user navigates away; a cleared job never
        # runs and never emits, and this flag is how the window tells the two apart.
        self.started = False

    def run(self) -> None:
        self.started = True
        reader = QImageReader(str(self.item.path))
        reader.setAutoTransform(True)  # honour EXIF orientation
        image = reader.read()
        if image.isNull():
            self.signals.image_failed.emit(self.item, reader.errorString() or "decode failed")
            return
        self.signals.image_ready.emit(self.item, image)


class MetadataWriteJob(QRunnable):
    """Writes the rating/label the item had when the job was created. On success
    the rating cache learns the file's new (mtime, size) → rating right away, so
    the viewer's own writes never look like cache misses later."""

    def __init__(self, item: MediaItem, signals: WorkerSignals, cache: RatingCache | None = None):
        super().__init__()
        self.item, self.signals = item, signals
        self.cache = cache
        self.rating: int = item.rating
        self.label: Label = item.label

    def run(self) -> None:
        try:
            metadata.write_rating_label(self.item.path, self.item.kind, self.rating, self.label)
        except metadata.MetadataError as exc:
            self.signals.write_finished.emit(self.item, str(exc))
            return
        if self.cache is not None:
            try:
                st = self.item.path.stat()
            except OSError:
                st = None
            if st is not None:
                self.cache.store(self.item.path, st.st_mtime, st.st_size, self.rating, self.label)
        self.signals.write_finished.emit(self.item, "")
