from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QThreadPool

from core.metadata import MetadataError
from core.models import Label, MediaItem, MediaKind
from core.thumbnails import ThumbnailCache
from tests.helpers import make_jpeg
from ui import workers
from ui.workers import ImageLoadJob, MetadataWriteJob, ScanJob, ThumbnailJob, WorkerSignals


def _pool() -> QThreadPool:
    return QThreadPool.globalInstance()


def _item(path: Path) -> MediaItem:
    st = path.stat()
    return MediaItem(path=path, kind=MediaKind.IMAGE, mtime=st.st_mtime, size=st.st_size)


def test_scan_job_emits_populated_items(qtbot, tmp_path: Path):
    make_jpeg(tmp_path / "b.jpg")
    make_jpeg(tmp_path / "a.jpg")
    signals = WorkerSignals()
    with qtbot.waitSignal(signals.scan_finished, timeout=5000) as blocker:
        _pool().start(ScanJob(tmp_path, signals))
    items = blocker.args[0]
    assert [i.path.name for i in items] == ["a.jpg", "b.jpg"]
    assert items[0].rating == 0


def test_scan_job_failure_emits_message(qtbot, tmp_path: Path):
    signals = WorkerSignals()
    with qtbot.waitSignal(signals.scan_failed, timeout=5000) as blocker:
        _pool().start(ScanJob(tmp_path / "missing", signals))
    assert "missing" in blocker.args[0]


def test_thumbnail_job_ready_and_failed(qtbot, tmp_path: Path):
    cache = ThumbnailCache(tmp_path / "cache")
    good = _item(make_jpeg(tmp_path / "a.jpg"))
    signals = WorkerSignals()
    with qtbot.waitSignal(signals.thumbnail_ready, timeout=5000) as blocker:
        _pool().start(ThumbnailJob(good, cache, signals))
    assert blocker.args[0] is good and Path(blocker.args[1]).exists()

    bad_path = tmp_path / "bad.jpg"
    bad_path.write_bytes(b"nope")
    bad = _item(bad_path)
    with qtbot.waitSignal(signals.thumbnail_failed, timeout=5000) as blocker:
        _pool().start(ThumbnailJob(bad, cache, signals))
    assert blocker.args[0] is bad


def test_image_load_job_applies_orientation(qtbot, tmp_path: Path):
    item = _item(make_jpeg(tmp_path / "a.jpg", size=(40, 20), orientation=6))
    signals = WorkerSignals()
    with qtbot.waitSignal(signals.image_ready, timeout=5000) as blocker:
        _pool().start(ImageLoadJob(item, signals))
    got, image = blocker.args
    assert got is item and (image.width(), image.height()) == (20, 40)


def test_image_load_job_failure(qtbot, tmp_path: Path):
    p = tmp_path / "bad.jpg"
    p.write_bytes(b"nope")
    item = _item(p)
    signals = WorkerSignals()
    with qtbot.waitSignal(signals.image_failed, timeout=5000) as blocker:
        _pool().start(ImageLoadJob(item, signals))
    assert blocker.args[0] is item


def test_metadata_write_job_success_and_error(qtbot, tmp_path: Path, monkeypatch):
    item = _item(make_jpeg(tmp_path / "a.jpg"))
    item.rating, item.label = 4, Label.RED
    signals = WorkerSignals()
    with qtbot.waitSignal(signals.write_finished, timeout=5000) as blocker:
        _pool().start(MetadataWriteJob(item, signals))
    assert blocker.args[0] is item and blocker.args[1] == ""
    assert workers.metadata.read_rating_label(item.path, MediaKind.IMAGE) == (4, Label.RED)

    def boom(*a, **k):
        raise MetadataError("locked")

    monkeypatch.setattr(workers.metadata, "write_rating_label", boom)
    with qtbot.waitSignal(signals.write_finished, timeout=5000) as blocker:
        _pool().start(MetadataWriteJob(item, signals))
    assert blocker.args[0] is item and blocker.args[1] == "locked"


def test_metadata_write_job_snapshots_values_at_dispatch(qtbot, tmp_path: Path, monkeypatch):
    seen: list[tuple[int, Label]] = []

    def spy(path, kind, rating, label):
        seen.append((rating, label))

    monkeypatch.setattr(workers.metadata, "write_rating_label", spy)
    item = _item(make_jpeg(tmp_path / "a.jpg"))
    item.rating = 2
    job = MetadataWriteJob(item, WorkerSignals())
    item.rating = 5  # changed after dispatch — job must still write 2
    signals = job.signals
    with qtbot.waitSignal(signals.write_finished, timeout=5000):
        _pool().start(job)
    assert seen == [(2, Label.NONE)]
