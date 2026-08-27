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


# ---------------- control bar ----------------

from ui.video_view import format_time  # noqa: E402


def test_format_time():
    assert format_time(0) == "00:00"
    assert format_time(3000) == "00:03"
    assert format_time(12999) == "00:12"
    assert format_time(3661000) == "1:01:01"
    assert format_time(-5) == "00:00"


def test_controls_exist_and_take_no_focus(qtbot):
    v = VideoView()
    qtbot.addWidget(v)
    for w in (v.play_button, v.slider, v.time_label, v.mute_button):
        assert w.focusPolicy() == Qt.FocusPolicy.NoFocus
    assert v.slider.orientation() == Qt.Orientation.Horizontal


def test_slider_and_label_follow_player(qtbot):
    v = VideoView()
    qtbot.addWidget(v)
    v.player.durationChanged.emit(12000)
    v.player.positionChanged.emit(3000)
    assert v.slider.maximum() == 12000
    assert v.slider.value() == 3000
    assert v.time_label.text() == "00:03 / 00:12"


def test_slider_drag_seeks_and_suppresses_feedback(qtbot, monkeypatch):
    v = VideoView()
    qtbot.addWidget(v)
    v.player.durationChanged.emit(12000)
    seeks: list[int] = []
    monkeypatch.setattr(v.player, "setPosition", lambda pos: seeks.append(pos))

    v.slider.sliderPressed.emit()
    v.slider.setValue(5000)
    v.slider.sliderMoved.emit(5000)
    assert seeks == [5000]
    assert v.time_label.text().startswith("00:05")

    v.player.positionChanged.emit(3000)      # ignored while the user holds the slider
    assert v.slider.value() == 5000

    v.slider.sliderReleased.emit()
    v.player.positionChanged.emit(3000)      # followed again after release
    assert v.slider.value() == 3000


def test_seek_by_clamps_to_media_bounds(qtbot, monkeypatch):
    v = VideoView()
    qtbot.addWidget(v)
    v.player.durationChanged.emit(12000)
    seeks: list[int] = []
    monkeypatch.setattr(v.player, "setPosition", lambda pos: seeks.append(pos))
    monkeypatch.setattr(v.player, "position", lambda: 10000)
    v.seek_by(5000)
    monkeypatch.setattr(v.player, "position", lambda: 3000)
    v.seek_by(-5000)
    assert seeks == [12000, 0]


def test_play_button_toggles_and_reflects_state(qtbot, monkeypatch):
    v = VideoView()
    qtbot.addWidget(v)
    v.show()
    calls: list[int] = []
    monkeypatch.setattr(v, "toggle_play", lambda: calls.append(1))
    qtbot.mouseClick(v.play_button, Qt.MouseButton.LeftButton)
    assert calls == [1]

    v.player.playbackStateChanged.emit(QMediaPlayer.PlaybackState.PlayingState)
    assert v.play_button.text() == "❚❚"
    v.player.playbackStateChanged.emit(QMediaPlayer.PlaybackState.PausedState)
    assert v.play_button.text() == "▶"


def test_mute_button_toggles_audio(qtbot):
    v = VideoView()
    qtbot.addWidget(v)
    v.show()
    assert v._audio.isMuted() is False
    qtbot.mouseClick(v.mute_button, Qt.MouseButton.LeftButton)
    assert v._audio.isMuted() is True
    assert v.mute_button.text() == "🔇"
    qtbot.mouseClick(v.mute_button, Qt.MouseButton.LeftButton)
    assert v._audio.isMuted() is False
    assert v.mute_button.text() == "🔊"


def test_load_resets_slider(qtbot, tmp_path: Path):
    v = VideoView()
    qtbot.addWidget(v)
    v.player.durationChanged.emit(12000)
    v.player.positionChanged.emit(6000)
    clip = tmp_path / "clip.mp4"
    clip.write_bytes(b"\x00")
    v.load(clip)
    assert v.slider.value() == 0
    assert v.time_label.text() == "00:00 / 00:00"
