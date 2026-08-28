from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QThreadPool

from core.metadata import MetadataError
from core.models import Label, MediaItem, MediaKind
from core.thumbnails import ThumbnailCache
from tests.helpers import make_jpeg
from ui import workers
from ui.workers import ImageLoadJob, MetadataWriteJob, RatedCollectJob, ScanJob, ThumbnailJob, WorkerSignals


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
    items, folder = blocker.args
    assert [i.path.name for i in items] == ["a.jpg", "b.jpg"]
    assert items[0].rating == 0
    assert folder == tmp_path        # results identify the folder they were scanned from


def test_scan_job_failure_emits_message(qtbot, tmp_path: Path):
    signals = WorkerSignals()
    with qtbot.waitSignal(signals.scan_failed, timeout=5000) as blocker:
        _pool().start(ScanJob(tmp_path / "missing", signals))
    message, folder = blocker.args
    assert "missing" in message
    assert folder == tmp_path / "missing"


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
    job = ImageLoadJob(item, signals)
    assert job.started is False          # only true once the pool actually runs it
    with qtbot.waitSignal(signals.image_ready, timeout=5000) as blocker:
        _pool().start(job)
    got, image = blocker.args
    assert got is item and (image.width(), image.height()) == (20, 40)
    assert job.started is True


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


# ---------------- RatedCollectJob ----------------

def test_rated_collect_job_emits_rated_items_with_root(qtbot, tmp_path: Path):
    from core.metadata import write_rating_label

    rated = make_jpeg(tmp_path / "sub" / "star.jpg")
    write_rating_label(rated, MediaKind.IMAGE, 2, Label.NONE)
    make_jpeg(tmp_path / "plain.jpg")

    signals = WorkerSignals()
    with qtbot.waitSignal(signals.collect_finished, timeout=5000) as blocker:
        _pool().start(RatedCollectJob(tmp_path, signals))
    job, items = blocker.args
    assert job.root == tmp_path
    assert [it.path for it in items] == [rated]
    assert items[0].rating == 2


def test_rated_collect_job_reports_progress(qtbot, tmp_path: Path):
    make_jpeg(tmp_path / "a.jpg")
    signals = WorkerSignals()
    seen = []
    signals.collect_progress.connect(lambda j, p: seen.append((j, p)))
    with qtbot.waitSignal(signals.collect_finished, timeout=5000):
        _pool().start(RatedCollectJob(tmp_path, signals))
    # progress is throttled, but the first folder always reports
    qtbot.waitUntil(lambda: len(seen) >= 1, timeout=2000)
    job, progress = seen[0]
    assert job.root == tmp_path
    assert progress.folders >= 1


def test_rated_collect_job_cancel_emits_none(qtbot, tmp_path: Path):
    make_jpeg(tmp_path / "a.jpg")
    signals = WorkerSignals()
    job = RatedCollectJob(tmp_path, signals)
    job.cancel()   # cancelled before it even starts
    with qtbot.waitSignal(signals.collect_finished, timeout=5000) as blocker:
        _pool().start(job)
    assert blocker.args == [job, None]


# ---------- rating cache pass-through ----------

def test_scan_job_fills_the_rating_cache(qtbot, tmp_path: Path):
    from core.rating_cache import RatingCache

    make_jpeg(tmp_path / "a.jpg")
    make_jpeg(tmp_path / "b.jpg")
    cache = RatingCache(tmp_path / "c.json")
    signals = WorkerSignals()
    with qtbot.waitSignal(signals.scan_finished, timeout=5000) as blocker:
        _pool().start(ScanJob(tmp_path, signals, cache=cache))
    items, _ = blocker.args
    assert len(cache) == 2
    assert cache.lookup(items[0].path, items[0].mtime, items[0].size) == (0, Label.NONE)


def test_scan_job_refresh_overrides_a_stale_cache_entry(qtbot, tmp_path: Path):
    from core.rating_cache import RatingCache

    p = make_jpeg(tmp_path / "a.jpg")
    st = p.stat()
    cache = RatingCache(tmp_path / "c.json")
    cache.store(p, st.st_mtime, st.st_size, 4, Label.NONE)   # stale lie
    signals = WorkerSignals()

    with qtbot.waitSignal(signals.scan_finished, timeout=5000) as blocker:
        _pool().start(ScanJob(tmp_path, signals, cache=cache))
    assert blocker.args[0][0].rating == 4                      # trusted
    with qtbot.waitSignal(signals.scan_finished, timeout=5000) as blocker:
        _pool().start(ScanJob(tmp_path, signals, cache=cache, refresh=True))
    assert blocker.args[0][0].rating == 0                      # re-read
    assert cache.lookup(p, st.st_mtime, st.st_size) == (0, Label.NONE)


def test_rated_collect_job_uses_and_fills_the_cache(qtbot, tmp_path: Path):
    from core.rating_cache import RatingCache

    make_jpeg(tmp_path / "a.jpg")
    make_jpeg(tmp_path / "sub" / "b.jpg")
    cache = RatingCache(tmp_path / "c.json")
    signals = WorkerSignals()
    with qtbot.waitSignal(signals.collect_finished, timeout=5000):
        _pool().start(RatedCollectJob(tmp_path, signals, cache=cache))
    assert len(cache) == 2
    assert (tmp_path / "c.json").exists()                       # a finished walk persists the cache


def test_metadata_write_job_records_the_new_rating_in_the_cache(qtbot, tmp_path: Path):
    from core.rating_cache import RatingCache

    item = _item(make_jpeg(tmp_path / "a.jpg"))
    item.rating = 3
    cache = RatingCache(tmp_path / "c.json")
    signals = WorkerSignals()
    with qtbot.waitSignal(signals.write_finished, timeout=5000) as blocker:
        _pool().start(MetadataWriteJob(item, signals, cache=cache))
    assert blocker.args == [item, ""]
    st = item.path.stat()                                       # the file changed under the write
    assert cache.lookup(item.path, st.st_mtime, st.st_size) == (3, Label.NONE)


def test_metadata_write_job_failure_leaves_the_cache_alone(qtbot, tmp_path: Path, monkeypatch):
    from core.rating_cache import RatingCache

    item = _item(make_jpeg(tmp_path / "a.jpg"))
    item.rating = 3
    cache = RatingCache(tmp_path / "c.json")
    monkeypatch.setattr(workers.metadata, "write_rating_label",
                        lambda *a, **k: (_ for _ in ()).throw(MetadataError("disk full")))
    signals = WorkerSignals()
    with qtbot.waitSignal(signals.write_finished, timeout=5000):
        _pool().start(MetadataWriteJob(item, signals, cache=cache))
    assert len(cache) == 0
