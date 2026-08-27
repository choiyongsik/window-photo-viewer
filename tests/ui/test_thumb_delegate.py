from pathlib import Path

from PySide6.QtCore import QRect, QSize, Qt
from PySide6.QtGui import QColor, QImage, QPainter, QPixmap
from PySide6.QtWidgets import QStyleOptionViewItem

from core.models import Label, MediaItem, MediaKind
from ui.media_list_model import MediaListModel
from ui.thumb_delegate import LABEL_COLORS, ThumbDelegate

CELL = QSize(120, 110)


def _render(model: MediaListModel, row: int) -> QImage:
    delegate = ThumbDelegate(CELL)
    image = QImage(CELL, QImage.Format.Format_ARGB32)
    image.fill(Qt.GlobalColor.black)
    option = QStyleOptionViewItem()
    option.rect = QRect(0, 0, CELL.width(), CELL.height())
    painter = QPainter(image)
    delegate.paint(painter, option, model.index(row))
    painter.end()
    return image


def _model(item: MediaItem) -> MediaListModel:
    m = MediaListModel()
    m.set_items([item])
    return m


def test_size_hint_is_cell(qtbot):
    d = ThumbDelegate(CELL)
    m = _model(MediaItem(Path("a.jpg"), MediaKind.IMAGE, 0, 1))
    assert d.sizeHint(QStyleOptionViewItem(), m.index(0)) == CELL


def test_paints_label_bar_in_label_color(qtbot):
    m = _model(MediaItem(Path("a.jpg"), MediaKind.IMAGE, 0, 1, rating=2, label=Label.RED))
    img = _render(m, 0)
    assert QColor(img.pixel(CELL.width() // 2, 1)) == LABEL_COLORS[Label.RED]


def test_paints_without_thumbnail_with_thumbnail_and_failed(qtbot):
    item = MediaItem(Path("a.jpg"), MediaKind.VIDEO, 0, 1, rating=-1, write_error="locked")
    m = _model(item)
    _render(m, 0)                       # placeholder path must not raise
    pm = QPixmap(300, 100)
    pm.fill(Qt.GlobalColor.white)
    m.set_thumbnail(0, pm)
    img = _render(m, 0)
    # pixmap scaled to fit width keeps aspect: white band appears mid-cell
    assert QColor(img.pixel(CELL.width() // 2, 40)).lightness() > 200
    m.set_thumbnail_failed(0)
    _render(m, 0)                       # failed placeholder must not raise
