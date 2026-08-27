from __future__ import annotations

from PySide6.QtCore import QSize, Qt, Signal
from PySide6.QtWidgets import QAbstractItemView, QListView

from ui.thumb_delegate import ThumbDelegate

FILMSTRIP_CELL = QSize(120, 110)
GRID_CELL = QSize(220, 210)


class ThumbListView(QListView):
    """Shared behaviour: icon-mode list of MediaListModel rows drawn by ThumbDelegate.

    The view never takes keyboard focus — MainWindow owns all shortcuts.
    """

    row_activated = Signal(int)
    row_double_clicked = Signal(int)

    def __init__(self, cell: QSize, wrapping: bool, parent=None):
        super().__init__(parent)
        self._cell = QSize(cell)
        self.setViewMode(QListView.ViewMode.IconMode)
        self.setFlow(QListView.Flow.LeftToRight)
        self.setWrapping(wrapping)
        self.setResizeMode(QListView.ResizeMode.Adjust)
        self.setMovement(QListView.Movement.Static)
        self.setUniformItemSizes(True)
        self.setGridSize(self._cell)
        self.setSpacing(2)
        self.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.setItemDelegate(ThumbDelegate(self._cell, self))
        self.clicked.connect(lambda mi: self.row_activated.emit(mi.row()))
        self.doubleClicked.connect(lambda mi: self.row_double_clicked.emit(mi.row()))

    def set_current_row(self, row: int) -> None:
        model = self.model()
        if model is None or not 0 <= row < model.rowCount():
            return
        mi = model.index(row)
        self.setCurrentIndex(mi)
        self.scrollTo(mi, QAbstractItemView.ScrollHint.EnsureVisible)

    def current_row(self) -> int:
        return self.currentIndex().row()

    def visible_rows(self) -> list[int]:
        """Rows whose cell intersects the viewport. Linear scan is fine for a few hundred items."""
        model = self.model()
        if model is None:
            return []
        vp = self.viewport().rect()
        return [row for row in range(model.rowCount()) if self.visualRect(model.index(row)).intersects(vp)]


class Filmstrip(ThumbListView):
    def __init__(self, parent=None):
        super().__init__(FILMSTRIP_CELL, wrapping=False, parent=parent)
        self.setFixedHeight(FILMSTRIP_CELL.height() + 2 * 2 + 16)  # cell + spacing + scrollbar
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)


class GridView(ThumbListView):
    def __init__(self, parent=None):
        super().__init__(GRID_CELL, wrapping=True, parent=parent)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
