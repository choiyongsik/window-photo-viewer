from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt

from tests.helpers import make_jpeg
from ui.folder_panel import PLACEHOLDER_TEXT, FolderPanel, list_child_folders


def _make_folder(base: Path, name: str, n_images: int = 1, n_videos: int = 0) -> Path:
    d = base / name
    d.mkdir(parents=True, exist_ok=True)
    for i in range(n_images):
        make_jpeg(d / f"img_{i}.jpg")
    for i in range(n_videos):
        (d / f"clip_{i}.mp4").write_bytes(b"")
    return d


# ---------------- list_child_folders ----------------

def test_list_child_folders_order_and_hidden_exclusion(tmp_path):
    root = tmp_path / "root"
    _make_folder(root, "B2")
    _make_folder(root, "B10")
    _make_folder(root, "A")
    hidden = root / ".hidden"
    hidden.mkdir()
    (root / "afile.txt").write_text("not a directory")

    result = list_child_folders(root)

    assert [p.name for p in result] == ["A", "B2", "B10"]


def test_list_child_folders_permission_error_returns_empty(tmp_path, monkeypatch):
    root = tmp_path / "root"
    root.mkdir()

    def boom(self):
        raise PermissionError("denied")

    monkeypatch.setattr(Path, "iterdir", boom)

    assert list_child_folders(root) == []


def test_list_child_folders_survives_a_missing_folder(tmp_path):
    missing = tmp_path / "does-not-exist"
    assert list_child_folders(missing) == []


# ---------------- FolderPanel: root / lazy loading ----------------

def test_set_root_shows_root_expanded(qtbot, tmp_path):
    root = tmp_path / "root"
    _make_folder(root, "A")

    panel = FolderPanel()
    qtbot.addWidget(panel)
    panel.set_root(root)

    assert panel.root() == root
    assert panel._tree.topLevelItemCount() == 1
    root_item = panel._tree.topLevelItem(0)
    assert root_item.isExpanded() is True
    assert root_item.text(0) == "root"
    assert root_item.toolTip(0) == str(root)


def test_children_lazily_loaded_on_expand(qtbot, tmp_path):
    root = tmp_path / "root"
    a = _make_folder(root, "A")
    _make_folder(a, "A1")

    panel = FolderPanel()
    qtbot.addWidget(panel)
    panel.set_root(root)

    a_item = panel._path_to_item[a]
    assert a_item.isExpanded() is False
    assert a_item.childCount() == 1
    placeholder = a_item.child(0)
    assert placeholder.data(0, Qt.ItemDataRole.UserRole) is None

    a_item.setExpanded(True)

    assert a_item.childCount() == 1
    real_child = a_item.child(0)
    assert real_child.data(0, Qt.ItemDataRole.UserRole) == a / "A1"
    assert real_child.text(0) == "A1"


def test_hidden_dirs_excluded_from_tree(qtbot, tmp_path):
    root = tmp_path / "root"
    _make_folder(root, "A")
    hidden = root / ".hidden"
    hidden.mkdir()

    panel = FolderPanel()
    qtbot.addWidget(panel)
    panel.set_root(root)

    root_item = panel._tree.topLevelItem(0)
    names = [root_item.child(i).text(0) for i in range(root_item.childCount())]
    assert names == ["A"]


def test_set_root_none_clears(qtbot, tmp_path):
    root = tmp_path / "root"
    _make_folder(root, "A")

    panel = FolderPanel()
    qtbot.addWidget(panel)
    panel.set_root(root)

    panel.set_root(None)

    assert panel.root() is None
    assert panel._tree.topLevelItemCount() == 0
    assert panel.current_folder() is None
    assert PLACEHOLDER_TEXT in panel._header.text()


# ---------------- FolderPanel: set_folder / highlight ----------------

def test_set_folder_deep_child_expands_ancestors_and_highlights(qtbot, tmp_path):
    root = tmp_path / "root"
    b = _make_folder(root, "B")
    b1 = _make_folder(b, "B1")

    panel = FolderPanel()
    qtbot.addWidget(panel)
    panel.set_root(root)

    panel.set_folder(b1)

    assert panel.current_folder() == b1
    b_item = panel._path_to_item[b]
    assert b_item.isExpanded() is True
    b1_item = panel._path_to_item[b1]
    assert b1_item.font(0).bold() is True
    assert panel._tree.currentItem() is b1_item


def test_set_folder_outside_root_is_a_noop(qtbot, tmp_path):
    root = tmp_path / "root"
    _make_folder(root, "A")
    other = tmp_path / "other"
    other.mkdir()

    panel = FolderPanel()
    qtbot.addWidget(panel)
    panel.set_root(root)
    panel.set_folder(root / "A")

    panel.set_folder(other)   # outside root: no-op

    assert panel.current_folder() == root / "A"


