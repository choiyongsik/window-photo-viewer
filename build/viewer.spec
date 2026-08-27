# -*- mode: python ; coding: utf-8 -*-
# Build:  python -m PyInstaller build/viewer.spec --noconfirm
import os

from PyInstaller.utils.hooks import collect_data_files, collect_dynamic_libs

binaries = collect_dynamic_libs("pyexiv2")
datas = collect_data_files("imageio_ffmpeg")   # bundled ffmpeg.exe

a = Analysis(
    ["../app.py"],
    pathex=[os.path.join(SPECPATH, "..")],  # noqa: F821 — SPECPATH is injected by PyInstaller
    binaries=binaries,
    datas=datas,
    hiddenimports=["PySide6.QtMultimedia", "PySide6.QtMultimediaWidgets"],
    hookspath=[],
    runtime_hooks=[],
    excludes=["tkinter"],
    noarchive=False,
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="WindowPhotoViewer",
    debug=False,
    console=False,
)
coll = COLLECT(exe, a.binaries, a.datas, strip=False, upx=False, name="WindowPhotoViewer")
