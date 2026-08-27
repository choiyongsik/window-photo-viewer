from pathlib import Path

import pytest

pytest.importorskip("PySide6.QtMultimedia")

from PySide6.QtCore import Qt  # noqa: E402
from PySide6.QtMultimedia import QMediaPlayer  # noqa: E402

from ui.video_view import VideoView  # noqa: E402


def test_load_sets_source_and_not_playing(qtbot, tmp_path: Path):
    v = VideoView()
    qtbot.addWidget(v)
    clip = tmp_path / "clip.mp4"
    clip.write_bytes(b"\x00")
    assert v.source_path() is None
    v.load(clip)
    assert v.source_path() == clip
    assert v.is_playing() is False


def test_stop_clears_playback_without_error(qtbot, tmp_path: Path):
    v = VideoView()
    qtbot.addWidget(v)
    clip = tmp_path / "clip.mp4"
    clip.write_bytes(b"\x00")
    v.load(clip)
    v.toggle_play()   # may fail to decode garbage — must not raise
    v.stop()
    assert v.is_playing() is False


def test_end_of_media_rewinds_and_pauses(qtbot, tmp_path: Path, monkeypatch):
    v = VideoView()
    qtbot.addWidget(v)
    calls: list[str] = []
    monkeypatch.setattr(v.player, "setPosition", lambda pos: calls.append(f"setPosition({pos})"))
    monkeypatch.setattr(v.player, "pause", lambda: calls.append("pause"))

    v._on_media_status(QMediaPlayer.MediaStatus.EndOfMedia)
    assert calls == ["setPosition(0)", "pause"]

    calls.clear()
    v._on_media_status(QMediaPlayer.MediaStatus.LoadedMedia)
    assert calls == []


def test_click_toggles_play(qtbot, tmp_path: Path, monkeypatch):
    v = VideoView()
    qtbot.addWidget(v)
    v.show()
    calls = []
    monkeypatch.setattr(v, "toggle_play", lambda: calls.append(1))

    qtbot.mouseClick(v, Qt.MouseButton.LeftButton)

    assert calls == [1]
