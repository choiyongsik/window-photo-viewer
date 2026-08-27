import pytest
from PySide6.QtCore import QPoint, Qt
from PySide6.QtGui import QImage
from PySide6.QtWidgets import QGraphicsView

from ui.loupe_view import LoupeView


def _img(w: int, h: int) -> QImage:
    im = QImage(w, h, QImage.Format.Format_RGB32)
    im.fill(Qt.GlobalColor.gray)
    return im


@pytest.fixture
def view(qtbot):
    v = LoupeView()
    qtbot.addWidget(v)
    v.resize(400, 300)
    v.show()
    qtbot.waitExposed(v)
    return v


def test_fit_scales_down_to_viewport(view):
    view.set_image(_img(2000, 1000))
    assert view.has_image and view.is_fit
    assert view.current_scale() == pytest.approx(400 / 2000, rel=0.05)


def test_fit_never_upscales(view):
    view.set_image(_img(100, 50))
    assert view.current_scale() == pytest.approx(1.0)


def test_toggle_zoom_switches_between_fit_and_100(view):
    view.set_image(_img(2000, 1000))
    view.toggle_zoom(QPoint(10, 10))
    assert not view.is_fit
    assert view.current_scale() == pytest.approx(1.0)
    assert view.dragMode() == QGraphicsView.DragMode.ScrollHandDrag
    view.toggle_zoom()
    assert view.is_fit
    assert view.dragMode() == QGraphicsView.DragMode.NoDrag


def test_click_toggles_but_drag_does_not(view, qtbot):
    view.set_image(_img(2000, 1000))
    qtbot.mouseClick(view.viewport(), Qt.MouseButton.LeftButton, pos=QPoint(200, 150))
    assert not view.is_fit
    qtbot.mousePress(view.viewport(), Qt.MouseButton.LeftButton, pos=QPoint(200, 150))
    qtbot.mouseMove(view.viewport(), QPoint(260, 190))
    qtbot.mouseRelease(view.viewport(), Qt.MouseButton.LeftButton, pos=QPoint(260, 190))
    assert not view.is_fit  # a drag pans; it must not toggle back


def test_resize_keeps_fit(view):
    view.set_image(_img(2000, 1000))
    view.resize(800, 600)
    assert view.current_scale() == pytest.approx(800 / 2000, rel=0.05)


def test_placeholder_disables_zoom(view):
    view.set_placeholder("깨진 파일")
    assert not view.has_image
    view.toggle_zoom()
    assert view.is_fit


def test_new_image_resets_to_fit(view):
    view.set_image(_img(2000, 1000))
    view.toggle_zoom()
    view.set_image(_img(3000, 1500))
    assert view.is_fit
