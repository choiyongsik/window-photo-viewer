"""QThreadPool jobs. Each job reports through a shared WorkerSignals instance
(signals emitted from a worker thread are queued to the receiver's thread by Qt)."""
from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QObject, QRunnable, Signal
from PySide6.QtGui import QImage, QImageReader

from core import metadata, scanner
from core.models import Label, MediaItem, MediaKind
from core.thumbnails import ThumbnailCache, ThumbnailError


class WorkerSignals(QObject):
    """Result signals carry the MediaItem object (not an index) so that results arriving
    after the item list was replaced can be recognised as stale by the receiver."""

    scan_finished = Signal(object)         # list[MediaItem]
    scan_failed = Signal(str)
    thumbnail_ready = Signal(object, str)  # MediaItem, cached thumbnail path
    thumbnail_failed = Signal(object, str)
    image_ready = Signal(object, QImage)
    image_failed = Signal(object, str)
    write_finished = Signal(object, str)   # MediaItem, "" on success else error text


class ScanJob(QRunnable):
    def __init__(self, folder: Path, signals: WorkerSignals):
        super().__init__()
        self.folder, self.signals = folder, signals

    def run(self) -> None:
        try:
            items = scanner.scan(self.folder)
            for item in items:
                metadata.populate(item)
        except Exception as exc:
            self.signals.scan_failed.emit(f"{self.folder}: {exc}")
            return
        self.signals.scan_finished.emit(items)


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

    def run(self) -> None:
        reader = QImageReader(str(self.item.path))
        reader.setAutoTransform(True)  # honour EXIF orientation
        image = reader.read()
        if image.isNull():
            self.signals.image_failed.emit(self.item, reader.errorString() or "decode failed")
            return
        self.signals.image_ready.emit(self.item, image)


class MetadataWriteJob(QRunnable):
    """Writes the rating/label the item had when the job was created."""

    def __init__(self, item: MediaItem, signals: WorkerSignals):
        super().__init__()
        self.item, self.signals = item, signals
        self.rating: int = item.rating
        self.label: Label = item.label

    def run(self) -> None:
        try:
            metadata.write_rating_label(self.item.path, self.item.kind, self.rating, self.label)
        except metadata.MetadataError as exc:
            self.signals.write_finished.emit(self.item, str(exc))
            return
        self.signals.write_finished.emit(self.item, "")
