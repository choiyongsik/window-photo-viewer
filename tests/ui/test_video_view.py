from pathlib import Path

import pytest

pytest.importorskip("PySide6.QtMultimedia")

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
