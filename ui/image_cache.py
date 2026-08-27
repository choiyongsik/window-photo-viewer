from __future__ import annotations

from collections import OrderedDict

from PySide6.QtGui import QImage


class ImageCache:
    """Small LRU of decoded full-size images keyed by item index."""

    def __init__(self, capacity: int = 6):
        self.capacity = capacity
        self._data: OrderedDict[int, QImage] = OrderedDict()

    def get(self, key: int) -> QImage | None:
        img = self._data.get(key)
        if img is not None:
            self._data.move_to_end(key)
        return img

    def put(self, key: int, image: QImage) -> None:
        self._data[key] = image
        self._data.move_to_end(key)
        while len(self._data) > self.capacity:
            self._data.popitem(last=False)

    def __contains__(self, key: int) -> bool:
        return key in self._data

    def __len__(self) -> int:
        return len(self._data)

    def clear(self) -> None:
        self._data.clear()
