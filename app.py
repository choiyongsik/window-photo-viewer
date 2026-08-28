"""골라보기 (Photo Culling Viewer) entry point.

Usage: python app.py [folder-or-image-path]
Without an argument the last opened folder (QSettings) is restored.
"""
from __future__ import annotations

import sys
from pathlib import Path

from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QApplication

from ui.main_window import APP_TITLE, MainWindow
from ui.resources import app_icon_path


def resolve_start_folder(argv: list[str], fallback: Path | None) -> Path | None:
    if len(argv) > 1:
        p = Path(argv[1])
        if p.is_dir():
            return p
        if p.is_file():
            return p.parent
    if fallback is not None and fallback.is_dir():
        return fallback
    return None


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv if argv is None else argv
    app = QApplication(argv)
    app.setApplicationName("WindowPhotoViewer")      # internal: QSettings key, stays ASCII
    app.setOrganizationName("WindowPhotoViewer")
    app.setApplicationDisplayName(APP_TITLE)         # what Windows shows in the taskbar
    app.setWindowIcon(QIcon(str(app_icon_path())))   # taskbar / Alt+Tab icon for every window
    window = MainWindow()
    window.show()
    folder = resolve_start_folder(argv, window.last_folder())
    if folder is not None:
        window.open_folder(folder)
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