def test_set_folder_none_clears_highlight(qtbot, tmp_path):
    root = tmp_path / "root"
    _make_folder(root, "A")

    panel = FolderPanel()
    qtbot.addWidget(panel)
    panel.set_root(root)
    panel.set_folder(root / "A")

    panel.set_folder(None)

    assert panel.current_folder() is None


# ---------------- FolderPanel: visible_folders / navigation ----------------

def test_visible_folders_dfs_order(qtbot, tmp_path):
    root = tmp_path / "root"
    _make_folder(root, "A")
    b = _make_folder(root, "B")
    _make_folder(b, "B1")
    _make_folder(b, "B2")
    _make_folder(root, "C")

    panel = FolderPanel()
    qtbot.addWidget(panel)
    panel.set_root(root)

    assert panel.visible_folders() == [root, root / "A", b, root / "C"]

    panel._path_to_item[b].setExpanded(True)

    assert panel.visible_folders() == [
        root, root / "A", b, b / "B1", b / "B2", root / "C",
    ]


def test_next_prev_folder_boundaries(qtbot, tmp_path):
    root = tmp_path / "root"
    _make_folder(root, "A")
    _make_folder(root, "B")
    _make_folder(root, "C")

    panel = FolderPanel()
    qtbot.addWidget(panel)
    panel.set_root(root)

    panel.set_folder(root)
    assert panel.prev_folder() is None
    assert panel.next_folder() == root / "A"

    panel.set_folder(root / "A")
    assert panel.prev_folder() == root
    assert panel.next_folder() == root / "B"

    panel.set_folder(root / "C")
    assert panel.next_folder() is None


# ---------------- FolderPanel: click ----------------

def test_click_emits_folder_activated(qtbot, tmp_path):
    root = tmp_path / "root"
    a = _make_folder(root, "A")

    panel = FolderPanel()
    qtbot.addWidget(panel)
    panel.set_root(root)
    panel.set_folder(root)

    item = panel._path_to_item[a]
    with qtbot.waitSignal(panel.folder_activated, timeout=1000) as blocker:
        panel._tree.itemClicked.emit(item, 0)
    assert blocker.args == [a]


def test_click_on_current_folder_is_a_noop(qtbot, tmp_path):
    root = tmp_path / "root"
    a = _make_folder(root, "A")

    panel = FolderPanel()
    qtbot.addWidget(panel)
    panel.set_root(root)
    panel.set_folder(a)

    item = panel._path_to_item[a]
    with qtbot.assertNotEmitted(panel.folder_activated):
        panel._tree.itemClicked.emit(item, 0)


# ---------------- FolderPanel: contains ----------------

def test_contains(qtbot, tmp_path):
    root = tmp_path / "root"
    a = _make_folder(root, "A")
    other = tmp_path / "other"
    other.mkdir()

    panel = FolderPanel()
    qtbot.addWidget(panel)
    panel.set_root(root)

    assert panel.contains(root) is True
    assert panel.contains(a) is True
    assert panel.contains(other) is False


def test_contains_with_no_root_is_always_false(qtbot, tmp_path):
    panel = FolderPanel()
    qtbot.addWidget(panel)

    assert panel.contains(tmp_path) is False


# ---------------- FolderPanel: counts ----------------

def test_counts_arrive_and_are_applied_to_the_item(qtbot, tmp_path):
    root = tmp_path / "root"
    a = _make_folder(root, "A", n_images=2, n_videos=1)

    panel = FolderPanel()
    qtbot.addWidget(panel)
    panel.set_root(root)

    item = panel._path_to_item[a]
    qtbot.waitUntil(lambda: "장" in item.text(0), timeout=2000)
    assert item.text(0) == "A   (2장 · 1영상)"


def test_counts_arrive_without_videos_omit_the_video_count(qtbot, tmp_path):
    root = tmp_path / "root"
    a = _make_folder(root, "A", n_images=3)

    panel = FolderPanel()
    qtbot.addWidget(panel)
    panel.set_root(root)

    item = panel._path_to_item[a]
    qtbot.waitUntil(lambda: "장" in item.text(0), timeout=2000)
    assert item.text(0) == "A   (3장)"


def test_stale_counts_are_ignored(qtbot, tmp_path):
    root = tmp_path / "root"
    a = _make_folder(root, "A", n_images=1)

    panel = FolderPanel()
    qtbot.addWidget(panel)
    panel.set_root(root)

    item = panel._path_to_item[a]
    panel._path_to_item.pop(a)   # simulate the path no longer being tracked in the tree

    panel._on_counts_ready({a: (99, 99)})

    assert item.text(0) == "A"
