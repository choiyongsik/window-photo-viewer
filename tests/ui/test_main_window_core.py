from __future__ import annotations

from pathlib import Path

import pytest
from PySide6.QtCore import QSettings, Qt

from core import metadata
from core.models import Label, MediaKind
from core.scanner import scan
from core.thumbnails import ThumbnailCache
from tests.helpers import make_jpeg
from ui import workers
from ui.main_window import MainWindow


@pytest.fixture
def folder(tmp_path: Path) -> Path:
    for i in range(1, 6):
        make_jpeg(tmp_path / f"IMG_{i}.jpg", size=(200 + i, 100))
    (tmp_path / "clip.mp4").write_bytes(b"\x00" * 8)
    return tmp_path


def _items(folder: Path):
    items = scan(folder)
    for it in items:
        metadata.populate(it)
    return items


@pytest.fixture
def win(qtbot, tmp_path: Path):
    settings = QSettings(str(tmp_path / "settings.ini"), QSettings.Format.IniFormat)
    w = MainWindow(thumb_cache=ThumbnailCache(tmp_path / "cache"), settings=settings)
    w.suppress_dialogs = True
    qtbot.addWidget(w)
    w.resize(1000, 700)
    w.show()
    qtbot.waitExposed(w)
    return w


def test_empty_state(win):
    assert win.current == -1
    assert win.current_item() is None
    assert "Ctrl+O" in win.header.text()
    win.next_item()
    win.set_rating(3)  # must not raise with no items


def test_load_items_selects_first_and_updates_header(win, folder, qtbot):
    win.load_items(_items(folder), folder)
    assert win.current == 0
    assert win.current_item().path.name == "clip.mp4"
    assert "1/6" in win.header.text()
    assert str(folder) in win.header.text()
    assert win.filmstrip.current_row() == 0


def test_navigation_keys_and_bounds(win, folder, qtbot):
    win.load_items(_items(folder), folder)
    qtbot.keyClick(win, Qt.Key.Key_Right)
    assert win.current == 1
    qtbot.keyClick(win, Qt.Key.Key_Space)
    assert win.current == 2
    qtbot.keyClick(win, Qt.Key.Key_Left)
    qtbot.keyClick(win, Qt.Key.Key_Backspace)
    assert win.current == 0
    qtbot.keyClick(win, Qt.Key.Key_Left)
    assert win.current == 0
    qtbot.keyClick(win, Qt.Key.Key_End)
    assert win.current == 5
    qtbot.keyClick(win, Qt.Key.Key_Right)
    assert win.current == 5
    qtbot.keyClick(win, Qt.Key.Key_Home)
    assert win.current == 0
    assert win.grid.current_row() == 0


def test_video_item_shows_video_view_and_space_toggles_play(win, folder, qtbot, monkeypatch):
    win.load_items(_items(folder), folder)
    assert win.current_item().kind is MediaKind.VIDEO
    assert win.content_stack.currentWidget() is win.video
    assert win.video.source_path() == folder / "clip.mp4"

    calls = []
    monkeypatch.setattr(win.video, "toggle_play", lambda: calls.append(1))
    qtbot.keyClick(win, Qt.Key.Key_Space)
    assert calls == [1]
    assert win.current == 0  # Space toggled play, it did not advance

    win.next_item()
    assert win.content_stack.currentWidget() is win.loupe


def test_image_loads_into_loupe_and_neighbors_preload(win, folder, qtbot):
    win.load_items(_items(folder), folder)
    win.next_item()  # IMG_1
    qtbot.waitUntil(lambda: win.loupe.has_image, timeout=5000)
    qtbot.waitUntil(lambda: all(i in win.image_cache for i in (1, 2, 3)), timeout=5000)
    assert 0 not in win.image_cache  # video is never decoded as image


