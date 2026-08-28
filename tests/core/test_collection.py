from __future__ import annotations

from pathlib import Path

from core.collection import CollectProgress, collect_rated
from core.metadata import write_rating_label
from core.models import Label, MediaKind
from tests.helpers import make_jpeg


def _rated_tree(root: Path) -> dict[str, Path]:
    """root/ a.jpg(★3)  none.jpg  rej.jpg(-1)  sub/ b.jpg(★1 Red)  sub/deep/ c.jpg(★5)  .hidden/ h.jpg(★4)"""
    files = {
        "a": make_jpeg(root / "a.jpg"),
        "none": make_jpeg(root / "none.jpg"),
        "rej": make_jpeg(root / "rej.jpg"),
        "b": make_jpeg(root / "sub" / "b.jpg"),
        "c": make_jpeg(root / "sub" / "deep" / "c.jpg"),
        "h": make_jpeg(root / ".hidden" / "h.jpg"),
    }
    write_rating_label(files["a"], MediaKind.IMAGE, 3, Label.NONE)
    write_rating_label(files["rej"], MediaKind.IMAGE, -1, Label.NONE)
    write_rating_label(files["b"], MediaKind.IMAGE, 1, Label.RED)
    write_rating_label(files["c"], MediaKind.IMAGE, 5, Label.NONE)
    write_rating_label(files["h"], MediaKind.IMAGE, 4, Label.NONE)
    return files


def test_collect_rated_returns_only_rated_items_across_folders(tmp_path: Path):
    _rated_tree(tmp_path)
    items = collect_rated(tmp_path)
    assert items is not None
    got = {it.path.relative_to(tmp_path).as_posix(): (it.rating, it.label) for it in items}
    assert got == {
        "a.jpg": (3, Label.NONE),
        "sub/b.jpg": (1, Label.RED),
        "sub/deep/c.jpg": (5, Label.NONE),
    }


def test_collect_rated_reads_exif_for_rated_items_only(tmp_path: Path, monkeypatch):
    """EXIF is only needed for items that end up on screen: 3 rated of 5 visible files."""
    import core.collection as collection

    _rated_tree(tmp_path)
    exif_reads: list[Path] = []
    real = collection.metadata.read_exif_summary

    def spy(path: Path):
        exif_reads.append(path)
        return real(path)

    monkeypatch.setattr(collection.metadata, "read_exif_summary", spy)
    items = collect_rated(tmp_path)
    assert items is not None
    assert sorted(exif_reads) == sorted(it.path for it in items)
    assert len(exif_reads) == 3


def test_collect_rated_includes_video_sidecar_ratings(tmp_path: Path):
    clip = tmp_path / "clip.mp4"
    clip.write_bytes(b"\x00" * 16)
    write_rating_label(clip, MediaKind.VIDEO, 2, Label.NONE)
    (tmp_path / "other.mp4").write_bytes(b"\x00" * 16)
    items = collect_rated(tmp_path)
    assert items is not None
    assert [(it.path.name, it.rating, it.kind) for it in items] == [("clip.mp4", 2, MediaKind.VIDEO)]


def test_collect_rated_reports_progress_per_folder(tmp_path: Path):
    _rated_tree(tmp_path)
    seen: list[CollectProgress] = []
    collect_rated(tmp_path, on_progress=seen.append)
    assert len(seen) == 3            # root, sub, sub/deep  (.hidden pruned)
    assert seen[-1] == CollectProgress(folders=3, files=5, rated=3)


def test_collect_rated_cancelled_returns_none(tmp_path: Path):
    _rated_tree(tmp_path)
    calls = 0

    def cancelled() -> bool:
        nonlocal calls
        calls += 1
        return calls > 1   # let the first folder through, then cancel

    assert collect_rated(tmp_path, is_cancelled=cancelled) is None


def test_collect_rated_empty_root(tmp_path: Path):
    assert collect_rated(tmp_path) == []


# ---------- rating cache integration ----------

def _spy_reads(monkeypatch) -> list[Path]:
    import core.collection as collection

    reads: list[Path] = []
    real = collection.metadata.read_rating_label

    def spy(path: Path, kind):
        reads.append(path)
        return real(path, kind)

    monkeypatch.setattr(collection.metadata, "read_rating_label", spy)
    return reads


def test_collect_rated_with_cache_skips_files_seen_before(tmp_path: Path, monkeypatch):
    from core.rating_cache import RatingCache

    _rated_tree(tmp_path)
    cache = RatingCache(tmp_path / "c.json")
    reads = _spy_reads(monkeypatch)

    first = collect_rated(tmp_path, cache=cache)
    assert len(reads) == 5           # every visible file read once, rated or not
    reads.clear()

    second = collect_rated(tmp_path, cache=cache)
    assert reads == []               # all served from the cache
    assert [it.path for it in second] == [it.path for it in first]
    assert [(it.rating, it.label) for it in second] == [(it.rating, it.label) for it in first]


def test_collect_rated_with_cache_rereads_a_changed_file(tmp_path: Path, monkeypatch):
    import os
    import time

    from core.rating_cache import RatingCache

    files = _rated_tree(tmp_path)
    cache = RatingCache(tmp_path / "c.json")
    collect_rated(tmp_path, cache=cache)

    write_rating_label(files["none"], MediaKind.IMAGE, 4, Label.NONE)   # mtime/size change
    # Guarantee a visible mtime difference even on coarse filesystems.
    os.utime(files["none"], ns=(time.time_ns(), time.time_ns()))
    reads = _spy_reads(monkeypatch)

    items = collect_rated(tmp_path, cache=cache)
    assert reads == [files["none"]]
    assert files["none"] in [it.path for it in items]


def test_collect_rated_refresh_ignores_cache_but_refills_it(tmp_path: Path, monkeypatch):
    from core.rating_cache import RatingCache

    _rated_tree(tmp_path)
    cache = RatingCache(tmp_path / "c.json")
    collect_rated(tmp_path, cache=cache)
    reads = _spy_reads(monkeypatch)

    collect_rated(tmp_path, cache=cache, refresh=True)
    assert len(reads) == 5
    reads.clear()
    collect_rated(tmp_path, cache=cache)
    assert reads == []


def test_collect_rated_prunes_cache_entries_for_deleted_files(tmp_path: Path):
    from core.rating_cache import RatingCache

    files = _rated_tree(tmp_path)
    cache = RatingCache(tmp_path / "c.json")
    collect_rated(tmp_path, cache=cache)
    st = files["b"].stat()
    files["b"].unlink()

    collect_rated(tmp_path, cache=cache)
    assert cache.lookup(files["b"], st.st_mtime, st.st_size) is None
