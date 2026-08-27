from __future__ import annotations

from PySide6.QtCore import QAbstractListModel, QModelIndex, Qt
from PySide6.QtGui import QPixmap

from core.filters import NO_FILTER, Filter
from core.models import MediaItem


class MediaListModel(QAbstractListModel):
    ItemRole = Qt.ItemDataRole.UserRole + 1
    IndexRole = Qt.ItemDataRole.UserRole + 2
    FailedRole = Qt.ItemDataRole.UserRole + 3

    def __init__(self, parent=None):
        super().__init__(parent)
        self._items: list[MediaItem] = []
        self._filter: Filter = NO_FILTER
        self._visible: list[int] = []
        self._thumbs: dict[int, QPixmap] = {}
        self._failed: set[int] = set()
        self._requested: set[int] = set()

    # ---- items / filter ----
    def set_items(self, items: list[MediaItem]) -> None:
        self.beginResetModel()
        self._items = list(items)
        self._thumbs.clear()
        self._failed.clear()
        self._requested.clear()
        self._visible = self._filter.apply(self._items)
        self.endResetModel()

    def items(self) -> list[MediaItem]:
        return self._items

    def set_filter(self, f: Filter) -> None:
        self._filter = f
        self.refresh_filter()

    def filter(self) -> Filter:
        return self._filter

    def refresh_filter(self) -> None:
        self.beginResetModel()
        self._visible = self._filter.apply(self._items)
        self.endResetModel()

    def visible_indices(self) -> list[int]:
        return list(self._visible)

    def row_for_item_index(self, idx: int) -> int:
        try:
            return self._visible.index(idx)
        except ValueError:
            return -1

    def item_index_at_row(self, row: int) -> int:
        return self._visible[row]

    def item_at_row(self, row: int) -> MediaItem:
        return self._items[self._visible[row]]

    # ---- thumbnails ----
    def set_thumbnail(self, idx: int, pixmap: QPixmap) -> None:
        self._thumbs[idx] = pixmap
        self._failed.discard(idx)
        self.item_changed(idx)

    def set_thumbnail_failed(self, idx: int) -> None:
        self._failed.add(idx)
        self.item_changed(idx)

    def thumbnail(self, idx: int) -> QPixmap | None:
        return self._thumbs.get(idx)

    def has_thumbnail_request(self, idx: int) -> bool:
        return idx in self._requested

    def mark_requested(self, idx: int) -> None:
        self._requested.add(idx)

    def item_changed(self, idx: int) -> None:
        row = self.row_for_item_index(idx)
        if row >= 0:
            mi = self.index(row)
            self.dataChanged.emit(mi, mi)

    # ---- Qt model API ----
    def rowCount(self, parent: QModelIndex = QModelIndex()) -> int:  # noqa: N802
        return 0 if parent.isValid() else len(self._visible)

    def data(self, index: QModelIndex, role: int = Qt.ItemDataRole.DisplayRole):
        if not index.isValid() or not 0 <= index.row() < len(self._visible):
            return None
        idx = self._visible[index.row()]
        item = self._items[idx]
        if role == Qt.ItemDataRole.DisplayRole:
            return item.path.name
        if role == Qt.ItemDataRole.DecorationRole:
            return self._thumbs.get(idx)
        if role == self.ItemRole:
            return item
        if role == self.IndexRole:
            return idx
        if role == self.FailedRole:
            return idx in self._failed
        return None
