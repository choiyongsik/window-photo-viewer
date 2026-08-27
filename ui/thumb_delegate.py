from __future__ import annotations

from PySide6.QtCore import QModelIndex, QRect, QSize, Qt
from PySide6.QtGui import QColor, QFont, QPainter, QPen, QPixmap
from PySide6.QtWidgets import QStyle, QStyledItemDelegate, QStyleOptionViewItem

from core.models import Label, MediaItem, MediaKind
from ui.media_list_model import MediaListModel

LABEL_COLORS: dict[Label, QColor] = {
    Label.RED: QColor("#e5484d"),
    Label.YELLOW: QColor("#f5d90a"),
    Label.GREEN: QColor("#46a758"),
    Label.BLUE: QColor("#0090ff"),
}
_BG = QColor("#202020")
_BG_SELECTED = QColor("#3a5f8f")
_PLACEHOLDER = QColor("#3c3c3c")
_TEXT = QColor("#e0e0e0")
_STAR = QColor("#ffcc33")
_REJECT = QColor("#8a8a8a")
_ERROR = QColor("#ff4040")
_FOOTER_H = 18
_BAR_H = 4
_PAD = 4


class ThumbDelegate(QStyledItemDelegate):
    def __init__(self, cell: QSize, parent=None):
        super().__init__(parent)
        self._cell = QSize(cell)

    def sizeHint(self, option: QStyleOptionViewItem, index: QModelIndex) -> QSize:  # noqa: N802
        return QSize(self._cell)

    def paint(self, painter: QPainter, option: QStyleOptionViewItem, index: QModelIndex) -> None:
        item: MediaItem = index.data(MediaListModel.ItemRole)
        pixmap: QPixmap | None = index.data(Qt.ItemDataRole.DecorationRole)
        failed: bool = bool(index.data(MediaListModel.FailedRole))
        rect: QRect = option.rect

        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        selected = bool(option.state & QStyle.StateFlag.State_Selected)
        painter.fillRect(rect, _BG_SELECTED if selected else _BG)

        # label bar across the top
        if item.label in LABEL_COLORS:
            painter.fillRect(QRect(rect.left(), rect.top(), rect.width(), _BAR_H), LABEL_COLORS[item.label])

        image_area = QRect(rect.left() + _PAD, rect.top() + _BAR_H + _PAD,
                           rect.width() - 2 * _PAD, rect.height() - _BAR_H - _FOOTER_H - 2 * _PAD)
        if pixmap is not None and not pixmap.isNull():
            scaled = pixmap.scaled(image_area.size(), Qt.AspectRatioMode.KeepAspectRatio,
                                   Qt.TransformationMode.SmoothTransformation)
            target = QRect(0, 0, scaled.width(), scaled.height())
            target.moveCenter(image_area.center())
            painter.drawPixmap(target, scaled)
        else:
            painter.fillRect(image_area, _PLACEHOLDER)
            painter.setPen(_TEXT)
            painter.drawText(image_area, Qt.AlignmentFlag.AlignCenter, "✗" if failed else "…")

        # overlays
        small = QFont(option.font)
        small.setPointSizeF(max(7.0, option.font.pointSizeF() - 1))
        painter.setFont(small)
        stars_rect = QRect(image_area.left() + 2, image_area.top() + 2, image_area.width() - 4, 14)
        if item.is_rejected:
            painter.setPen(_REJECT)
            painter.drawText(stars_rect, Qt.AlignmentFlag.AlignLeft, "✕ reject")
        elif item.rating > 0:
            painter.setPen(_STAR)
            painter.drawText(stars_rect, Qt.AlignmentFlag.AlignLeft, "★" * item.rating)
        if item.kind is MediaKind.VIDEO:
            painter.setPen(_TEXT)
            painter.drawText(stars_rect, Qt.AlignmentFlag.AlignRight, "▶")
        if item.write_error:
            painter.setPen(QPen(_ERROR))
            bold = QFont(small)
            bold.setBold(True)
            painter.setFont(bold)
            painter.drawText(QRect(image_area.right() - 14, image_area.bottom() - 14, 14, 14),
                             Qt.AlignmentFlag.AlignCenter, "!")
            painter.setFont(small)

        # footer: file name
        painter.setPen(_TEXT)
        footer = QRect(rect.left() + _PAD, rect.bottom() - _FOOTER_H, rect.width() - 2 * _PAD, _FOOTER_H)
        name = option.fontMetrics.elidedText(item.path.name, Qt.TextElideMode.ElideMiddle, footer.width())
        painter.drawText(footer, Qt.AlignmentFlag.AlignCenter, name)
        painter.restore()
