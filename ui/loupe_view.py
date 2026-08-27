from __future__ import annotations

from PySide6.QtCore import QPoint, QPointF, Qt
from PySide6.QtGui import QColor, QImage, QMouseEvent, QPainter, QPixmap, QResizeEvent, QTransform
from PySide6.QtWidgets import QFrame, QGraphicsPixmapItem, QGraphicsScene, QGraphicsSimpleTextItem, QGraphicsView

_CLICK_TOLERANCE_PX = 4


class LoupeView(QGraphicsView):
    """Single-image view. Two states only: fit-to-window (no upscaling) and 100%."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._scene = QGraphicsScene(self)
        self.setScene(self._scene)
        self._pix_item = QGraphicsPixmapItem()
        self._pix_item.setTransformationMode(Qt.TransformationMode.SmoothTransformation)
        self._text_item = QGraphicsSimpleTextItem()
        self._text_item.setBrush(QColor("#c0c0c0"))
        self._scene.addItem(self._pix_item)
        self._scene.addItem(self._text_item)
        self._fit = True
        self._has_image = False
        self._press_pos: QPoint | None = None

        self.setBackgroundBrush(QColor("#141414"))
        self.setFrameShape(QFrame.Shape.NoFrame)
        self.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setTransformationAnchor(QGraphicsView.ViewportAnchor.AnchorUnderMouse)
        self.setResizeAnchor(QGraphicsView.ViewportAnchor.AnchorViewCenter)
        self.setDragMode(QGraphicsView.DragMode.NoDrag)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)

    # ---- content ----
    @property
    def has_image(self) -> bool:
        return self._has_image

    @property
    def is_fit(self) -> bool:
        return self._fit

    def set_image(self, image: QImage) -> None:
        self._text_item.setVisible(False)
        self._pix_item.setPixmap(QPixmap.fromImage(image))
        self._pix_item.setVisible(True)
        self._scene.setSceneRect(self._pix_item.boundingRect())
        self._has_image = True
        self.fit()

    def set_placeholder(self, text: str) -> None:
        self._pix_item.setVisible(False)
        self._pix_item.setPixmap(QPixmap())
        self._has_image = False
        self._text_item.setText(text)
        self._text_item.setVisible(True)
        self._scene.setSceneRect(self._text_item.boundingRect())
        self.resetTransform()
        self.centerOn(self._text_item)
        self._fit = True
        self.setDragMode(QGraphicsView.DragMode.NoDrag)

    # ---- zoom ----
    def current_scale(self) -> float:
        return self.transform().m11()

    def fit(self) -> None:
        self._fit = True
        self.setDragMode(QGraphicsView.DragMode.NoDrag)
        if not self._has_image:
            self.resetTransform()
            self.centerOn(self._text_item)
            return
        br = self._pix_item.boundingRect()
        vp = self.viewport().rect()
        if br.width() <= 0 or br.height() <= 0:
            return
        if vp.width() <= 0 or vp.height() <= 0:
            # Viewport is momentarily collapsed (e.g. a QSplitter pane at 0
            # width). Leave the transform as-is; the pending resizeEvent
            # will call fit() again once the viewport has a real size.
            return
        scale = min(vp.width() / br.width(), vp.height() / br.height(), 1.0)
        self.setTransform(QTransform.fromScale(scale, scale))
        self.centerOn(self._pix_item)

    def zoom_100(self, anchor: QPoint | None = None) -> None:
        if not self._has_image:
            return
        target: QPointF = self.mapToScene(anchor) if anchor is not None else self._pix_item.boundingRect().center()
        self._fit = False
        self.setTransform(QTransform())
        self.centerOn(target)
        self.setDragMode(QGraphicsView.DragMode.ScrollHandDrag)

    def toggle_zoom(self, anchor: QPoint | None = None) -> None:
        if not self._has_image:
            return
        if self._fit:
            self.zoom_100(anchor)
        else:
            self.fit()

    # ---- events ----
    def resizeEvent(self, event: QResizeEvent) -> None:  # noqa: N802
        super().resizeEvent(event)
        if self._fit:
            self.fit()

    def mousePressEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        if event.button() == Qt.MouseButton.LeftButton:
            self._press_pos = event.position().toPoint()
        super().mousePressEvent(event)

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        super().mouseReleaseEvent(event)
        if event.button() == Qt.MouseButton.LeftButton and self._press_pos is not None:
            moved = (event.position().toPoint() - self._press_pos).manhattanLength()
            if moved <= _CLICK_TOLERANCE_PX:
                self.toggle_zoom(event.position().toPoint())
        self._press_pos = None
