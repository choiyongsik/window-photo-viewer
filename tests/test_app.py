from pathlib import Path

from app import resolve_start_folder


def test_argv_folder_wins(tmp_path: Path):
    assert resolve_start_folder(["app.py", str(tmp_path)], fallback=Path("C:/x")) == tmp_path


def test_fallback_used_when_no_argv(tmp_path: Path):
    assert resolve_start_folder(["app.py"], fallback=tmp_path) == tmp_path


def test_nonexistent_paths_are_ignored(tmp_path: Path):
    assert resolve_start_folder(["app.py", str(tmp_path / "nope")], fallback=tmp_path / "gone") is None


def test_file_argument_uses_parent_folder(tmp_path: Path):
    f = tmp_path / "a.jpg"
    f.write_bytes(b"\x00")
    assert resolve_start_folder(["app.py", str(f)], fallback=None) == tmp_path
