from __future__ import annotations

import os
import stat
from pathlib import Path
from typing import NamedTuple

from PySide6.QtCore import QObject, QRunnable, Qt, QThreadPool, Signal
from PySide6.QtWidgets import QLabel, QTreeWidget, QTreeWidgetItem, QVBoxLayout, QWidget

from core.models import IMAGE_EXTENSIONS, VIDEO_EXTENSIONS
from core.scanner import natural_key

PANEL_WIDTH = 240
PANEL_MIN_WIDTH = 180
PLACEHOLDER_TEXT = "폴더 없음"
# Windows marks files/dirs hidden with an attribute rather than a leading dot; 0
# elsewhere, which makes the mask test below a no-op on other platforms (mirrors
# core/scanner.py's own check).
_HIDDEN_MASK = getattr(stat, "FILE_ATTRIBUTE_HIDDEN", 0)

# Item data roles on column 0. _ROLE_PATH holds the Path for a real folder node, or
# None for the lazy-load placeholder child every unexpanded directory node carries.
# _ROLE_LOADED marks whether a directory node's real children have been fetched yet
# (guards against re-listing the folder every time it is re-expanded).
_ROLE_PATH = Qt.ItemDataRole.UserRole
_ROLE_LOADED = Qt.ItemDataRole.UserRole + 1


class FolderEntry(NamedTuple):
    path: Path
    images: int
    videos: int


def _is_hidden(entry: Path) -> bool:
    if entry.name.startswith("."):
        return True
    if not _HIDDEN_MASK:
        return False
    try:
        st = entry.stat()
    except OSError:
        return False
    return bool(getattr(st, "st_file_attributes", 0) & _HIDDEN_MASK)


def _counts(folder: Path) -> tuple[int, int]:
    """Non-recursive image/video counts for one folder, ignoring XMP. A folder that
    cannot be scanned at all (permission denied, or it vanished between being listed
    and being counted) still gets listed, just with (0, 0) — any OSError here is a
    filesystem race or access problem, not a reason to blow up folder navigation."""
    images = videos = 0
    try:
        with os.scandir(folder) as it:
            for entry in it:
                try:
                    if not entry.is_file():
                        continue
                except OSError:
                    continue
                ext = os.path.splitext(entry.name)[1].lower()
                if ext in IMAGE_EXTENSIONS:
                    images += 1
                elif ext in VIDEO_EXTENSIONS:
                    videos += 1
    except OSError:
        return (0, 0)
    return images, videos


def _entry_for(path: Path) -> FolderEntry:
    """FolderEntry for one folder, tolerant of _counts raising (e.g. the folder
    vanished between being listed and being counted) — same (0, 0) fallback."""
    try:
        images, videos = _counts(path)
    except OSError:
        images, videos = 0, 0
    return FolderEntry(path, images, videos)


def list_child_folders(folder: Path) -> list[Path]:
    """The direct subdirectories of *folder*, natural-sorted.

    Hidden directories (leading '.' or the Windows hidden attribute) are excluded.
    Ordinary filesystem races (folder vanished, permission denied, an unreadable
    directory entry) never raise here — they just yield an empty list, same as an
    empty folder. Losing a subtree should never break the rest of the tree.
    """
    try:
        entries = list(folder.iterdir())
    except OSError:
        return []

    dirs: list[Path] = []
    for entry in entries:
        try:
            if not entry.is_dir():
                continue
        except OSError:
            continue
        if _is_hidden(entry):
            continue
        dirs.append(entry)
    dirs.sort(key=lambda p: natural_key(p.name))
    return dirs


def _display_name(path: Path) -> str:
    # A drive root (e.g. "C:\") has an empty .name; fall back to the full path so
    # the node is never shown blank.
    return path.name or str(path)


class _CountSignals(QObject):
    counts_ready = Signal(object)  # dict[Path, tuple[int, int]]


class FolderCountJob(QRunnable):
    """Counts images/videos for each of *paths*, off the UI thread, and reports the
    whole batch at once via *signals*."""

    def __init__(self, paths: list[Path], signals: _CountSignals):
        super().__init__()
        self.paths = paths
        self.signals = signals

    def run(self) -> None:
        counts = {path: _counts(path) for path in self.paths}
        self.signals.counts_ready.emit(counts)