def test_rating_keys_toggle_and_dispatch_write(win, folder, qtbot, monkeypatch):
    calls: list[tuple[str, int, Label]] = []
    real = metadata.write_rating_label

    def spy(path, kind, rating, label):
        calls.append((path.name, rating, label))
        real(path, kind, rating, label)

    monkeypatch.setattr(workers.metadata, "write_rating_label", spy)
    win.load_items(_items(folder), folder)
    win.next_item()
    item = win.current_item()

    with qtbot.waitSignal(win.signals.write_finished, timeout=5000):
        qtbot.keyClick(win, Qt.Key.Key_3)
    assert item.rating == 3 and "★★★☆☆" in win.header.text()
    with qtbot.waitSignal(win.signals.write_finished, timeout=5000):
        qtbot.keyClick(win, Qt.Key.Key_3)
    assert item.rating == 0
    with qtbot.waitSignal(win.signals.write_finished, timeout=5000):
        qtbot.keyClick(win, Qt.Key.Key_X)
    assert item.rating == -1 and "✕" in win.header.text()
    with qtbot.waitSignal(win.signals.write_finished, timeout=5000):
        qtbot.keyClick(win, Qt.Key.Key_X)
    assert item.rating == 0
    with qtbot.waitSignal(win.signals.write_finished, timeout=5000):
        qtbot.keyClick(win, Qt.Key.Key_7)
    assert item.label is Label.YELLOW and "[Yellow]" in win.header.text()
    with qtbot.waitSignal(win.signals.write_finished, timeout=5000):
        qtbot.keyClick(win, Qt.Key.Key_7)
    assert item.label is Label.NONE
    with qtbot.waitSignal(win.signals.write_finished, timeout=5000):
        qtbot.keyClick(win, Qt.Key.Key_0)
    assert [c[1:] for c in calls] == [(3, Label.NONE), (0, Label.NONE), (-1, Label.NONE), (0, Label.NONE),
                                      (0, Label.YELLOW), (0, Label.NONE), (0, Label.NONE)]
    assert metadata.read_rating_label(item.path, item.kind) == (0, Label.NONE)
    assert win.current == 1  # no auto-advance by default


def test_write_failure_marks_item_and_status(win, folder, qtbot, monkeypatch):
    def boom(*a, **k):
        raise metadata.MetadataError("locked")

    monkeypatch.setattr(workers.metadata, "write_rating_label", boom)
    win.load_items(_items(folder), folder)
    win.next_item()
    with qtbot.waitSignal(win.signals.write_finished, timeout=5000):
        qtbot.keyClick(win, Qt.Key.Key_4)
    item = win.current_item()
    assert item.rating == 4                       # in-memory state is kept
    assert item.write_error == "locked"
    assert "locked" in win.statusBar().currentMessage()
    assert "기록 실패" in win.header.text()


def test_filmstrip_click_changes_current(win, folder, qtbot):
    win.load_items(_items(folder), folder)
    win.filmstrip.row_activated.emit(3)
    assert win.current == 3


def test_thumbnails_arrive_in_model(win, folder, qtbot):
    win.load_items(_items(folder), folder)
    qtbot.waitUntil(lambda: win.model.thumbnail(1) is not None, timeout=5000)
    qtbot.waitUntil(lambda: win.model.data(win.model.index(0), win.model.FailedRole) is True, timeout=5000)


def test_z_toggles_zoom(win, folder, qtbot):
    win.load_items(_items(folder), folder)
    win.next_item()
    qtbot.waitUntil(lambda: win.loupe.has_image, timeout=5000)
    qtbot.keyClick(win, Qt.Key.Key_Z)
    assert not win.loupe.is_fit
    qtbot.keyClick(win, Qt.Key.Key_Z)
    assert win.loupe.is_fit


def test_open_folder_scans_in_background(win, folder, qtbot):
    with qtbot.waitSignal(win.signals.scan_finished, timeout=5000):
        win.open_folder(folder)
    qtbot.waitUntil(lambda: win.current == 0, timeout=2000)
    assert win.folder == folder
    assert len(win.model.items()) == 6


def test_open_missing_folder_reports_error(win, tmp_path, qtbot):
    with qtbot.waitSignal(win.signals.scan_failed, timeout=5000):
        win.open_folder(tmp_path / "nope")
    qtbot.waitUntil(lambda: "nope" in win.statusBar().currentMessage(), timeout=2000)
