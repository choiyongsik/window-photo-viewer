from __future__ import annotations

import os
import stat
from pathlib import Path
from typing import NamedTuple

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QLabel, QListWidget, QListWidgetItem, QVBoxLayout, QWidget

from core.models import MediaKind, kind_for
from core.scanner import natural_key

PANEL_WIDTH = 240
PLACEHOLDER_TEXT = "폴더 없음"
# Windows marks files/dirs hidden with an attribute rather than a leading dot; 0
# elsewhere, which makes the mask test below a no-op on other platforms (mirrors
# core/scanner.py's own check).
_HIDDEN_MASK = getattr(stat, "FILE_ATTRIBUTE_HIDDEN", 0)


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
    cannot be scanned (e.g. permission denied) still gets listed, just with (0, 0)."""
    images = videos = 0
    try:
        with os.scandir(folder) as it:
            for entry in it:
                if not entry.is_file():
                    continue
                kind = kind_for(Path(entry.name))
                if kind is MediaKind.IMAGE:
                    images += 1
                elif kind is MediaKind.VIDEO:
                    videos += 1
    except PermissionError:
        return (0, 0)
    return images, videos


def list_sibling_folders(folder: Path) -> list[FolderEntry]:
    """The subdirectories of folder.parent (including folder itself), natural-sorted.

    Hidden directories (leading '.' or the Windows hidden attribute) are excluded.
    At a drive root, folder.parent == folder, so the folder is the only entry.
    """
    parent = folder.parent
    if parent == folder:
        images, videos = _counts(folder)
        return [FolderEntry(folder, images, videos)]

    dirs: list[Path] = []
    for entry in parent.iterdir():
        if not entry.is_dir() or _is_hidden(entry):
            continue
        dirs.append(entry)
    dirs.sort(key=lambda p: natural_key(p.name))
    return [FolderEntry(d, *_counts(d)) for d in dirs]


class FolderPanel(QWidget):
    """Left-hand sibling-folder list: shows the folders next to the one currently
    open, so PgUp/PgDn (wired by MainWindow) can hop between shoots without a
    file dialog. Never takes keyboard focus — MainWindow owns all shortcuts.
    """

    folder_activated = Signal(object)   # Path

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedWidth(PANEL_WIDTH)
        self._current: Path | None = None
        self._siblings: list[Path] = []

        self._header = QLabel()
        self._header.setStyleSheet("padding:4px 8px; background:#181818; color:#dddddd;")
        self._header.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self._header.setWordWrap(False)

        self._list = QListWidget()
        self._list.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self._list.setStyleSheet(
            "QListWidget { background:#181818; color:#dddddd; border:none; }"
            "QListWidget::item:selected { background:#333333; }"
        )
        self._list.itemClicked.connect(self._on_item_clicked)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.addWidget(self._header, 0)
        layout.addWidget(self._list, 1)

        self.set_folder(None)

    def set_folder(self, folder: Path | None) -> None:
        self._current = folder
        self._list.clear()
        self._siblings = []
        if folder is None:
            self._header.setText(PLACEHOLDER_TEXT)
            return

        entries = list_sibling_folders(folder)
        self._siblings = [e.path for e in entries]
        self._header.setText(self._elided(str(folder.parent)))

        for entry in entries:
            label = f"{entry.path.name}   ({entry.images}장"
            if entry.videos:
                label += f" · {entry.videos}영상"
            label += ")"
            item = QListWidgetItem(label)
            item.setData(Qt.ItemDataRole.UserRole, entry.path)
            self._list.addItem(item)
            if entry.path == folder:
                self._list.setCurrentItem(item)
                font = item.font()
                font.setBold(True)
                item.setFont(font)

    def _elided(self, text: str) -> str:
        metrics = self._header.fontMetrics()
        return metrics.elidedText(text, Qt.TextElideMode.ElideMiddle, PANEL_WIDTH - 16)

    def current_folder(self) -> Path | None:
        return self._current

    def sibling_folders(self) -> list[Path]:
        return list(self._siblings)

    def next_folder(self) -> Path | None:
        return self._offset_folder(1)

    def prev_folder(self) -> Path | None:
        return self._offset_folder(-1)

    def _offset_folder(self, delta: int) -> Path | None:
        if self._current not in self._siblings:
            return None
        idx = self._siblings.index(self._current) + delta
        return self._siblings[idx] if 0 <= idx < len(self._siblings) else None

    def _on_item_clicked(self, item: QListWidgetItem) -> None:
        path = item.data(Qt.ItemDataRole.UserRole)
        if path == self._current:
            return
        self.folder_activated.emit(path)