class FolderPanel(QWidget):
    """Left-hand root-based folder tree: a lazily-loaded QTreeWidget rooted at
    whatever folder MainWindow considers the current root, so PgUp/PgDn (wired by
    MainWindow) can hop between folders in tree order without a file dialog.
    Never takes keyboard focus — MainWindow owns all shortcuts.
    """

    folder_activated = Signal(object)   # Path — ignored when it is the current folder

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumWidth(PANEL_MIN_WIDTH)
        self._root: Path | None = None
        self._root_item: QTreeWidgetItem | None = None
        self._current: Path | None = None
        self._highlighted_item: QTreeWidgetItem | None = None
        self._path_to_item: dict[Path, QTreeWidgetItem] = {}

        self._header = QLabel()
        self._header.setStyleSheet("padding:4px 8px; background:#181818; color:#dddddd;")
        self._header.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self._header.setWordWrap(False)

        self._tree = QTreeWidget()
        self._tree.setColumnCount(1)
        self._tree.setHeaderHidden(True)
        self._tree.setUniformRowHeights(True)
        self._tree.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self._tree.setStyleSheet(
            "QTreeWidget { background:#181818; color:#dddddd; border:none; }"
            "QTreeWidget::item:selected { background:#333333; }"
        )
        self._tree.itemExpanded.connect(self._on_item_expanded)
        self._tree.itemClicked.connect(self._on_item_clicked)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.addWidget(self._header, 0)
        layout.addWidget(self._tree, 1)

        # Background folder counting: 2 worker threads, one shared signal holder.
        # Deliberately unparented (mirrors ui.workers.WorkerSignals): a FolderCountJob
        # may still be running after this panel is destroyed (e.g. test teardown), and
        # each job keeps its own reference to _count_signals, so it stays alive as long
        # as any job needs it. Qt auto-disconnects the queued connection to this
        # panel's slot once the panel itself is destroyed, so a late emit is dropped
        # instead of crashing.
        self._count_pool = QThreadPool(self)
        self._count_pool.setMaxThreadCount(2)
        self._count_signals = _CountSignals()
        self._count_signals.counts_ready.connect(self._on_counts_ready)

        self.set_root(None)

    # ---------------- root ----------------
    def set_root(self, root: Path | None) -> None:
        self._count_pool.clear()
        self._tree.clear()
        self._path_to_item.clear()
        self._root = root
        self._root_item = None
        self._current = None
        self._highlighted_item = None

        if root is None:
            self._header.setText(PLACEHOLDER_TEXT)
            self._header.setToolTip("")
            return

        self._header.setText(self._elided(str(root)))
        self._header.setToolTip(str(root))
        root_item = self._make_node(root)
        self._tree.addTopLevelItem(root_item)
        self._root_item = root_item
        self._path_to_item[root] = root_item
        root_item.setExpanded(True)   # loads root's children synchronously

    def root(self) -> Path | None:
        return self._root

    def contains(self, folder: Path) -> bool:
        """folder == root or folder is under root. Pure path test, no I/O."""
        if self._root is None:
            return False
        try:
            folder.relative_to(self._root)
        except ValueError:
            return False
        return True

    # ---------------- nodes / lazy loading ----------------
    def _make_node(self, path: Path) -> QTreeWidgetItem:
        item = QTreeWidgetItem([_display_name(path)])
        item.setData(0, _ROLE_PATH, path)
        item.setData(0, _ROLE_LOADED, False)
        item.setToolTip(0, str(path))
        item.addChild(self._make_placeholder())
        return item

    @staticmethod
    def _make_placeholder() -> QTreeWidgetItem:
        placeholder = QTreeWidgetItem([""])
        placeholder.setData(0, _ROLE_PATH, None)
        return placeholder

    def _on_item_expanded(self, item: QTreeWidgetItem) -> None:
        if item.data(0, _ROLE_LOADED):
            return   # already loaded — a placeholder never coexists with real children
        item.setData(0, _ROLE_LOADED, True)
        item.takeChildren()   # drop the placeholder
        path = item.data(0, _ROLE_PATH)
        if path is None:
            return

        new_paths: list[Path] = []
        for child_path in list_child_folders(path):
            child_item = self._make_node(child_path)
            item.addChild(child_item)
            self._path_to_item[child_path] = child_item
            new_paths.append(child_path)

        if new_paths:
            self._count_pool.start(FolderCountJob(new_paths, self._count_signals))

    def _on_counts_ready(self, counts: dict[Path, tuple[int, int]]) -> None:
        for path, (images, videos) in counts.items():
            item = self._path_to_item.get(path)
            if item is None:
                continue   # stale: path no longer in the tree (root changed since)
            label = f"{_display_name(path)}   ({images}장"
            if videos:
                label += f" · {videos}영상"
            label += ")"
            item.setText(0, label)

    # ---------------- current folder / highlight ----------------
    def set_folder(self, folder: Path | None) -> None:
        if folder is not None and not self.contains(folder):
            return   # outside root: MainWindow decides root changes, not us
        self._current = folder
        self._clear_highlight()
        if folder is None:
            return
        item = self._ensure_item(folder)
        if item is not None:
            self._highlight(item)

    def current_folder(self) -> Path | None:
        return self._current

    def _ensure_item(self, folder: Path) -> QTreeWidgetItem | None:
        """The tree item for *folder*, expanding (and lazily loading) every ancestor
        along the way. folder must already satisfy self.contains(folder)."""
        if self._root_item is None:
            return None
        if folder == self._root:
            return self._root_item
        item = self._root_item
        for name in folder.relative_to(self._root).parts:
            if not item.isExpanded():
                item.setExpanded(True)   # synchronously loads item's real children
            child = self._child_by_name(item, name)
            if child is None:
                return None
            item = child
        return item

    @staticmethod
    def _child_by_name(parent: QTreeWidgetItem, name: str) -> QTreeWidgetItem | None:
        for i in range(parent.childCount()):
            child = parent.child(i)
            path = child.data(0, _ROLE_PATH)
            if path is not None and path.name == name:
                return child
        return None

    def _clear_highlight(self) -> None:
        if self._highlighted_item is not None:
            font = self._highlighted_item.font(0)
            if font.bold():
                font.setBold(False)
                self._highlighted_item.setFont(0, font)
            self._highlighted_item = None
        self._tree.setCurrentItem(None)

    def _highlight(self, item: QTreeWidgetItem) -> None:
        font = item.font(0)
        if not font.bold():
            font.setBold(True)
            item.setFont(0, font)
        self._tree.setCurrentItem(item)
        self._tree.scrollToItem(item)
        self._highlighted_item = item

    # ---------------- visible order / navigation ----------------
    def visible_folders(self) -> list[Path]:
        """Folders in the tree's current visible (expanded) order, root first."""
        result: list[Path] = []
        if self._root_item is not None:
            self._collect_visible(self._root_item, result)
        return result

    def _collect_visible(self, item: QTreeWidgetItem, result: list[Path]) -> None:
        path = item.data(0, _ROLE_PATH)
        if path is None:
            return   # placeholder
        result.append(path)
        if item.isExpanded():
            for i in range(item.childCount()):
                self._collect_visible(item.child(i), result)

    def next_folder(self) -> Path | None:
        return self._offset_folder(1)

    def prev_folder(self) -> Path | None:
        return self._offset_folder(-1)

    def _offset_folder(self, delta: int) -> Path | None:
        folders = self.visible_folders()
        if self._current not in folders:
            return None
        idx = folders.index(self._current) + delta
        return folders[idx] if 0 <= idx < len(folders) else None

    # ---------------- clicking ----------------
    def _on_item_clicked(self, item: QTreeWidgetItem, _column: int) -> None:
        path = item.data(0, _ROLE_PATH)
        if path is None or path == self._current:
            return
        self.folder_activated.emit(path)

    # ---------------- misc ----------------
    def _elided(self, text: str) -> str:
        metrics = self._header.fontMetrics()
        return metrics.elidedText(text, Qt.TextElideMode.ElideMiddle, PANEL_WIDTH - 16)
