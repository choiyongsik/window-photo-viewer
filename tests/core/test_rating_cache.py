from __future__ import annotations

import json
import threading
from pathlib import Path

from core.models import Label
from core.rating_cache import RatingCache


def _cache(tmp_path: Path) -> RatingCache:
    return RatingCache(tmp_path / "cache" / "ratings.json")


def test_lookup_miss_on_empty_cache(tmp_path: Path):
    c = _cache(tmp_path)
    assert c.lookup(tmp_path / "a.jpg", 1.0, 10) is None


def test_store_then_lookup_hits_on_same_mtime_and_size(tmp_path: Path):
    c = _cache(tmp_path)
    p = tmp_path / "a.jpg"
    c.store(p, 1.5, 10, 3, Label.RED)
    assert c.lookup(p, 1.5, 10) == (3, Label.RED)


def test_lookup_misses_when_mtime_or_size_changed(tmp_path: Path):
    c = _cache(tmp_path)
    p = tmp_path / "a.jpg"
    c.store(p, 1.5, 10, 3, Label.NONE)
    assert c.lookup(p, 2.5, 10) is None
    assert c.lookup(p, 1.5, 11) is None


def test_unrated_files_are_cached_too(tmp_path: Path):
    """The whole point is not re-opening files that turned out unrated."""
    c = _cache(tmp_path)
    p = tmp_path / "a.jpg"
    c.store(p, 1.0, 10, 0, Label.NONE)
    assert c.lookup(p, 1.0, 10) == (0, Label.NONE)


def test_save_and_reload_roundtrip(tmp_path: Path):
    c = _cache(tmp_path)
    p = tmp_path / "a.jpg"
    c.store(p, 1.25, 10, -1, Label.BLUE)
    c.save()

    again = _cache(tmp_path)
    assert again.lookup(p, 1.25, 10) == (-1, Label.BLUE)


def test_save_only_writes_when_dirty(tmp_path: Path):
    c = _cache(tmp_path)
    c.save()
    assert not (tmp_path / "cache" / "ratings.json").exists()   # nothing to save
    c.store(tmp_path / "a.jpg", 1.0, 1, 1, Label.NONE)
    c.save()
    mtime = (tmp_path / "cache" / "ratings.json").stat().st_mtime_ns
    c.save()   # clean: must not rewrite
    assert (tmp_path / "cache" / "ratings.json").stat().st_mtime_ns == mtime


def test_corrupt_file_is_treated_as_empty(tmp_path: Path):
    f = tmp_path / "cache" / "ratings.json"
    f.parent.mkdir(parents=True)
    f.write_text("{not json", encoding="utf-8")
    c = RatingCache(f)
    assert c.lookup(tmp_path / "a.jpg", 1.0, 1) is None
    c.store(tmp_path / "a.jpg", 1.0, 1, 2, Label.NONE)
    c.save()
    assert json.loads(f.read_text(encoding="utf-8"))   # overwritten with valid content


def test_retain_under_root_drops_entries_for_vanished_files(tmp_path: Path):
    c = _cache(tmp_path)
    root = tmp_path / "root"
    kept, gone, other = root / "a.jpg", root / "sub" / "b.jpg", tmp_path / "elsewhere" / "c.jpg"
    for p in (kept, gone, other):
        c.store(p, 1.0, 1, 1, Label.NONE)

    c.retain_under(root, {kept})

    assert c.lookup(kept, 1.0, 1) == (1, Label.NONE)
    assert c.lookup(gone, 1.0, 1) is None
    assert c.lookup(other, 1.0, 1) == (1, Label.NONE)   # outside root: untouched


def test_concurrent_store_from_threads_is_safe(tmp_path: Path):
    c = _cache(tmp_path)

    def worker(n: int) -> None:
        for i in range(200):
            c.store(tmp_path / f"{n}_{i}.jpg", 1.0, 1, i % 6, Label.NONE)

    threads = [threading.Thread(target=worker, args=(n,)) for n in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert c.lookup(tmp_path / "3_199.jpg", 1.0, 1) == (199 % 6, Label.NONE)
    assert len(c) == 800
