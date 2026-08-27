from PySide6.QtGui import QImage

from ui.image_cache import ImageCache


def _img(w: int = 2) -> QImage:
    return QImage(w, 1, QImage.Format.Format_RGB32)


def test_put_get_and_capacity_evicts_least_recent():
    cache = ImageCache(capacity=2)
    cache.put(1, _img())
    cache.put(2, _img())
    assert cache.get(1) is not None      # touches 1 → 2 is now least recent
    cache.put(3, _img())
    assert 2 not in cache
    assert 1 in cache and 3 in cache
    assert len(cache) == 2


def test_get_missing_returns_none_and_clear():
    cache = ImageCache()
    assert cache.get(9) is None
    cache.put(1, _img())
    cache.clear()
    assert len(cache) == 0


def test_put_existing_key_replaces_and_refreshes():
    cache = ImageCache(capacity=2)
    cache.put(1, _img(1))
    cache.put(2, _img())
    cache.put(1, _img(5))
    cache.put(3, _img())
    assert 2 not in cache
    assert cache.get(1).width() == 5
