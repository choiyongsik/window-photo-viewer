from __future__ import annotations

import os
from pathlib import Path

from tests.helpers import make_jpeg, make_png
from ui.folder_panel import FolderEntry, FolderPanel, list_sibling_folders


def _make_folder(base: Path, name: str, n_images: int = 1, n_videos: int = 0) -> Path:
    d = base / name
    d.mkdir(parents=True, exist_ok=True)
    for i in range(n_images):
        make_jpeg(d / f"img_{i}.jpg")
    for i in range(n_videos):
        (d / f"clip_{i}.mp4").write_bytes(b"")
    return d


# ---------------- list_sibling_folders ----------------

def test_list_sibling_folders_order_counts_and_hidden(tmp_path):
    root = tmp_path / "root"
    a = _make_folder(root, "A", n_images=2, n_videos=1)
    b2 = _make_folder(root, "B2", n_images=1)
    b10 = _make_folder(root, "B10", n_images=3, n_videos=2)
    make_png(b2 / "extra.png")   # B2: 1 jpg + 1 png = 2 images
    hidden = root / ".hidden"
    hidden.mkdir()
    make_jpeg(hidden / "x.jpg")

    entries = list_sibling_folders(a)

    assert [e.path.name for e in entries] == ["A", "B2", "B10"]
    by_name = {e.path.name: e for e in entries}
    assert by_name["A"] == FolderEntry(a, 2, 1)
    assert by_name["B2"] == FolderEntry(b2, 2, 0)
    assert by_name["B10"] == FolderEntry(b10, 3, 2)


def test_list_sibling_folders_includes_the_current_folder(tmp_path):
    root = tmp_path / "root"
    a = _make_folder(root, "A")
    b2 = _make_folder(root, "B2")

    entries = list_sibling_folders(b2)

    assert {e.path for e in entries} == {a, b2}


def test_list_sibling_folders_folder_whose_parent_has_only_itself(tmp_path):
    only = tmp_path / "only"
    make_jpeg(only / "a.jpg")

    entries = list_sibling_folders(only)

    assert [e.path for e in entries] == [only]
    assert entries[0].images == 1


def test_list_sibling_folders_permission_error_yields_zero_counts_but_is_listed(tmp_path, monkeypatch):
    root = tmp_path / "root"
    locked = _make_folder(root, "Locked", n_images=1)
    opened = _make_folder(root, "Open", n_images=1)

    real_scandir = os.scandir

    def boom(path=None):
        if Path(path) == locked:
            raise PermissionError("denied")
        return real_scandir(path)

    monkeypatch.setattr("ui.folder_panel.os.scandir", boom)

    entries = list_sibling_folders(opened)
    by_name = {e.path.name: e for e in entries}
    assert by_name["Locked"] == FolderEntry(locked, 0, 0)
    assert by_name["Open"].images == 1


# ---------------- FolderPanel ----------------

def test_folder_panel_set_folder_and_navigation(qtbot, tmp_path):
    root = tmp_path / "root"
    a = _make_folder(root, "A")
    b2 = _make_folder(root, "B2")
    b10 = _make_folder(root, "B10")

    panel = FolderPanel()
    qtbot.addWidget(panel)

    panel.set_folder(b2)

    assert panel._list.count() == 3
    assert panel.current_folder() == b2
    assert panel.sibling_folders() == [a, b2, b10]
    assert panel.next_folder() == b10
    assert panel.prev_folder() == a

    panel.set_folder(a)
    assert panel.prev_folder() is None
    assert panel.next_folder() == b2

    panel.set_folder(b10)
    assert panel.next_folder() is None


def test_folder_panel_click_emits_folder_activated(qtbot, tmp_path):
    root = tmp_path / "root"
    a = _make_folder(root, "A")
    b2 = _make_folder(root, "B2")

    panel = FolderPanel()
    qtbot.addWidget(panel)
    panel.set_folder(b2)

    with qtbot.waitSignal(panel.folder_activated, timeout=1000) as blocker:
        panel._list.itemClicked.emit(panel._list.item(0))
    assert blocker.args == [a]


def test_folder_panel_click_on_current_folder_is_a_noop(qtbot, tmp_path):
    root = tmp_path / "root"
    _make_folder(root, "A")
    b2 = _make_folder(root, "B2")

    panel = FolderPanel()
    qtbot.addWidget(panel)
    panel.set_folder(b2)
    current_row = panel._list.currentRow()

    with qtbot.assertNotEmitted(panel.folder_activated):
        panel._list.itemClicked.emit(panel._list.item(current_row))


def test_folder_panel_set_folder_none_clears_and_shows_placeholder(qtbot, tmp_path):
    root = tmp_path / "root"
    a = _make_folder(root, "A")

    panel = FolderPanel()
    qtbot.addWidget(panel)
    panel.set_folder(a)
    assert panel._list.count() == 1

    panel.set_folder(None)

    assert panel._list.count() == 0
    assert panel.current_folder() is None
    assert "폴더 없음" in panel._header.text()
