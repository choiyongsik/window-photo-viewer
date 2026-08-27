# Photo Culling Viewer Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** JPEG/짧은 영상 폴더를 키보드로 빠르게 훑으며 별점·색 라벨을 매기고, 그 결과를 Lightroom Classic이 읽는 JPEG 내장 XMP에 기록하는 Windows 컬링 뷰어.

**Architecture:** `core/`(Qt 무의존: 스캔·메타데이터·썸네일·필터)와 `ui/`(PySide6: 모델·뷰·워커·메인윈도우) 2층 구조. UI는 `core`를 호출하는 얇은 층이며 모든 I/O(썸네일 생성, 풀사이즈 로드, 메타 쓰기, 폴더 스캔)는 `QThreadPool` 워커에서 실행되고 시그널로 결과를 돌려준다. 메모리 상태(`MediaItem`)를 즉시 갱신해 UI에 반영하고, 파일 기록은 백그라운드에서 따라간다.

**Tech Stack:** Python 3.12+, PySide6 (QtWidgets/QtMultimedia), pyexiv2 (JPEG XMP 쓰기), Pillow (썸네일·EXIF 읽기), imageio-ffmpeg (영상 프레임 추출), pytest, pytest-qt, PyInstaller.

**Spec:** `docs/superpowers/specs/2026-08-27-photo-culling-viewer-design.md`

## Global Constraints

- Python `>=3.12`. 런타임 의존: `PySide6`, `pyexiv2`, `Pillow`, `imageio-ffmpeg`, `defusedxml`(사이드카 XML 파싱 — stdlib 파서의 XXE 취약점 회피). 개발 의존: `pytest`, `pytest-qt`, `pyinstaller`.
- `core/` 아래 모듈은 **어떤 Qt 모듈도 import하지 않는다** (`PySide6` 문자열이 `core/` 안에 나오면 가드 테스트 실패).
- 지원 확장자: 이미지 `.jpg` `.jpeg`, 영상 `.mp4` `.mov` (대소문자 무시).
- 별점 값: `-1`(reject), `0`~`5`. 라벨: `""`, `"Red"`, `"Yellow"`, `"Green"`, `"Blue"`.
- JPEG 메타 기록은 **내장 XMP**(`Xmp.xmp.Rating`, `Xmp.xmp.Label`)만. 사이드카 `.xmp`는 **영상에만** 만든다(`clip.mp4` → `clip.xmp`).
- 값이 0/빈 라벨이면 태그를 **삭제**한다(pyexiv2에서는 `""`로 설정).
- 파일 쓰기는 `원본명.tmp`에 쓰고 `os.replace`로 교체. 원본 픽셀 데이터(SOS 세그먼트 이후 바이트) 불변.
- 썸네일: 긴 변 256px, 디스크 캐시 `%LOCALAPPDATA%\WindowPhotoViewer\thumbs\{sha1(path|mtime|size)}.jpg`.
- 풀사이즈 프리로드: 현재 ±2장, 메모리 LRU 최대 6장.
- 별점 입력 후 자동 전진 **기본 off**. 폴더를 열면 항상 첫 항목.
- 커밋 메시지 끝에 `Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>` 를 붙인다.
- 테스트는 활성화된 venv에서 `python -m pytest`로 실행한다. 헤드리스 Qt는 `tests/conftest.py`가 `QT_QPA_PLATFORM=offscreen`을 설정한다.

---

## File Structure

| 파일 | 책임 |
|---|---|
| `pyproject.toml` | 의존성, pytest 설정 |
| `app.py` | 진입점: `QApplication` 생성, 인자로 폴더 받기, `MainWindow` 표시 |
| `core/__init__.py` | 빈 파일 |
| `core/models.py` | `MediaKind`, `Label`, `ExifSummary`, `MediaItem`, 확장자 상수 |
| `core/scanner.py` | `natural_key`, `scan(folder)` |
| `core/metadata.py` | JPEG XMP 읽기/쓰기, 영상 사이드카 읽기/쓰기, EXIF 요약, `populate(item)` |
| `core/thumbnails.py` | `ThumbnailCache`, 이미지/영상 썸네일 생성, `default_cache_dir()` |
| `core/filters.py` | `Filter` (min_rating / rejected_only) |
| `ui/__init__.py` | 빈 파일 |
| `ui/workers.py` | `WorkerSignals`, `ScanJob`, `ThumbnailJob`, `ImageLoadJob`, `MetadataWriteJob` |
| `ui/image_cache.py` | `ImageCache` (QImage LRU) |
| `ui/media_list_model.py` | `MediaListModel` |
| `ui/thumb_delegate.py` | `ThumbDelegate` |
| `ui/thumb_views.py` | `ThumbListView`(베이스), `Filmstrip`, `GridView` |
| `ui/loupe_view.py` | `LoupeView` |
| `ui/video_view.py` | `VideoView` |
| `ui/main_window.py` | `MainWindow` — 상태·단축키·모드·필터·폴더 열기·상태 헤더 |
| `tests/conftest.py` | offscreen 설정 |
| `tests/helpers.py` | 테스트용 JPEG/영상 생성 헬퍼 |
| `tests/core/test_*.py` | core 단위 테스트 |
| `tests/ui/test_*.py` | pytest-qt 테스트 |
| `build/viewer.spec` | PyInstaller 스펙 |
| `README.md` | 사용법, 단축키, LR 호환 주의사항 |

---

### Task 1: 프로젝트 스캐폴딩과 테스트 환경

**Files:**
- Create: `pyproject.toml`, `core/__init__.py`, `ui/__init__.py`, `app.py`(임시 스텁), `tests/__init__.py`, `tests/core/__init__.py`, `tests/ui/__init__.py`, `tests/conftest.py`, `tests/helpers.py`, `tests/core/test_no_qt_in_core.py`, `tests/test_helpers.py`, `.gitignore`

**Interfaces:**
- Produces: `tests/helpers.py`의 `make_jpeg(path: Path, size=(64, 48), color=(200, 30, 30), orientation: int | None = None) -> Path`, `make_video(path: Path, seconds: float = 2.0) -> Path`, `scan_segment(data: bytes) -> bytes`

- [ ] **Step 1: Python 버전 확인 및 venv**

Run (PowerShell):
```powershell
py -0p
py -3.12 -c "import sys; print(sys.version)"
```
Expected: 3.12 인터프리터 버전 출력. **3.12가 없으면** `winget install Python.Python.3.12` 로 설치 후 다시 확인한다 (pyexiv2·PySide6 wheel이 최신 파이썬에는 늦게 나오므로 3.12로 고정).

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
```
이후 모든 명령은 활성화된 venv 안에서 `python ...`으로 실행한다.

- [ ] **Step 2: pyproject.toml 작성**

```toml
[build-system]
requires = ["setuptools>=68"]
build-backend = "setuptools.build_meta"

[project]
name = "window-photo-viewer"
version = "0.1.0"
description = "Keyboard-driven JPEG culling viewer that writes xmp:Rating for Lightroom Classic"
requires-python = ">=3.12"
dependencies = [
    "PySide6>=6.7",
    "pyexiv2>=2.12",
    "Pillow>=10.0",
    "imageio-ffmpeg>=0.5",
    "defusedxml>=0.7",
]

[project.optional-dependencies]
dev = [
    "pytest>=8",
    "pytest-qt>=4.4",
    "pyinstaller>=6",
]

[tool.setuptools]
packages = ["core", "ui"]
py-modules = ["app"]

[tool.pytest.ini_options]
testpaths = ["tests"]
qt_api = "pyside6"
addopts = "-q"
```

- [ ] **Step 3: 패키지 빈 파일, .gitignore, app.py 스텁**

```powershell
New-Item -ItemType Directory -Force core, ui, tests/core, tests/ui | Out-Null
foreach ($f in "core/__init__.py","ui/__init__.py","tests/__init__.py","tests/core/__init__.py","tests/ui/__init__.py") { if (-not (Test-Path $f)) { New-Item -ItemType File $f | Out-Null } }
```

`.gitignore`:
```
.venv/
__pycache__/
*.pyc
.pytest_cache/
dist/
build/*
!build/viewer.spec
*.egg-info/
.omc/
```

`app.py` (Task 13에서 교체):
```python
"""Entry point placeholder — replaced in Task 13."""


def main() -> int:
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 4: 의존성 설치**

```powershell
python -m pip install -e ".[dev]"
python -c "import PySide6, pyexiv2, PIL, imageio_ffmpeg, defusedxml; print('ok')"
```
Expected: `ok`. 실패하면 어떤 패키지가 wheel이 없는지 확인하고 Step 1의 파이썬 버전을 조정한다.

- [ ] **Step 5: conftest.py — offscreen Qt**

`tests/conftest.py`:
```python
import os

# Must be set before any PySide6 import happens in the test session.
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
```

- [ ] **Step 6: 테스트 헬퍼 실패 테스트 작성**

`tests/test_helpers.py`:
```python
from pathlib import Path

from PIL import Image

from tests.helpers import make_jpeg, make_video, scan_segment


def test_make_jpeg_creates_readable_jpeg(tmp_path: Path):
    p = make_jpeg(tmp_path / "a.jpg", size=(64, 48))
    with Image.open(p) as im:
        assert im.format == "JPEG"
        assert im.size == (64, 48)


def test_make_jpeg_writes_orientation(tmp_path: Path):
    p = make_jpeg(tmp_path / "a.jpg", orientation=6)
    with Image.open(p) as im:
        assert im.getexif()[0x0112] == 6


def test_scan_segment_starts_at_sos_marker(tmp_path: Path):
    data = make_jpeg(tmp_path / "a.jpg").read_bytes()
    seg = scan_segment(data)
    assert seg[:2] == b"\xff\xda"
    assert data.endswith(seg)


def test_make_video_creates_file(tmp_path: Path):
    p = make_video(tmp_path / "clip.mp4", seconds=1.0)
    assert p.exists() and p.stat().st_size > 0
```

- [ ] **Step 7: 실패 확인**

Run: `python -m pytest tests/test_helpers.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'tests.helpers'`

- [ ] **Step 8: helpers.py 구현**

`tests/helpers.py`:
```python
"""Test fixtures: synthesize JPEG/video files without checking binaries into git."""
from __future__ import annotations

import subprocess
from pathlib import Path

from PIL import Image


def make_jpeg(
    path: Path,
    size: tuple[int, int] = (64, 48),
    color: tuple[int, int, int] = (200, 30, 30),
    orientation: int | None = None,
) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    im = Image.new("RGB", size, color)
    exif = Image.Exif()
    if orientation is not None:
        exif[0x0112] = orientation
    im.save(path, "JPEG", quality=90, exif=exif.tobytes())
    return path


def make_video(path: Path, seconds: float = 2.0) -> Path:
    """Generate a tiny solid-color MP4 with the ffmpeg binary bundled by imageio-ffmpeg."""
    import imageio_ffmpeg

    path.parent.mkdir(parents=True, exist_ok=True)
    ffmpeg = imageio_ffmpeg.get_ffmpeg_exe()
    subprocess.run(
        [
            ffmpeg, "-y", "-loglevel", "error",
            "-f", "lavfi", "-i", f"color=c=blue:s=64x64:d={seconds}:r=10",
            "-pix_fmt", "yuv420p", str(path),
        ],
        check=True,
    )
    return path


def scan_segment(data: bytes) -> bytes:
    """Bytes from the JPEG SOS marker (0xFFDA) to the end — the compressed pixel data."""
    idx = data.find(b"\xff\xda")
    if idx < 0:
        raise ValueError("no SOS marker")
    return data[idx:]
```

- [ ] **Step 9: 통과 확인**

Run: `python -m pytest tests/test_helpers.py -v`
Expected: 4 PASS

- [ ] **Step 10: core에 Qt 금지 가드 테스트**

`tests/core/test_no_qt_in_core.py`:
```python
from pathlib import Path

CORE = Path(__file__).resolve().parents[2] / "core"


def test_core_never_imports_qt():
    offenders = []
    for p in CORE.rglob("*.py"):
        text = p.read_text(encoding="utf-8")
        if "PySide6" in text or "PyQt" in text:
            offenders.append(p.name)
    assert offenders == []
```

Run: `python -m pytest tests/core/test_no_qt_in_core.py -v`
Expected: PASS (core는 아직 빈 패키지)

- [ ] **Step 11: Commit**

```bash
git add pyproject.toml .gitignore app.py core ui tests
git commit -m "chore: 프로젝트 스캐폴딩, 테스트 헬퍼, core Qt 금지 가드

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 2: 데이터 모델과 폴더 스캐너

**Files:**
- Create: `core/models.py`, `core/scanner.py`
- Test: `tests/core/test_models.py`, `tests/core/test_scanner.py`

**Interfaces:**
- Produces (`core/models.py`):
  - `IMAGE_EXTENSIONS: frozenset[str]`, `VIDEO_EXTENSIONS: frozenset[str]`
  - `class MediaKind(Enum): IMAGE = "image"; VIDEO = "video"`
  - `class Label(Enum): NONE = ""; RED = "Red"; YELLOW = "Yellow"; GREEN = "Green"; BLUE = "Blue"` + `Label.from_xmp(value: str | None) -> Label` (모르는 값은 NONE)
  - `@dataclass(frozen=True) class ExifSummary(exposure_time: str | None, f_number: str | None, iso: int | None, focal_length: str | None, date_time_original: str | None, orientation: int = 1)` + `.format() -> str` (예 `"1/250  f/2.8  ISO 400  35mm"`, None 항목 생략)
  - `@dataclass class MediaItem(path: Path, kind: MediaKind, mtime: float, size: int, rating: int = 0, label: Label = Label.NONE, exif: ExifSummary | None = None, write_error: str | None = None)` + `is_rejected` property + `stars() -> str` (`"★★★☆☆"`, reject는 `"✕"`)
  - `kind_for(path: Path) -> MediaKind | None`
- Produces (`core/scanner.py`): `natural_key(name: str) -> list[int | str]`, `scan(folder: Path) -> list[MediaItem]` (폴더 없으면 `FileNotFoundError`, 파일이면 `NotADirectoryError`)

- [ ] **Step 1: 모델 테스트 작성**

`tests/core/test_models.py`:
```python
from pathlib import Path

from core.models import ExifSummary, Label, MediaItem, MediaKind, kind_for


def test_kind_for_extensions_case_insensitive():
    assert kind_for(Path("a.JPG")) is MediaKind.IMAGE
    assert kind_for(Path("a.jpeg")) is MediaKind.IMAGE
    assert kind_for(Path("a.MOV")) is MediaKind.VIDEO
    assert kind_for(Path("a.mp4")) is MediaKind.VIDEO
    assert kind_for(Path("a.png")) is None
    assert kind_for(Path("a.xmp")) is None


def test_label_from_xmp():
    assert Label.from_xmp("Red") is Label.RED
    assert Label.from_xmp("") is Label.NONE
    assert Label.from_xmp(None) is Label.NONE
    assert Label.from_xmp("Purple") is Label.NONE


def _item(rating: int = 0) -> MediaItem:
    return MediaItem(path=Path("x.jpg"), kind=MediaKind.IMAGE, mtime=0.0, size=1, rating=rating)


def test_stars_and_rejected():
    assert _item(3).stars() == "★★★☆☆"
    assert _item(0).stars() == "☆☆☆☆☆"
    assert _item(-1).stars() == "✕"
    assert _item(-1).is_rejected is True
    assert _item(2).is_rejected is False


def test_exif_summary_format_skips_none():
    s = ExifSummary(exposure_time="1/250", f_number="f/2.8", iso=400, focal_length="35mm", date_time_original=None)
    assert s.format() == "1/250  f/2.8  ISO 400  35mm"
    assert ExifSummary(None, None, None, None, None).format() == ""
    assert s.orientation == 1
```

- [ ] **Step 2: 실패 확인**

Run: `python -m pytest tests/core/test_models.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'core.models'`

- [ ] **Step 3: models.py 구현**

`core/models.py`:
```python
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from pathlib import Path

IMAGE_EXTENSIONS: frozenset[str] = frozenset({".jpg", ".jpeg"})
VIDEO_EXTENSIONS: frozenset[str] = frozenset({".mp4", ".mov"})


class MediaKind(Enum):
    IMAGE = "image"
    VIDEO = "video"


class Label(Enum):
    NONE = ""
    RED = "Red"
    YELLOW = "Yellow"
    GREEN = "Green"
    BLUE = "Blue"

    @classmethod
    def from_xmp(cls, value: str | None) -> "Label":
        try:
            return cls(value or "")
        except ValueError:
            return cls.NONE


def kind_for(path: Path) -> MediaKind | None:
    ext = path.suffix.lower()
    if ext in IMAGE_EXTENSIONS:
        return MediaKind.IMAGE
    if ext in VIDEO_EXTENSIONS:
        return MediaKind.VIDEO
    return None


@dataclass(frozen=True)
class ExifSummary:
    exposure_time: str | None
    f_number: str | None
    iso: int | None
    focal_length: str | None
    date_time_original: str | None
    orientation: int = 1

    def format(self) -> str:
        parts = [
            self.exposure_time,
            self.f_number,
            f"ISO {self.iso}" if self.iso is not None else None,
            self.focal_length,
        ]
        return "  ".join(p for p in parts if p)


@dataclass
class MediaItem:
    path: Path
    kind: MediaKind
    mtime: float
    size: int
    rating: int = 0
    label: Label = Label.NONE
    exif: ExifSummary | None = None
    write_error: str | None = None

    @property
    def is_rejected(self) -> bool:
        return self.rating == -1

    def stars(self) -> str:
        if self.is_rejected:
            return "✕"
        return "★" * self.rating + "☆" * (5 - self.rating)
```

- [ ] **Step 4: 통과 확인**

Run: `python -m pytest tests/core/test_models.py -v`
Expected: 4 PASS

- [ ] **Step 5: 스캐너 테스트 작성**

`tests/core/test_scanner.py`:
```python
from pathlib import Path

import pytest

from core.models import MediaKind
from core.scanner import natural_key, scan
from tests.helpers import make_jpeg


def test_natural_key_orders_numbers_numerically():
    names = ["IMG_10.jpg", "IMG_2.jpg", "IMG_1.jpg", "img_3.JPG"]
    assert sorted(names, key=natural_key) == ["IMG_1.jpg", "IMG_2.jpg", "img_3.JPG", "IMG_10.jpg"]


def test_scan_filters_extensions_and_sorts(tmp_path: Path):
    make_jpeg(tmp_path / "IMG_10.jpg")
    make_jpeg(tmp_path / "IMG_2.JPEG")
    (tmp_path / "clip.MP4").write_bytes(b"\x00" * 10)
    (tmp_path / "notes.txt").write_text("x")
    (tmp_path / "IMG_2.xmp").write_text("<x/>")
    (tmp_path / "sub").mkdir()
    make_jpeg(tmp_path / "sub" / "nested.jpg")  # not recursive

    items = scan(tmp_path)

    assert [i.path.name for i in items] == ["clip.MP4", "IMG_2.JPEG", "IMG_10.jpg"]
    assert [i.kind for i in items] == [MediaKind.VIDEO, MediaKind.IMAGE, MediaKind.IMAGE]
    assert all(i.rating == 0 and i.exif is None for i in items)
    assert items[1].size == (tmp_path / "IMG_2.JPEG").stat().st_size
    assert items[1].mtime == (tmp_path / "IMG_2.JPEG").stat().st_mtime


def test_scan_skips_hidden_and_tmp_files(tmp_path: Path):
    make_jpeg(tmp_path / ".hidden.jpg")
    make_jpeg(tmp_path / "keep.jpg")
    (tmp_path / "keep.jpg.tmp").write_bytes(b"partial")
    assert [i.path.name for i in scan(tmp_path)] == ["keep.jpg"]


def test_scan_empty_folder(tmp_path: Path):
    assert scan(tmp_path) == []


def test_scan_errors(tmp_path: Path):
    with pytest.raises(FileNotFoundError):
        scan(tmp_path / "nope")
    f = make_jpeg(tmp_path / "a.jpg")
    with pytest.raises(NotADirectoryError):
        scan(f)
```

- [ ] **Step 6: 실패 확인**

Run: `python -m pytest tests/core/test_scanner.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'core.scanner'`

- [ ] **Step 7: scanner.py 구현**

`core/scanner.py`:
```python
from __future__ import annotations

import re
from pathlib import Path

from core.models import MediaItem, kind_for

_NUM_RE = re.compile(r"(\d+)")


def natural_key(name: str) -> list[int | str]:
    """'IMG_10' sorts after 'IMG_2'. Case-insensitive on the text parts."""
    return [int(tok) if tok.isdigit() else tok.lower() for tok in _NUM_RE.split(name)]


def scan(folder: Path) -> list[MediaItem]:
    """Non-recursive listing of supported media in *folder*, naturally sorted by file name.

    Ratings/labels/EXIF are NOT read here — see core.metadata.populate.
    """
    if not folder.exists():
        raise FileNotFoundError(folder)
    if not folder.is_dir():
        raise NotADirectoryError(folder)

    items: list[MediaItem] = []
    for entry in folder.iterdir():
        if not entry.is_file() or entry.name.startswith("."):
            continue
        kind = kind_for(entry)
        if kind is None:
            continue
        st = entry.stat()
        items.append(MediaItem(path=entry, kind=kind, mtime=st.st_mtime, size=st.st_size))
    items.sort(key=lambda i: natural_key(i.path.name))
    return items
```

- [ ] **Step 8: 통과 확인**

Run: `python -m pytest tests/core -v`
Expected: 모두 PASS (Qt 가드 포함)

- [ ] **Step 9: Commit**

```bash
git add core/models.py core/scanner.py tests/core/test_models.py tests/core/test_scanner.py
git commit -m "feat(core): MediaItem 모델과 폴더 스캐너

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---
### Task 3: 메타데이터 — JPEG 내장 XMP, 영상 사이드카, EXIF 요약

**Files:**
- Create: `core/metadata.py`
- Test: `tests/core/test_metadata.py`

**Interfaces:**
- Consumes: `core.models.{MediaItem, MediaKind, Label, ExifSummary}`, `tests.helpers.{make_jpeg, scan_segment}`
- Produces (`core/metadata.py`):
  - `class MetadataError(Exception)`
  - `XMP_RATING = "Xmp.xmp.Rating"`, `XMP_LABEL = "Xmp.xmp.Label"`
  - `sidecar_path(path: Path) -> Path` — `clip.mp4` → `clip.xmp`
  - `read_rating_label(path: Path, kind: MediaKind) -> tuple[int, Label]` — 읽기 실패는 `(0, Label.NONE)`
  - `write_rating_label(path: Path, kind: MediaKind, rating: int, label: Label) -> None` — 실패 시 `MetadataError`
  - `read_exif_summary(path: Path) -> ExifSummary | None`
  - `populate(item: MediaItem) -> None` — rating/label/exif를 채움 (영상은 exif None)

- [ ] **Step 1: JPEG 라운드트립 테스트 작성**

`tests/core/test_metadata.py`:
```python
from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest
from PIL import Image
from PIL.TiffImagePlugin import IFDRational

from core.metadata import (
    MetadataError,
    populate,
    read_exif_summary,
    read_rating_label,
    sidecar_path,
    write_rating_label,
)
from core.models import Label, MediaItem, MediaKind
from tests.helpers import make_jpeg, scan_segment


def _make_jpeg_with_exif(path: Path) -> Path:
    im = Image.new("RGB", (32, 32), (0, 0, 0))
    exif = Image.Exif()
    exif[0x0112] = 8
    ifd = exif.get_ifd(0x8769)
    ifd[0x829A] = IFDRational(1, 250)   # ExposureTime
    ifd[0x829D] = IFDRational(28, 10)   # FNumber
    ifd[0x8827] = 400                   # ISO
    ifd[0x920A] = IFDRational(35, 1)    # FocalLength
    ifd[0x9003] = "2026:08:27 10:00:00" # DateTimeOriginal
    im.save(path, "JPEG", exif=exif.tobytes())
    return path


def test_jpeg_defaults_when_no_xmp(tmp_path: Path):
    p = make_jpeg(tmp_path / "a.jpg")
    assert read_rating_label(p, MediaKind.IMAGE) == (0, Label.NONE)


def test_jpeg_roundtrip_rating_and_label(tmp_path: Path):
    p = make_jpeg(tmp_path / "a.jpg")
    write_rating_label(p, MediaKind.IMAGE, 3, Label.RED)
    assert read_rating_label(p, MediaKind.IMAGE) == (3, Label.RED)

    write_rating_label(p, MediaKind.IMAGE, -1, Label.NONE)
    assert read_rating_label(p, MediaKind.IMAGE) == (-1, Label.NONE)


def test_jpeg_write_keeps_pixel_data_and_exif(tmp_path: Path):
    p = _make_jpeg_with_exif(tmp_path / "a.jpg")
    before = p.read_bytes()
    write_rating_label(p, MediaKind.IMAGE, 5, Label.GREEN)
    after = p.read_bytes()

    assert scan_segment(before) == scan_segment(after)
    with Image.open(p) as im:
        assert im.getexif()[0x0112] == 8
    assert not (tmp_path / "a.jpg.tmp").exists()


def test_jpeg_zero_rating_removes_tags(tmp_path: Path):
    import pyexiv2

    p = make_jpeg(tmp_path / "a.jpg")
    write_rating_label(p, MediaKind.IMAGE, 4, Label.BLUE)
    write_rating_label(p, MediaKind.IMAGE, 0, Label.NONE)
    with pyexiv2.ImageData(p.read_bytes()) as img:
        xmp = img.read_xmp()
    assert "Xmp.xmp.Rating" not in xmp
    assert "Xmp.xmp.Label" not in xmp


def test_jpeg_write_failure_raises_and_leaves_no_tmp(tmp_path: Path, monkeypatch):
    p = make_jpeg(tmp_path / "a.jpg")
    original = p.read_bytes()

    def boom(src, dst):
        raise PermissionError("locked")

    monkeypatch.setattr(os, "replace", boom)
    with pytest.raises(MetadataError):
        write_rating_label(p, MediaKind.IMAGE, 2, Label.NONE)
    assert p.read_bytes() == original
    assert not (tmp_path / "a.jpg.tmp").exists()


def test_corrupt_jpeg_reads_as_default_and_write_raises(tmp_path: Path):
    p = tmp_path / "bad.jpg"
    p.write_bytes(b"not a jpeg at all")
    assert read_rating_label(p, MediaKind.IMAGE) == (0, Label.NONE)
    with pytest.raises(MetadataError):
        write_rating_label(p, MediaKind.IMAGE, 1, Label.NONE)


@pytest.mark.skipif(shutil.which("exiftool") is None, reason="exiftool not installed")
def test_exiftool_reads_our_rating(tmp_path: Path):
    p = make_jpeg(tmp_path / "a.jpg")
    write_rating_label(p, MediaKind.IMAGE, 4, Label.YELLOW)
    out = subprocess.run(
        ["exiftool", "-s3", "-XMP:Rating", "-XMP:Label", str(p)],
        capture_output=True, text=True, check=True,
    ).stdout.split()
    assert out == ["4", "Yellow"]


def test_video_sidecar_roundtrip(tmp_path: Path):
    v = tmp_path / "clip.mp4"
    v.write_bytes(b"\x00" * 16)
    assert sidecar_path(v) == tmp_path / "clip.xmp"
    assert read_rating_label(v, MediaKind.VIDEO) == (0, Label.NONE)

    write_rating_label(v, MediaKind.VIDEO, 2, Label.BLUE)
    assert sidecar_path(v).exists()
    assert read_rating_label(v, MediaKind.VIDEO) == (2, Label.BLUE)
    assert v.read_bytes() == b"\x00" * 16  # video itself untouched


def test_video_sidecar_zero_does_not_create_file(tmp_path: Path):
    v = tmp_path / "clip.mov"
    v.write_bytes(b"\x00")
    write_rating_label(v, MediaKind.VIDEO, 0, Label.NONE)
    assert not sidecar_path(v).exists()


def test_video_sidecar_reads_element_form(tmp_path: Path):
    v = tmp_path / "clip.mp4"
    v.write_bytes(b"\x00")
    sidecar_path(v).write_text(
        '<x:xmpmeta xmlns:x="adobe:ns:meta/">'
        '<rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#">'
        '<rdf:Description rdf:about="" xmlns:xmp="http://ns.adobe.com/xap/1.0/">'
        "<xmp:Rating>5</xmp:Rating><xmp:Label>Red</xmp:Label>"
        "</rdf:Description></rdf:RDF></x:xmpmeta>",
        encoding="utf-8",
    )
    assert read_rating_label(v, MediaKind.VIDEO) == (5, Label.RED)


def test_read_exif_summary(tmp_path: Path):
    p = _make_jpeg_with_exif(tmp_path / "a.jpg")
    s = read_exif_summary(p)
    assert s is not None
    assert s.exposure_time == "1/250"
    assert s.f_number == "f/2.8"
    assert s.iso == 400
    assert s.focal_length == "35mm"
    assert s.date_time_original == "2026:08:27 10:00:00"
    assert s.orientation == 8


def test_read_exif_summary_without_exif_is_none_or_empty(tmp_path: Path):
    p = make_jpeg(tmp_path / "a.jpg")
    s = read_exif_summary(p)
    assert s is None or s.format() == ""


def test_populate_fills_item(tmp_path: Path):
    p = _make_jpeg_with_exif(tmp_path / "a.jpg")
    write_rating_label(p, MediaKind.IMAGE, 3, Label.RED)
    item = MediaItem(path=p, kind=MediaKind.IMAGE, mtime=0.0, size=1)
    populate(item)
    assert (item.rating, item.label) == (3, Label.RED)
    assert item.exif is not None and item.exif.iso == 400

    v = tmp_path / "clip.mp4"
    v.write_bytes(b"\x00")
    vitem = MediaItem(path=v, kind=MediaKind.VIDEO, mtime=0.0, size=1)
    populate(vitem)
    assert vitem.exif is None and vitem.rating == 0
```

- [ ] **Step 2: 실패 확인**

Run: `python -m pytest tests/core/test_metadata.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'core.metadata'`

- [ ] **Step 3: metadata.py 구현**

`core/metadata.py`:
```python
"""Rating/label persistence.

JPEG  → embedded XMP packet (Xmp.xmp.Rating / Xmp.xmp.Label) via pyexiv2. Lightroom Classic reads this.
Video → sidecar `<stem>.xmp` written by us (exiv2 cannot write MP4/MOV). Viewer-internal.
"""
from __future__ import annotations

import os
from pathlib import Path

import pyexiv2
from defusedxml import ElementTree as ET  # stdlib ET is vulnerable to XXE / entity expansion
from PIL import Image

from core.models import ExifSummary, Label, MediaItem, MediaKind

XMP_RATING = "Xmp.xmp.Rating"
XMP_LABEL = "Xmp.xmp.Label"
_XMP_NS = "http://ns.adobe.com/xap/1.0/"
_RDF_NS = "http://www.w3.org/1999/02/22-rdf-syntax-ns#"


class MetadataError(Exception):
    """Raised when a rating/label could not be written."""


def sidecar_path(path: Path) -> Path:
    return path.with_suffix(".xmp")


# ---------- read ----------

def read_rating_label(path: Path, kind: MediaKind) -> tuple[int, Label]:
    try:
        if kind is MediaKind.VIDEO:
            return _read_sidecar(sidecar_path(path))
        return _read_jpeg(path)
    except Exception:
        return 0, Label.NONE


def _parse_rating(value: str | None) -> int:
    if value is None:
        return 0
    try:
        r = int(float(str(value).strip()))
    except ValueError:
        return 0
    return max(-1, min(5, r))


def _read_jpeg(path: Path) -> tuple[int, Label]:
    with pyexiv2.ImageData(path.read_bytes()) as img:
        xmp = img.read_xmp()
    return _parse_rating(xmp.get(XMP_RATING)), Label.from_xmp(xmp.get(XMP_LABEL))


def _read_sidecar(sc: Path) -> tuple[int, Label]:
    if not sc.exists():
        return 0, Label.NONE
    root = ET.parse(sc).getroot()
    rating: str | None = None
    label: str | None = None
    for desc in root.iter(f"{{{_RDF_NS}}}Description"):
        rating = rating or desc.get(f"{{{_XMP_NS}}}Rating")
        label = label or desc.get(f"{{{_XMP_NS}}}Label")
        r_el = desc.find(f"{{{_XMP_NS}}}Rating")
        l_el = desc.find(f"{{{_XMP_NS}}}Label")
        if r_el is not None and r_el.text:
            rating = rating or r_el.text
        if l_el is not None and l_el.text:
            label = label or l_el.text
    return _parse_rating(rating), Label.from_xmp(label)


# ---------- write ----------

def write_rating_label(path: Path, kind: MediaKind, rating: int, label: Label) -> None:
    if not -1 <= rating <= 5:
        raise MetadataError(f"rating out of range: {rating}")
    try:
        if kind is MediaKind.VIDEO:
            _write_sidecar(sidecar_path(path), rating, label)
        else:
            _write_jpeg(path, rating, label)
    except MetadataError:
        raise
    except Exception as exc:  # pyexiv2 raises RuntimeError on bad data
        raise MetadataError(f"{path.name}: {exc}") from exc


def _write_jpeg(path: Path, rating: int, label: Label) -> None:
    with pyexiv2.ImageData(path.read_bytes()) as img:
        img.modify_xmp({
            XMP_RATING: str(rating) if rating != 0 else "",   # "" deletes the tag
            XMP_LABEL: label.value,                          # "" deletes the tag
        })
        new_bytes = img.get_bytes()
    _atomic_write(path, new_bytes)


def _write_sidecar(sc: Path, rating: int, label: Label) -> None:
    attrs = ""
    if rating != 0:
        attrs += f' xmp:Rating="{rating}"'
    if label is not Label.NONE:
        attrs += f' xmp:Label="{label.value}"'
    if not attrs and not sc.exists():
        return  # nothing to record, do not litter the folder
    xml = (
        '<?xpacket begin="﻿" id="W5M0MpCehiHzreSzNTczkc9d"?>\n'
        '<x:xmpmeta xmlns:x="adobe:ns:meta/">\n'
        f' <rdf:RDF xmlns:rdf="{_RDF_NS}">\n'
        f'  <rdf:Description rdf:about="" xmlns:xmp="{_XMP_NS}"{attrs}/>\n'
        " </rdf:RDF>\n"
        "</x:xmpmeta>\n"
        '<?xpacket end="w"?>\n'
    )
    _atomic_write(sc, xml.encode("utf-8"))


def _atomic_write(target: Path, data: bytes) -> None:
    tmp = target.with_name(target.name + ".tmp")
    try:
        tmp.write_bytes(data)
        os.replace(tmp, target)
    except OSError as exc:
        tmp.unlink(missing_ok=True)
        raise MetadataError(f"{target.name}: {exc}") from exc


# ---------- EXIF ----------

def _fmt_exposure(v) -> str | None:
    if v is None:
        return None
    f = float(v)
    if f <= 0:
        return None
    return f"{f:g}s" if f >= 1 else f"1/{round(1 / f)}"


def _fmt_fnumber(v) -> str | None:
    return None if v is None else f"f/{float(v):g}"


def _fmt_focal(v) -> str | None:
    return None if v is None else f"{float(v):g}mm"


def _to_int(v) -> int | None:
    if v is None:
        return None
    if isinstance(v, (tuple, list)):
        v = v[0] if v else None
    try:
        return int(v)
    except (TypeError, ValueError):
        return None


def read_exif_summary(path: Path) -> ExifSummary | None:
    try:
        with Image.open(path) as im:
            exif = im.getexif()
            if not exif:
                return None
            ifd = exif.get_ifd(0x8769)
            return ExifSummary(
                exposure_time=_fmt_exposure(ifd.get(0x829A)),
                f_number=_fmt_fnumber(ifd.get(0x829D)),
                iso=_to_int(ifd.get(0x8827)),
                focal_length=_fmt_focal(ifd.get(0x920A)),
                date_time_original=ifd.get(0x9003) or None,
                orientation=int(exif.get(0x0112) or 1),
            )
    except Exception:
        return None


def populate(item: MediaItem) -> None:
    item.rating, item.label = read_rating_label(item.path, item.kind)
    item.exif = read_exif_summary(item.path) if item.kind is MediaKind.IMAGE else None
```

- [ ] **Step 4: 통과 확인**

Run: `python -m pytest tests/core/test_metadata.py -v`
Expected: 전부 PASS (`test_exiftool_reads_our_rating`은 exiftool 없으면 SKIP). `test_jpeg_zero_rating_removes_tags`가 실패하면 pyexiv2가 `""`를 삭제로 처리하지 않는 버전이다 — 그 경우 `_write_jpeg`에서 값이 `""`인 키를 딕셔너리에서 빼고 `img.clear_xmp()` 대신 `img.modify_xmp({key: None})`이 아닌 **읽은 xmp에서 해당 키를 제거한 뒤 `clear_xmp()` + `modify_xmp(remaining)`** 으로 다시 쓴다.

- [ ] **Step 5: Commit**

```bash
git add core/metadata.py tests/core/test_metadata.py
git commit -m "feat(core): JPEG 내장 XMP 별점/라벨 읽기·쓰기, 영상 사이드카, EXIF 요약

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 4: 썸네일 생성과 디스크 캐시

**Files:**
- Create: `core/thumbnails.py`
- Test: `tests/core/test_thumbnails.py`

**Interfaces:**
- Consumes: `core.models.{MediaItem, MediaKind}`, `tests.helpers.{make_jpeg, make_video}`
- Produces (`core/thumbnails.py`):
  - `THUMB_SIZE = 256`
  - `class ThumbnailError(Exception)`
  - `default_cache_dir() -> Path` — `%LOCALAPPDATA%\WindowPhotoViewer\thumbs`
  - `make_image_thumbnail(src: Path, dst: Path, size: int = THUMB_SIZE) -> None`
  - `make_video_thumbnail(src: Path, dst: Path, size: int = THUMB_SIZE) -> None`
  - `class ThumbnailCache(cache_dir: Path)` with `cache_path(item: MediaItem) -> Path`, `get_or_create(item: MediaItem) -> Path` (실패 시 `ThumbnailError`)

- [ ] **Step 1: 테스트 작성**

`tests/core/test_thumbnails.py`:
```python
from __future__ import annotations

import os
from pathlib import Path

import pytest
from PIL import Image

from core.models import MediaItem, MediaKind
from core.thumbnails import (
    THUMB_SIZE,
    ThumbnailCache,
    ThumbnailError,
    default_cache_dir,
    make_image_thumbnail,
    make_video_thumbnail,
)
from tests.helpers import make_jpeg, make_video


def _item(path: Path, kind: MediaKind = MediaKind.IMAGE) -> MediaItem:
    st = path.stat()
    return MediaItem(path=path, kind=kind, mtime=st.st_mtime, size=st.st_size)


def test_default_cache_dir_uses_localappdata(monkeypatch, tmp_path: Path):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    assert default_cache_dir() == tmp_path / "WindowPhotoViewer" / "thumbs"


def test_image_thumbnail_longest_side_256(tmp_path: Path):
    src = make_jpeg(tmp_path / "a.jpg", size=(1200, 600))
    dst = tmp_path / "t.jpg"
    make_image_thumbnail(src, dst)
    with Image.open(dst) as im:
        assert im.size == (256, 128)
        assert im.format == "JPEG"


def test_image_thumbnail_applies_exif_orientation(tmp_path: Path):
    src = make_jpeg(tmp_path / "a.jpg", size=(400, 200), orientation=6)
    dst = tmp_path / "t.jpg"
    make_image_thumbnail(src, dst)
    with Image.open(dst) as im:
        assert im.size == (128, 256)


def test_image_thumbnail_does_not_upscale(tmp_path: Path):
    src = make_jpeg(tmp_path / "a.jpg", size=(64, 48))
    dst = tmp_path / "t.jpg"
    make_image_thumbnail(src, dst)
    with Image.open(dst) as im:
        assert im.size == (64, 48)


def test_video_thumbnail_extracts_frame(tmp_path: Path):
    src = make_video(tmp_path / "clip.mp4", seconds=2.0)
    dst = tmp_path / "t.jpg"
    make_video_thumbnail(src, dst)
    with Image.open(dst) as im:
        assert im.format == "JPEG"
        assert max(im.size) <= THUMB_SIZE
        assert im.getpixel((im.width // 2, im.height // 2))[2] > 150  # blue clip


def test_video_thumbnail_short_clip_falls_back_to_start(tmp_path: Path):
    src = make_video(tmp_path / "short.mp4", seconds=0.3)
    dst = tmp_path / "t.jpg"
    make_video_thumbnail(src, dst)
    assert dst.exists()


def test_video_thumbnail_error_on_garbage(tmp_path: Path):
    src = tmp_path / "bad.mp4"
    src.write_bytes(b"\x00" * 100)
    with pytest.raises(ThumbnailError):
        make_video_thumbnail(src, tmp_path / "t.jpg")


def test_cache_key_depends_on_path_mtime_size(tmp_path: Path):
    cache = ThumbnailCache(tmp_path / "cache")
    a = make_jpeg(tmp_path / "a.jpg")
    item = _item(a)
    p1 = cache.cache_path(item)
    assert p1.parent == tmp_path / "cache" and p1.suffix == ".jpg"

    item2 = MediaItem(path=a, kind=MediaKind.IMAGE, mtime=item.mtime + 1, size=item.size)
    assert cache.cache_path(item2) != p1


def test_cache_hit_does_not_regenerate(tmp_path: Path):
    cache = ThumbnailCache(tmp_path / "cache")
    item = _item(make_jpeg(tmp_path / "a.jpg", size=(500, 500)))
    p = cache.get_or_create(item)
    assert p.exists()
    first_mtime_ns = p.stat().st_mtime_ns
    p2 = cache.get_or_create(item)
    assert p2 == p and p2.stat().st_mtime_ns == first_mtime_ns


def test_cache_handles_video_and_corrupt_image(tmp_path: Path):
    cache = ThumbnailCache(tmp_path / "cache")
    v = _item(make_video(tmp_path / "clip.mp4", seconds=1.0), MediaKind.VIDEO)
    assert cache.get_or_create(v).exists()

    bad = tmp_path / "bad.jpg"
    bad.write_bytes(b"nope")
    with pytest.raises(ThumbnailError):
        cache.get_or_create(_item(bad))
    assert not any(tmp_path.joinpath("cache").glob("*.part.jpg"))
```

- [ ] **Step 2: 실패 확인**

Run: `python -m pytest tests/core/test_thumbnails.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'core.thumbnails'`

- [ ] **Step 3: thumbnails.py 구현**

`core/thumbnails.py`:
```python
from __future__ import annotations

import hashlib
import os
import subprocess
from pathlib import Path

from PIL import Image, ImageOps

from core.models import MediaItem, MediaKind

THUMB_SIZE = 256
_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


class ThumbnailError(Exception):
    pass


def default_cache_dir() -> Path:
    base = os.environ.get("LOCALAPPDATA")
    root = Path(base) if base else Path.home() / "AppData" / "Local"
    return root / "WindowPhotoViewer" / "thumbs"


def make_image_thumbnail(src: Path, dst: Path, size: int = THUMB_SIZE) -> None:
    try:
        with Image.open(src) as im:
            # JPEG DCT-domain downscale: decodes at 1/2, 1/4, 1/8 — far cheaper than full decode.
            im.draft("RGB", (size * 2, size * 2))
            im = ImageOps.exif_transpose(im) or im
            im.thumbnail((size, size), Image.Resampling.LANCZOS)
            im.convert("RGB").save(dst, "JPEG", quality=85)
    except Exception as exc:
        raise ThumbnailError(f"{src.name}: {exc}") from exc


def make_video_thumbnail(src: Path, dst: Path, size: int = THUMB_SIZE) -> None:
    import imageio_ffmpeg

    ffmpeg = imageio_ffmpeg.get_ffmpeg_exe()
    frame = dst.with_name(dst.stem + ".frame.jpg")
    try:
        for seek in ("1", "0"):  # 1s in; fall back to first frame for very short clips
            frame.unlink(missing_ok=True)
            proc = subprocess.run(
                [ffmpeg, "-y", "-loglevel", "error", "-ss", seek, "-i", str(src),
                 "-frames:v", "1", "-q:v", "3", str(frame)],
                capture_output=True, creationflags=_NO_WINDOW,
            )
            if proc.returncode == 0 and frame.exists() and frame.stat().st_size > 0:
                make_image_thumbnail(frame, dst, size)
                return
        raise ThumbnailError(f"{src.name}: ffmpeg produced no frame")
    finally:
        frame.unlink(missing_ok=True)


class ThumbnailCache:
    def __init__(self, cache_dir: Path):
        self.cache_dir = cache_dir

    def cache_path(self, item: MediaItem) -> Path:
        key = hashlib.sha1(f"{item.path}|{item.mtime}|{item.size}".encode("utf-8")).hexdigest()
        return self.cache_dir / f"{key}.jpg"

    def get_or_create(self, item: MediaItem) -> Path:
        dst = self.cache_path(item)
        if dst.exists():
            return dst
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        part = dst.with_name(dst.stem + ".part.jpg")
        try:
            if item.kind is MediaKind.VIDEO:
                make_video_thumbnail(item.path, part)
            else:
                make_image_thumbnail(item.path, part)
            os.replace(part, dst)
        except ThumbnailError:
            part.unlink(missing_ok=True)
            raise
        except OSError as exc:
            part.unlink(missing_ok=True)
            raise ThumbnailError(f"{item.path.name}: {exc}") from exc
        return dst
```

- [ ] **Step 4: 통과 확인**

Run: `python -m pytest tests/core/test_thumbnails.py -v`
Expected: 전부 PASS. 영상 테스트가 `imageio_ffmpeg` 바이너리 다운로드 문제로 실패하면 `python -c "import imageio_ffmpeg; print(imageio_ffmpeg.get_ffmpeg_exe())"` 로 경로를 확인한다(pip wheel에 포함되어 있어야 함).

- [ ] **Step 5: Commit**

```bash
git add core/thumbnails.py tests/core/test_thumbnails.py
git commit -m "feat(core): 썸네일 생성(이미지 draft 디코딩, 영상 ffmpeg 프레임)과 디스크 캐시

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 5: 필터

**Files:**
- Create: `core/filters.py`
- Test: `tests/core/test_filters.py`

**Interfaces:**
- Consumes: `core.models.MediaItem`
- Produces (`core/filters.py`):
  - `@dataclass(frozen=True) class Filter(min_rating: int | None = None, rejected_only: bool = False)` with `matches(item) -> bool`, `apply(items: list[MediaItem]) -> list[int]` (원본 인덱스), `describe() -> str` (`""`, `"★3+"`, `"reject"`), `is_active` property
  - `NO_FILTER = Filter()`

- [ ] **Step 1: 테스트 작성**

`tests/core/test_filters.py`:
```python
from pathlib import Path

from core.filters import NO_FILTER, Filter
from core.models import MediaItem, MediaKind


def _items(*ratings: int) -> list[MediaItem]:
    return [MediaItem(path=Path(f"{i}.jpg"), kind=MediaKind.IMAGE, mtime=0, size=1, rating=r)
            for i, r in enumerate(ratings)]


def test_no_filter_returns_all_indices():
    assert NO_FILTER.apply(_items(0, 3, -1)) == [0, 1, 2]
    assert NO_FILTER.is_active is False
    assert NO_FILTER.describe() == ""


def test_min_rating_excludes_lower_and_rejected():
    f = Filter(min_rating=3)
    assert f.apply(_items(0, 3, 5, 2, -1)) == [1, 2]
    assert f.is_active is True
    assert f.describe() == "★3+"


def test_rejected_only():
    f = Filter(rejected_only=True)
    assert f.apply(_items(0, -1, 5, -1)) == [1, 3]
    assert f.describe() == "reject"


def test_rejected_only_wins_over_min_rating():
    f = Filter(min_rating=2, rejected_only=True)
    assert f.apply(_items(3, -1)) == [1]
```

- [ ] **Step 2: 실패 확인**

Run: `python -m pytest tests/core/test_filters.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'core.filters'`

- [ ] **Step 3: filters.py 구현**

`core/filters.py`:
```python
from __future__ import annotations

from dataclasses import dataclass

from core.models import MediaItem


@dataclass(frozen=True)
class Filter:
    min_rating: int | None = None
    rejected_only: bool = False

    @property
    def is_active(self) -> bool:
        return self.rejected_only or self.min_rating is not None

    def matches(self, item: MediaItem) -> bool:
        if self.rejected_only:
            return item.is_rejected
        if self.min_rating is not None:
            return item.rating >= self.min_rating
        return True

    def apply(self, items: list[MediaItem]) -> list[int]:
        return [i for i, item in enumerate(items) if self.matches(item)]

    def describe(self) -> str:
        if self.rejected_only:
            return "reject"
        if self.min_rating is not None:
            return f"★{self.min_rating}+"
        return ""


NO_FILTER = Filter()
```

- [ ] **Step 4: 통과 확인**

Run: `python -m pytest tests/core -v`
Expected: 전부 PASS

- [ ] **Step 5: Commit**

```bash
git add core/filters.py tests/core/test_filters.py
git commit -m "feat(core): 별점/reject 필터

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---
### Task 6: 백그라운드 워커와 풀사이즈 이미지 LRU

**Files:**
- Create: `ui/workers.py`, `ui/image_cache.py`
- Test: `tests/ui/test_workers.py`, `tests/ui/test_image_cache.py`

**Interfaces:**
- Consumes: `core.scanner.scan`, `core.metadata.{populate, write_rating_label, MetadataError}`, `core.thumbnails.{ThumbnailCache, ThumbnailError}`, `core.models.{MediaItem, MediaKind, Label}`
- Produces (`ui/workers.py`):
  - `class WorkerSignals(QObject)` — `scan_finished = Signal(object)` (list[MediaItem]), `scan_failed = Signal(str)`, `thumbnail_ready = Signal(object, str)` (MediaItem, 캐시 파일 경로), `thumbnail_failed = Signal(object, str)`, `image_ready = Signal(object, QImage)`, `image_failed = Signal(object, str)`, `write_finished = Signal(object, str)` (MediaItem, `""`면 성공, 아니면 에러 메시지)
  - 결과 시그널은 **인덱스가 아니라 `MediaItem` 객체**를 싣는다. 폴더를 다시 열어 목록이 바뀐 뒤 도착하는 오래된 결과는 수신측(MainWindow)이 `id(item)`으로 현재 목록에 없음을 알고 버린다.
  - `class ScanJob(QRunnable)(folder: Path, signals: WorkerSignals)`
  - `class ThumbnailJob(QRunnable)(item: MediaItem, cache: ThumbnailCache, signals: WorkerSignals)`
  - `class ImageLoadJob(QRunnable)(item: MediaItem, signals: WorkerSignals)` — `item.path`를 EXIF 방향 자동 적용해 `QImage`로
  - `class MetadataWriteJob(QRunnable)(item: MediaItem, signals: WorkerSignals)` — 생성 시점의 `item.rating`/`item.label`을 스냅샷해 기록
- Produces (`ui/image_cache.py`): `class ImageCache(capacity: int = 6)` with `get(key: int) -> QImage | None`, `put(key: int, image: QImage) -> None`, `__contains__`, `__len__`, `clear()`

- [ ] **Step 1: ImageCache 테스트 작성**

`tests/ui/test_image_cache.py`:
```python
from PySide6.QtGui import QImage

from ui.image_cache import ImageCache


def _img(w: int = 2) -> QImage:
    return QImage(w, 1, QImage.Format.Format_RGB32)


def test_put_get_and_capacity_evicts_least_recent():
    cache = ImageCache(capacity=2)
    cache.put(1, _img())
    cache.put(2, _img())
    assert cache.get(1) is not None      # touches 1 → 2 is now least recent
    cache.put(3, _img())
    assert 2 not in cache
    assert 1 in cache and 3 in cache
    assert len(cache) == 2


def test_get_missing_returns_none_and_clear():
    cache = ImageCache()
    assert cache.get(9) is None
    cache.put(1, _img())
    cache.clear()
    assert len(cache) == 0


def test_put_existing_key_replaces_and_refreshes():
    cache = ImageCache(capacity=2)
    cache.put(1, _img(1))
    cache.put(2, _img())
    cache.put(1, _img(5))
    cache.put(3, _img())
    assert 2 not in cache
    assert cache.get(1).width() == 5
```

- [ ] **Step 2: 실패 확인**

Run: `python -m pytest tests/ui/test_image_cache.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'ui.image_cache'`

- [ ] **Step 3: image_cache.py 구현**

`ui/image_cache.py`:
```python
from __future__ import annotations

from collections import OrderedDict

from PySide6.QtGui import QImage


class ImageCache:
    """Small LRU of decoded full-size images keyed by item index."""

    def __init__(self, capacity: int = 6):
        self.capacity = capacity
        self._data: OrderedDict[int, QImage] = OrderedDict()

    def get(self, key: int) -> QImage | None:
        img = self._data.get(key)
        if img is not None:
            self._data.move_to_end(key)
        return img

    def put(self, key: int, image: QImage) -> None:
        self._data[key] = image
        self._data.move_to_end(key)
        while len(self._data) > self.capacity:
            self._data.popitem(last=False)

    def __contains__(self, key: int) -> bool:
        return key in self._data

    def __len__(self) -> int:
        return len(self._data)

    def clear(self) -> None:
        self._data.clear()
```

- [ ] **Step 4: 통과 확인**

Run: `python -m pytest tests/ui/test_image_cache.py -v`
Expected: 3 PASS

- [ ] **Step 5: 워커 테스트 작성**

`tests/ui/test_workers.py`:
```python
from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QThreadPool

from core.metadata import MetadataError
from core.models import Label, MediaItem, MediaKind
from core.thumbnails import ThumbnailCache
from tests.helpers import make_jpeg
from ui import workers
from ui.workers import ImageLoadJob, MetadataWriteJob, ScanJob, ThumbnailJob, WorkerSignals


def _pool() -> QThreadPool:
    return QThreadPool.globalInstance()


def _item(path: Path) -> MediaItem:
    st = path.stat()
    return MediaItem(path=path, kind=MediaKind.IMAGE, mtime=st.st_mtime, size=st.st_size)


def test_scan_job_emits_populated_items(qtbot, tmp_path: Path):
    make_jpeg(tmp_path / "b.jpg")
    make_jpeg(tmp_path / "a.jpg")
    signals = WorkerSignals()
    with qtbot.waitSignal(signals.scan_finished, timeout=5000) as blocker:
        _pool().start(ScanJob(tmp_path, signals))
    items = blocker.args[0]
    assert [i.path.name for i in items] == ["a.jpg", "b.jpg"]
    assert items[0].rating == 0


def test_scan_job_failure_emits_message(qtbot, tmp_path: Path):
    signals = WorkerSignals()
    with qtbot.waitSignal(signals.scan_failed, timeout=5000) as blocker:
        _pool().start(ScanJob(tmp_path / "missing", signals))
    assert "missing" in blocker.args[0]


def test_thumbnail_job_ready_and_failed(qtbot, tmp_path: Path):
    cache = ThumbnailCache(tmp_path / "cache")
    good = _item(make_jpeg(tmp_path / "a.jpg"))
    signals = WorkerSignals()
    with qtbot.waitSignal(signals.thumbnail_ready, timeout=5000) as blocker:
        _pool().start(ThumbnailJob(good, cache, signals))
    assert blocker.args[0] is good and Path(blocker.args[1]).exists()

    bad_path = tmp_path / "bad.jpg"
    bad_path.write_bytes(b"nope")
    bad = _item(bad_path)
    with qtbot.waitSignal(signals.thumbnail_failed, timeout=5000) as blocker:
        _pool().start(ThumbnailJob(bad, cache, signals))
    assert blocker.args[0] is bad


def test_image_load_job_applies_orientation(qtbot, tmp_path: Path):
    item = _item(make_jpeg(tmp_path / "a.jpg", size=(40, 20), orientation=6))
    signals = WorkerSignals()
    with qtbot.waitSignal(signals.image_ready, timeout=5000) as blocker:
        _pool().start(ImageLoadJob(item, signals))
    got, image = blocker.args
    assert got is item and (image.width(), image.height()) == (20, 40)


def test_image_load_job_failure(qtbot, tmp_path: Path):
    p = tmp_path / "bad.jpg"
    p.write_bytes(b"nope")
    item = _item(p)
    signals = WorkerSignals()
    with qtbot.waitSignal(signals.image_failed, timeout=5000) as blocker:
        _pool().start(ImageLoadJob(item, signals))
    assert blocker.args[0] is item


def test_metadata_write_job_success_and_error(qtbot, tmp_path: Path, monkeypatch):
    item = _item(make_jpeg(tmp_path / "a.jpg"))
    item.rating, item.label = 4, Label.RED
    signals = WorkerSignals()
    with qtbot.waitSignal(signals.write_finished, timeout=5000) as blocker:
        _pool().start(MetadataWriteJob(item, signals))
    assert blocker.args[0] is item and blocker.args[1] == ""
    assert workers.metadata.read_rating_label(item.path, MediaKind.IMAGE) == (4, Label.RED)

    def boom(*a, **k):
        raise MetadataError("locked")

    monkeypatch.setattr(workers.metadata, "write_rating_label", boom)
    with qtbot.waitSignal(signals.write_finished, timeout=5000) as blocker:
        _pool().start(MetadataWriteJob(item, signals))
    assert blocker.args[0] is item and blocker.args[1] == "locked"


def test_metadata_write_job_snapshots_values_at_dispatch(qtbot, tmp_path: Path, monkeypatch):
    seen: list[tuple[int, Label]] = []

    def spy(path, kind, rating, label):
        seen.append((rating, label))

    monkeypatch.setattr(workers.metadata, "write_rating_label", spy)
    item = _item(make_jpeg(tmp_path / "a.jpg"))
    item.rating = 2
    job = MetadataWriteJob(item, WorkerSignals())
    item.rating = 5  # changed after dispatch — job must still write 2
    signals = job.signals
    with qtbot.waitSignal(signals.write_finished, timeout=5000):
        _pool().start(job)
    assert seen == [(2, Label.NONE)]
```

- [ ] **Step 6: 실패 확인**

Run: `python -m pytest tests/ui/test_workers.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'ui.workers'`

- [ ] **Step 7: workers.py 구현**

`ui/workers.py`:
```python
"""QThreadPool jobs. Each job reports through a shared WorkerSignals instance
(signals emitted from a worker thread are queued to the receiver's thread by Qt)."""
from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QObject, QRunnable, Signal
from PySide6.QtGui import QImage, QImageReader

from core import metadata, scanner
from core.models import Label, MediaItem, MediaKind
from core.thumbnails import ThumbnailCache, ThumbnailError


class WorkerSignals(QObject):
    """Result signals carry the MediaItem object (not an index) so that results arriving
    after the item list was replaced can be recognised as stale by the receiver."""

    scan_finished = Signal(object)         # list[MediaItem]
    scan_failed = Signal(str)
    thumbnail_ready = Signal(object, str)  # MediaItem, cached thumbnail path
    thumbnail_failed = Signal(object, str)
    image_ready = Signal(object, QImage)
    image_failed = Signal(object, str)
    write_finished = Signal(object, str)   # MediaItem, "" on success else error text


class ScanJob(QRunnable):
    def __init__(self, folder: Path, signals: WorkerSignals):
        super().__init__()
        self.folder, self.signals = folder, signals

    def run(self) -> None:
        try:
            items = scanner.scan(self.folder)
            for item in items:
                metadata.populate(item)
        except Exception as exc:
            self.signals.scan_failed.emit(f"{self.folder}: {exc}")
            return
        self.signals.scan_finished.emit(items)


class ThumbnailJob(QRunnable):
    def __init__(self, item: MediaItem, cache: ThumbnailCache, signals: WorkerSignals):
        super().__init__()
        self.item, self.cache, self.signals = item, cache, signals

    def run(self) -> None:
        try:
            path = self.cache.get_or_create(self.item)
        except ThumbnailError as exc:
            self.signals.thumbnail_failed.emit(self.item, str(exc))
            return
        self.signals.thumbnail_ready.emit(self.item, str(path))


class ImageLoadJob(QRunnable):
    def __init__(self, item: MediaItem, signals: WorkerSignals):
        super().__init__()
        self.item, self.signals = item, signals

    def run(self) -> None:
        reader = QImageReader(str(self.item.path))
        reader.setAutoTransform(True)  # honour EXIF orientation
        image = reader.read()
        if image.isNull():
            self.signals.image_failed.emit(self.item, reader.errorString() or "decode failed")
            return
        self.signals.image_ready.emit(self.item, image)


class MetadataWriteJob(QRunnable):
    """Writes the rating/label the item had when the job was created."""

    def __init__(self, item: MediaItem, signals: WorkerSignals):
        super().__init__()
        self.item, self.signals = item, signals
        self.rating: int = item.rating
        self.label: Label = item.label

    def run(self) -> None:
        try:
            metadata.write_rating_label(self.item.path, self.item.kind, self.rating, self.label)
        except metadata.MetadataError as exc:
            self.signals.write_finished.emit(self.item, str(exc))
            return
        self.signals.write_finished.emit(self.item, "")
```

- [ ] **Step 8: 통과 확인**

Run: `python -m pytest tests/ui -v`
Expected: 전부 PASS

- [ ] **Step 9: Commit**

```bash
git add ui/workers.py ui/image_cache.py tests/ui/test_workers.py tests/ui/test_image_cache.py
git commit -m "feat(ui): 스캔/썸네일/이미지 로드/메타 쓰기 워커와 QImage LRU

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 7: MediaListModel

**Files:**
- Create: `ui/media_list_model.py`
- Test: `tests/ui/test_media_list_model.py`

**Interfaces:**
- Consumes: `core.models.MediaItem`, `core.filters.{Filter, NO_FILTER}`
- Produces (`ui/media_list_model.py`): `class MediaListModel(QAbstractListModel)`
  - 역할 상수: `ItemRole` (MediaItem), `IndexRole` (원본 item index, int), `FailedRole` (썸네일 실패 bool); `Qt.DisplayRole` → 파일명, `Qt.DecorationRole` → `QPixmap | None`
  - `set_items(items: list[MediaItem])` — 리셋, 썸네일·실패 정보 초기화
  - `items() -> list[MediaItem]`, `set_filter(f: Filter)`, `filter() -> Filter`, `refresh_filter()` (아이템 별점 변경 후 가시 목록 재계산), `visible_indices() -> list[int]`
  - `row_for_item_index(idx: int) -> int` (-1이면 필터에 가려짐), `item_index_at_row(row: int) -> int`, `item_at_row(row: int) -> MediaItem`
  - `set_thumbnail(idx: int, pixmap: QPixmap)`, `set_thumbnail_failed(idx: int)`, `thumbnail(idx: int) -> QPixmap | None`, `has_thumbnail_request(idx) -> bool`/`mark_requested(idx)` (중복 잡 방지)
  - `item_changed(idx: int)` — 해당 행 `dataChanged`

- [ ] **Step 1: 테스트 작성**

`tests/ui/test_media_list_model.py`:
```python
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtGui import QPixmap

from core.filters import Filter
from core.models import MediaItem, MediaKind
from ui.media_list_model import MediaListModel


def _items(*ratings: int) -> list[MediaItem]:
    return [MediaItem(path=Path(f"IMG_{i}.jpg"), kind=MediaKind.IMAGE, mtime=0, size=1, rating=r)
            for i, r in enumerate(ratings)]


def test_rows_and_roles():
    m = MediaListModel()
    m.set_items(_items(0, 3))
    assert m.rowCount() == 2
    idx = m.index(1)
    assert m.data(idx, Qt.ItemDataRole.DisplayRole) == "IMG_1.jpg"
    assert m.data(idx, MediaListModel.ItemRole).rating == 3
    assert m.data(idx, MediaListModel.IndexRole) == 1
    assert m.data(idx, Qt.ItemDataRole.DecorationRole) is None
    assert m.data(idx, MediaListModel.FailedRole) is False


def test_filter_maps_rows_to_item_indices():
    m = MediaListModel()
    m.set_items(_items(0, 3, 5))
    m.set_filter(Filter(min_rating=3))
    assert m.rowCount() == 2
    assert m.visible_indices() == [1, 2]
    assert m.item_index_at_row(0) == 1
    assert m.item_at_row(1).rating == 5
    assert m.row_for_item_index(0) == -1
    assert m.row_for_item_index(2) == 1


def test_refresh_filter_after_rating_change():
    m = MediaListModel()
    m.set_items(_items(0, 3))
    m.set_filter(Filter(min_rating=3))
    m.items()[0].rating = 4
    m.refresh_filter()
    assert m.visible_indices() == [0, 1]


def test_thumbnail_storage_survives_filter_change_and_emits_datachanged(qtbot):
    m = MediaListModel()
    m.set_items(_items(0, 3))
    pm = QPixmap(4, 4)
    with qtbot.waitSignal(m.dataChanged, timeout=1000) as blocker:
        m.set_thumbnail(1, pm)
    assert blocker.args[0].row() == 1
    m.set_filter(Filter(min_rating=3))
    assert m.thumbnail(1) is pm
    assert m.data(m.index(0), Qt.ItemDataRole.DecorationRole) is pm


def test_thumbnail_failed_and_request_tracking():
    m = MediaListModel()
    m.set_items(_items(0))
    assert m.has_thumbnail_request(0) is False
    m.mark_requested(0)
    assert m.has_thumbnail_request(0) is True
    m.set_thumbnail_failed(0)
    assert m.data(m.index(0), MediaListModel.FailedRole) is True
    m.set_items(_items(1))
    assert m.has_thumbnail_request(0) is False  # reset clears tracking


def test_set_thumbnail_for_hidden_item_does_not_emit(qtbot):
    m = MediaListModel()
    m.set_items(_items(0, 3))
    m.set_filter(Filter(min_rating=3))
    with qtbot.assertNotEmitted(m.dataChanged):
        m.set_thumbnail(0, QPixmap(2, 2))
    assert m.thumbnail(0) is not None
```

- [ ] **Step 2: 실패 확인**

Run: `python -m pytest tests/ui/test_media_list_model.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'ui.media_list_model'`

- [ ] **Step 3: media_list_model.py 구현**

`ui/media_list_model.py`:
```python
from __future__ import annotations

from PySide6.QtCore import QAbstractListModel, QModelIndex, Qt
from PySide6.QtGui import QPixmap

from core.filters import NO_FILTER, Filter
from core.models import MediaItem


class MediaListModel(QAbstractListModel):
    ItemRole = Qt.ItemDataRole.UserRole + 1
    IndexRole = Qt.ItemDataRole.UserRole + 2
    FailedRole = Qt.ItemDataRole.UserRole + 3

    def __init__(self, parent=None):
        super().__init__(parent)
        self._items: list[MediaItem] = []
        self._filter: Filter = NO_FILTER
        self._visible: list[int] = []
        self._thumbs: dict[int, QPixmap] = {}
        self._failed: set[int] = set()
        self._requested: set[int] = set()

    # ---- items / filter ----
    def set_items(self, items: list[MediaItem]) -> None:
        self.beginResetModel()
        self._items = list(items)
        self._thumbs.clear()
        self._failed.clear()
        self._requested.clear()
        self._visible = self._filter.apply(self._items)
        self.endResetModel()

    def items(self) -> list[MediaItem]:
        return self._items

    def set_filter(self, f: Filter) -> None:
        self._filter = f
        self.refresh_filter()

    def filter(self) -> Filter:
        return self._filter

    def refresh_filter(self) -> None:
        self.beginResetModel()
        self._visible = self._filter.apply(self._items)
        self.endResetModel()

    def visible_indices(self) -> list[int]:
        return list(self._visible)

    def row_for_item_index(self, idx: int) -> int:
        try:
            return self._visible.index(idx)
        except ValueError:
            return -1

    def item_index_at_row(self, row: int) -> int:
        return self._visible[row]

    def item_at_row(self, row: int) -> MediaItem:
        return self._items[self._visible[row]]

    # ---- thumbnails ----
    def set_thumbnail(self, idx: int, pixmap: QPixmap) -> None:
        self._thumbs[idx] = pixmap
        self._failed.discard(idx)
        self.item_changed(idx)

    def set_thumbnail_failed(self, idx: int) -> None:
        self._failed.add(idx)
        self.item_changed(idx)

    def thumbnail(self, idx: int) -> QPixmap | None:
        return self._thumbs.get(idx)

    def has_thumbnail_request(self, idx: int) -> bool:
        return idx in self._requested

    def mark_requested(self, idx: int) -> None:
        self._requested.add(idx)

    def item_changed(self, idx: int) -> None:
        row = self.row_for_item_index(idx)
        if row >= 0:
            mi = self.index(row)
            self.dataChanged.emit(mi, mi)

    # ---- Qt model API ----
    def rowCount(self, parent: QModelIndex = QModelIndex()) -> int:  # noqa: N802
        return 0 if parent.isValid() else len(self._visible)

    def data(self, index: QModelIndex, role: int = Qt.ItemDataRole.DisplayRole):
        if not index.isValid() or not 0 <= index.row() < len(self._visible):
            return None
        idx = self._visible[index.row()]
        item = self._items[idx]
        if role == Qt.ItemDataRole.DisplayRole:
            return item.path.name
        if role == Qt.ItemDataRole.DecorationRole:
            return self._thumbs.get(idx)
        if role == self.ItemRole:
            return item
        if role == self.IndexRole:
            return idx
        if role == self.FailedRole:
            return idx in self._failed
        return None
```

- [ ] **Step 4: 통과 확인**

Run: `python -m pytest tests/ui/test_media_list_model.py -v`
Expected: 6 PASS

- [ ] **Step 5: Commit**

```bash
git add ui/media_list_model.py tests/ui/test_media_list_model.py
git commit -m "feat(ui): MediaListModel — 필터·썸네일 픽스맵 보관

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 8: 썸네일 델리게이트와 Filmstrip/GridView

**Files:**
- Create: `ui/thumb_delegate.py`, `ui/thumb_views.py`
- Test: `tests/ui/test_thumb_delegate.py`, `tests/ui/test_thumb_views.py`

**Interfaces:**
- Consumes: `ui.media_list_model.MediaListModel` (역할 상수), `core.models.{MediaItem, MediaKind, Label}`
- Produces (`ui/thumb_delegate.py`):
  - `LABEL_COLORS: dict[Label, QColor]`
  - `class ThumbDelegate(QStyledItemDelegate)(cell: QSize, parent=None)` — `sizeHint()`는 항상 `cell`
- Produces (`ui/thumb_views.py`):
  - `class ThumbListView(QListView)` — `row_activated = Signal(int)`, `row_double_clicked = Signal(int)`, `set_current_row(row: int)`, `current_row() -> int`, `visible_rows() -> list[int]` (뷰포트에 보이는 행)
  - `class Filmstrip(ThumbListView)` — 가로 1줄, 셀 `QSize(120, 110)`, 고정 높이
  - `class GridView(ThumbListView)` — 줄바꿈 그리드, 셀 `QSize(220, 210)`

- [ ] **Step 1: 델리게이트 테스트 작성**

`tests/ui/test_thumb_delegate.py`:
```python
from pathlib import Path

from PySide6.QtCore import QRect, QSize, Qt
from PySide6.QtGui import QColor, QImage, QPainter, QPixmap
from PySide6.QtWidgets import QStyleOptionViewItem

from core.models import Label, MediaItem, MediaKind
from ui.media_list_model import MediaListModel
from ui.thumb_delegate import LABEL_COLORS, ThumbDelegate

CELL = QSize(120, 110)


def _render(model: MediaListModel, row: int) -> QImage:
    delegate = ThumbDelegate(CELL)
    image = QImage(CELL, QImage.Format.Format_ARGB32)
    image.fill(Qt.GlobalColor.black)
    option = QStyleOptionViewItem()
    option.rect = QRect(0, 0, CELL.width(), CELL.height())
    painter = QPainter(image)
    delegate.paint(painter, option, model.index(row))
    painter.end()
    return image


def _model(item: MediaItem) -> MediaListModel:
    m = MediaListModel()
    m.set_items([item])
    return m


def test_size_hint_is_cell():
    d = ThumbDelegate(CELL)
    m = _model(MediaItem(Path("a.jpg"), MediaKind.IMAGE, 0, 1))
    assert d.sizeHint(QStyleOptionViewItem(), m.index(0)) == CELL


def test_paints_label_bar_in_label_color():
    m = _model(MediaItem(Path("a.jpg"), MediaKind.IMAGE, 0, 1, rating=2, label=Label.RED))
    img = _render(m, 0)
    assert QColor(img.pixel(CELL.width() // 2, 1)) == LABEL_COLORS[Label.RED]


def test_paints_without_thumbnail_with_thumbnail_and_failed():
    item = MediaItem(Path("a.jpg"), MediaKind.VIDEO, 0, 1, rating=-1, write_error="locked")
    m = _model(item)
    _render(m, 0)                       # placeholder path must not raise
    pm = QPixmap(300, 100)
    pm.fill(Qt.GlobalColor.white)
    m.set_thumbnail(0, pm)
    img = _render(m, 0)
    # pixmap scaled to fit width keeps aspect: white band appears mid-cell
    assert QColor(img.pixel(CELL.width() // 2, 40)).lightness() > 200
    m.set_thumbnail_failed(0)
    _render(m, 0)                       # failed placeholder must not raise
```

- [ ] **Step 2: 실패 확인**

Run: `python -m pytest tests/ui/test_thumb_delegate.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'ui.thumb_delegate'`

- [ ] **Step 3: thumb_delegate.py 구현**

`ui/thumb_delegate.py`:
```python
from __future__ import annotations

from PySide6.QtCore import QModelIndex, QRect, QSize, Qt
from PySide6.QtGui import QColor, QFont, QPainter, QPen, QPixmap
from PySide6.QtWidgets import QStyle, QStyledItemDelegate, QStyleOptionViewItem

from core.models import Label, MediaItem, MediaKind
from ui.media_list_model import MediaListModel

LABEL_COLORS: dict[Label, QColor] = {
    Label.RED: QColor("#e5484d"),
    Label.YELLOW: QColor("#f5d90a"),
    Label.GREEN: QColor("#46a758"),
    Label.BLUE: QColor("#0090ff"),
}
_BG = QColor("#202020")
_BG_SELECTED = QColor("#3a5f8f")
_PLACEHOLDER = QColor("#3c3c3c")
_TEXT = QColor("#e0e0e0")
_STAR = QColor("#ffcc33")
_REJECT = QColor("#8a8a8a")
_ERROR = QColor("#ff4040")
_FOOTER_H = 18
_BAR_H = 4
_PAD = 4


class ThumbDelegate(QStyledItemDelegate):
    def __init__(self, cell: QSize, parent=None):
        super().__init__(parent)
        self._cell = QSize(cell)

    def sizeHint(self, option: QStyleOptionViewItem, index: QModelIndex) -> QSize:  # noqa: N802
        return QSize(self._cell)

    def paint(self, painter: QPainter, option: QStyleOptionViewItem, index: QModelIndex) -> None:
        item: MediaItem = index.data(MediaListModel.ItemRole)
        pixmap: QPixmap | None = index.data(Qt.ItemDataRole.DecorationRole)
        failed: bool = bool(index.data(MediaListModel.FailedRole))
        rect: QRect = option.rect

        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        selected = bool(option.state & QStyle.StateFlag.State_Selected)
        painter.fillRect(rect, _BG_SELECTED if selected else _BG)

        # label bar across the top
        if item.label in LABEL_COLORS:
            painter.fillRect(QRect(rect.left(), rect.top(), rect.width(), _BAR_H), LABEL_COLORS[item.label])

        image_area = QRect(rect.left() + _PAD, rect.top() + _BAR_H + _PAD,
                           rect.width() - 2 * _PAD, rect.height() - _BAR_H - _FOOTER_H - 2 * _PAD)
        if pixmap is not None and not pixmap.isNull():
            scaled = pixmap.scaled(image_area.size(), Qt.AspectRatioMode.KeepAspectRatio,
                                   Qt.TransformationMode.SmoothTransformation)
            target = QRect(0, 0, scaled.width(), scaled.height())
            target.moveCenter(image_area.center())
            painter.drawPixmap(target, scaled)
        else:
            painter.fillRect(image_area, _PLACEHOLDER)
            painter.setPen(_TEXT)
            painter.drawText(image_area, Qt.AlignmentFlag.AlignCenter, "✗" if failed else "…")

        # overlays
        small = QFont(option.font)
        small.setPointSizeF(max(7.0, option.font.pointSizeF() - 1))
        painter.setFont(small)
        stars_rect = QRect(image_area.left() + 2, image_area.top() + 2, image_area.width() - 4, 14)
        if item.is_rejected:
            painter.setPen(_REJECT)
            painter.drawText(stars_rect, Qt.AlignmentFlag.AlignLeft, "✕ reject")
        elif item.rating > 0:
            painter.setPen(_STAR)
            painter.drawText(stars_rect, Qt.AlignmentFlag.AlignLeft, "★" * item.rating)
        if item.kind is MediaKind.VIDEO:
            painter.setPen(_TEXT)
            painter.drawText(stars_rect, Qt.AlignmentFlag.AlignRight, "▶")
        if item.write_error:
            painter.setPen(QPen(_ERROR))
            bold = QFont(small)
            bold.setBold(True)
            painter.setFont(bold)
            painter.drawText(QRect(image_area.right() - 14, image_area.bottom() - 14, 14, 14),
                             Qt.AlignmentFlag.AlignCenter, "!")
            painter.setFont(small)

        # footer: file name
        painter.setPen(_TEXT)
        footer = QRect(rect.left() + _PAD, rect.bottom() - _FOOTER_H, rect.width() - 2 * _PAD, _FOOTER_H)
        name = option.fontMetrics.elidedText(item.path.name, Qt.TextElideMode.ElideMiddle, footer.width())
        painter.drawText(footer, Qt.AlignmentFlag.AlignCenter, name)
        painter.restore()
```

- [ ] **Step 4: 통과 확인**

Run: `python -m pytest tests/ui/test_thumb_delegate.py -v`
Expected: 3 PASS

- [ ] **Step 5: 뷰 테스트 작성**

`tests/ui/test_thumb_views.py`:
```python
from pathlib import Path

from PySide6.QtCore import Qt

from core.models import MediaItem, MediaKind
from ui.media_list_model import MediaListModel
from ui.thumb_views import Filmstrip, GridView


def _model(n: int) -> MediaListModel:
    m = MediaListModel()
    m.set_items([MediaItem(Path(f"{i}.jpg"), MediaKind.IMAGE, 0, 1) for i in range(n)])
    return m


def test_filmstrip_is_single_row_and_tracks_current(qtbot):
    m = _model(5)
    view = Filmstrip()
    view.setModel(m)
    qtbot.addWidget(view)
    view.resize(600, 130)
    view.show()
    assert view.isWrapping() is False
    assert view.focusPolicy() == Qt.FocusPolicy.NoFocus
    view.set_current_row(3)
    assert view.current_row() == 3
    assert view.current_row() == view.currentIndex().row()


def test_click_emits_row_activated_and_double_click(qtbot):
    m = _model(4)
    view = GridView()
    view.setModel(m)
    qtbot.addWidget(view)
    view.resize(500, 500)
    view.show()
    qtbot.waitExposed(view)
    assert view.isWrapping() is True
    target = view.visualRect(m.index(2)).center()
    with qtbot.waitSignal(view.row_activated, timeout=1000) as blocker:
        qtbot.mouseClick(view.viewport(), Qt.MouseButton.LeftButton, pos=target)
    assert blocker.args == [2]
    with qtbot.waitSignal(view.row_double_clicked, timeout=1000) as blocker:
        qtbot.mouseDClick(view.viewport(), Qt.MouseButton.LeftButton, pos=target)
    assert blocker.args == [2]


def test_visible_rows_subset_after_scroll(qtbot):
    m = _model(60)
    view = Filmstrip()
    view.setModel(m)
    qtbot.addWidget(view)
    view.resize(400, 130)
    view.show()
    qtbot.waitExposed(view)
    rows = view.visible_rows()
    assert rows and rows[0] == 0 and len(rows) < 60
    view.set_current_row(59)
    assert 59 in view.visible_rows()


def test_set_current_row_out_of_range_is_ignored(qtbot):
    m = _model(2)
    view = Filmstrip()
    view.setModel(m)
    qtbot.addWidget(view)
    view.set_current_row(5)
    assert view.current_row() == -1
```

- [ ] **Step 6: 실패 확인**

Run: `python -m pytest tests/ui/test_thumb_views.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'ui.thumb_views'`

- [ ] **Step 7: thumb_views.py 구현**

`ui/thumb_views.py`:
```python
from __future__ import annotations

from PySide6.QtCore import QSize, Qt, Signal
from PySide6.QtWidgets import QAbstractItemView, QListView

from ui.thumb_delegate import ThumbDelegate

FILMSTRIP_CELL = QSize(120, 110)
GRID_CELL = QSize(220, 210)


class ThumbListView(QListView):
    """Shared behaviour: icon-mode list of MediaListModel rows drawn by ThumbDelegate.

    The view never takes keyboard focus — MainWindow owns all shortcuts.
    """

    row_activated = Signal(int)
    row_double_clicked = Signal(int)

    def __init__(self, cell: QSize, wrapping: bool, parent=None):
        super().__init__(parent)
        self._cell = QSize(cell)
        self.setViewMode(QListView.ViewMode.IconMode)
        self.setFlow(QListView.Flow.LeftToRight)
        self.setWrapping(wrapping)
        self.setResizeMode(QListView.ResizeMode.Adjust)
        self.setMovement(QListView.Movement.Static)
        self.setUniformItemSizes(True)
        self.setGridSize(self._cell)
        self.setSpacing(2)
        self.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.setItemDelegate(ThumbDelegate(self._cell, self))
        self.clicked.connect(lambda mi: self.row_activated.emit(mi.row()))
        self.doubleClicked.connect(lambda mi: self.row_double_clicked.emit(mi.row()))

    def set_current_row(self, row: int) -> None:
        model = self.model()
        if model is None or not 0 <= row < model.rowCount():
            return
        mi = model.index(row)
        self.setCurrentIndex(mi)
        self.scrollTo(mi, QAbstractItemView.ScrollHint.EnsureVisible)

    def current_row(self) -> int:
        return self.currentIndex().row()

    def visible_rows(self) -> list[int]:
        """Rows whose cell intersects the viewport. Linear scan is fine for a few hundred items."""
        model = self.model()
        if model is None:
            return []
        vp = self.viewport().rect()
        return [row for row in range(model.rowCount()) if self.visualRect(model.index(row)).intersects(vp)]


class Filmstrip(ThumbListView):
    def __init__(self, parent=None):
        super().__init__(FILMSTRIP_CELL, wrapping=False, parent=parent)
        self.setFixedHeight(FILMSTRIP_CELL.height() + 2 * 2 + 16)  # cell + spacing + scrollbar
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)


class GridView(ThumbListView):
    def __init__(self, parent=None):
        super().__init__(GRID_CELL, wrapping=True, parent=parent)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
```

- [ ] **Step 8: 통과 확인**

Run: `python -m pytest tests/ui/test_thumb_views.py -v`
Expected: 4 PASS. `visible_rows`가 빈 리스트를 돌려주면 `qtbot.waitExposed` 뒤 `qtbot.wait(50)`을 테스트에 추가해 레이아웃이 끝나길 기다린다.

- [ ] **Step 9: Commit**

```bash
git add ui/thumb_delegate.py ui/thumb_views.py tests/ui/test_thumb_delegate.py tests/ui/test_thumb_views.py
git commit -m "feat(ui): 썸네일 델리게이트, Filmstrip/GridView

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 9: LoupeView — fit/100% 토글과 패닝

**Files:**
- Create: `ui/loupe_view.py`
- Test: `tests/ui/test_loupe_view.py`

**Interfaces:**
- Produces (`ui/loupe_view.py`): `class LoupeView(QGraphicsView)`
  - `set_image(image: QImage) -> None`, `set_placeholder(text: str) -> None`, `has_image -> bool`
  - `is_fit -> bool`, `fit() -> None`, `zoom_100(anchor: QPoint | None = None) -> None`, `toggle_zoom(anchor: QPoint | None = None) -> None`, `current_scale() -> float`
  - 좌클릭(드래그 아님)으로 토글; 100% 상태에서는 `ScrollHandDrag`로 패닝; 리사이즈 시 fit 유지

- [ ] **Step 1: 테스트 작성**

`tests/ui/test_loupe_view.py`:
```python
import pytest
from PySide6.QtCore import QPoint, Qt
from PySide6.QtGui import QImage
from PySide6.QtWidgets import QGraphicsView

from ui.loupe_view import LoupeView


def _img(w: int, h: int) -> QImage:
    im = QImage(w, h, QImage.Format.Format_RGB32)
    im.fill(Qt.GlobalColor.gray)
    return im


@pytest.fixture
def view(qtbot):
    v = LoupeView()
    qtbot.addWidget(v)
    v.resize(400, 300)
    v.show()
    qtbot.waitExposed(v)
    return v


def test_fit_scales_down_to_viewport(view):
    view.set_image(_img(2000, 1000))
    assert view.has_image and view.is_fit
    assert view.current_scale() == pytest.approx(400 / 2000, rel=0.05)


def test_fit_never_upscales(view):
    view.set_image(_img(100, 50))
    assert view.current_scale() == pytest.approx(1.0)


def test_toggle_zoom_switches_between_fit_and_100(view):
    view.set_image(_img(2000, 1000))
    view.toggle_zoom(QPoint(10, 10))
    assert not view.is_fit
    assert view.current_scale() == pytest.approx(1.0)
    assert view.dragMode() == QGraphicsView.DragMode.ScrollHandDrag
    view.toggle_zoom()
    assert view.is_fit
    assert view.dragMode() == QGraphicsView.DragMode.NoDrag


def test_click_toggles_but_drag_does_not(view, qtbot):
    view.set_image(_img(2000, 1000))
    qtbot.mouseClick(view.viewport(), Qt.MouseButton.LeftButton, pos=QPoint(200, 150))
    assert not view.is_fit
    qtbot.mousePress(view.viewport(), Qt.MouseButton.LeftButton, pos=QPoint(200, 150))
    qtbot.mouseMove(view.viewport(), QPoint(260, 190))
    qtbot.mouseRelease(view.viewport(), Qt.MouseButton.LeftButton, pos=QPoint(260, 190))
    assert not view.is_fit  # a drag pans; it must not toggle back


def test_resize_keeps_fit(view):
    view.set_image(_img(2000, 1000))
    view.resize(800, 600)
    assert view.current_scale() == pytest.approx(800 / 2000, rel=0.05)


def test_placeholder_disables_zoom(view):
    view.set_placeholder("깨진 파일")
    assert not view.has_image
    view.toggle_zoom()
    assert view.is_fit


def test_new_image_resets_to_fit(view):
    view.set_image(_img(2000, 1000))
    view.toggle_zoom()
    view.set_image(_img(3000, 1500))
    assert view.is_fit
```

- [ ] **Step 2: 실패 확인**

Run: `python -m pytest tests/ui/test_loupe_view.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'ui.loupe_view'`

- [ ] **Step 3: loupe_view.py 구현**

`ui/loupe_view.py`:
```python
from __future__ import annotations

from PySide6.QtCore import QPoint, QPointF, Qt
from PySide6.QtGui import QColor, QImage, QMouseEvent, QPainter, QPixmap, QResizeEvent, QTransform
from PySide6.QtWidgets import QFrame, QGraphicsPixmapItem, QGraphicsScene, QGraphicsSimpleTextItem, QGraphicsView

_CLICK_TOLERANCE_PX = 4


class LoupeView(QGraphicsView):
    """Single-image view. Two states only: fit-to-window (no upscaling) and 100%."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._scene = QGraphicsScene(self)
        self.setScene(self._scene)
        self._pix_item = QGraphicsPixmapItem()
        self._pix_item.setTransformationMode(Qt.TransformationMode.SmoothTransformation)
        self._text_item = QGraphicsSimpleTextItem()
        self._text_item.setBrush(QColor("#c0c0c0"))
        self._scene.addItem(self._pix_item)
        self._scene.addItem(self._text_item)
        self._fit = True
        self._has_image = False
        self._press_pos: QPoint | None = None

        self.setBackgroundBrush(QColor("#141414"))
        self.setFrameShape(QFrame.Shape.NoFrame)
        self.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setTransformationAnchor(QGraphicsView.ViewportAnchor.AnchorUnderMouse)
        self.setResizeAnchor(QGraphicsView.ViewportAnchor.AnchorViewCenter)
        self.setDragMode(QGraphicsView.DragMode.NoDrag)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)

    # ---- content ----
    @property
    def has_image(self) -> bool:
        return self._has_image

    @property
    def is_fit(self) -> bool:
        return self._fit

    def set_image(self, image: QImage) -> None:
        self._text_item.setVisible(False)
        self._pix_item.setPixmap(QPixmap.fromImage(image))
        self._pix_item.setVisible(True)
        self._scene.setSceneRect(self._pix_item.boundingRect())
        self._has_image = True
        self.fit()

    def set_placeholder(self, text: str) -> None:
        self._pix_item.setVisible(False)
        self._pix_item.setPixmap(QPixmap())
        self._has_image = False
        self._text_item.setText(text)
        self._text_item.setVisible(True)
        self._scene.setSceneRect(self._text_item.boundingRect())
        self.resetTransform()
        self.centerOn(self._text_item)
        self._fit = True
        self.setDragMode(QGraphicsView.DragMode.NoDrag)

    # ---- zoom ----
    def current_scale(self) -> float:
        return self.transform().m11()

    def fit(self) -> None:
        self._fit = True
        self.setDragMode(QGraphicsView.DragMode.NoDrag)
        if not self._has_image:
            self.resetTransform()
            return
        br = self._pix_item.boundingRect()
        vp = self.viewport().rect()
        if br.width() <= 0 or br.height() <= 0:
            return
        scale = min(vp.width() / br.width(), vp.height() / br.height(), 1.0)
        self.setTransform(QTransform.fromScale(scale, scale))
        self.centerOn(self._pix_item)

    def zoom_100(self, anchor: QPoint | None = None) -> None:
        if not self._has_image:
            return
        target: QPointF = self.mapToScene(anchor) if anchor is not None else self._pix_item.boundingRect().center()
        self._fit = False
        self.setTransform(QTransform())
        self.centerOn(target)
        self.setDragMode(QGraphicsView.DragMode.ScrollHandDrag)

    def toggle_zoom(self, anchor: QPoint | None = None) -> None:
        if not self._has_image:
            return
        if self._fit:
            self.zoom_100(anchor)
        else:
            self.fit()

    # ---- events ----
    def resizeEvent(self, event: QResizeEvent) -> None:  # noqa: N802
        super().resizeEvent(event)
        if self._fit:
            self.fit()

    def mousePressEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        if event.button() == Qt.MouseButton.LeftButton:
            self._press_pos = event.position().toPoint()
        super().mousePressEvent(event)

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        super().mouseReleaseEvent(event)
        if event.button() == Qt.MouseButton.LeftButton and self._press_pos is not None:
            moved = (event.position().toPoint() - self._press_pos).manhattanLength()
            if moved <= _CLICK_TOLERANCE_PX:
                self.toggle_zoom(event.position().toPoint())
        self._press_pos = None
```

- [ ] **Step 4: 통과 확인**

Run: `python -m pytest tests/ui/test_loupe_view.py -v`
Expected: 7 PASS. `test_click_toggles_but_drag_does_not`에서 offscreen 플랫폼이 `mouseMove`를 드래그로 인식하지 못해 토글이 되면, 테스트의 `mouseMove` 전후에 `qtbot.wait(20)`을 넣는다(이동 이벤트가 전달되도록).

- [ ] **Step 5: Commit**

```bash
git add ui/loupe_view.py tests/ui/test_loupe_view.py
git commit -m "feat(ui): LoupeView — fit/100% 토글, 클릭·드래그 구분, 패닝

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---
### Task 10: VideoView

**Files:**
- Create: `ui/video_view.py`
- Test: `tests/ui/test_video_view.py`

**Interfaces:**
- Produces (`ui/video_view.py`): `class VideoView(QWidget)` — `error = Signal(str)`, `load(path: Path) -> None`, `toggle_play() -> None`, `stop() -> None`, `is_playing() -> bool`, `source_path() -> Path | None`, 속성 `player: QMediaPlayer`

- [ ] **Step 1: 테스트 작성**

`tests/ui/test_video_view.py`:
```python
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
```

- [ ] **Step 2: 실패 확인**

Run: `python -m pytest tests/ui/test_video_view.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'ui.video_view'`

- [ ] **Step 3: video_view.py 구현**

`ui/video_view.py`:
```python
from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QUrl, Signal
from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer
from PySide6.QtMultimediaWidgets import QVideoWidget
from PySide6.QtWidgets import QVBoxLayout, QWidget


class VideoView(QWidget):
    error = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._source: Path | None = None
        self.player = QMediaPlayer(self)
        self._audio = QAudioOutput(self)
        self.player.setAudioOutput(self._audio)
        self._widget = QVideoWidget(self)
        self.player.setVideoOutput(self._widget)
        self.player.errorOccurred.connect(self._on_error)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self._widget)
        self.setStyleSheet("background:#141414;")

    def load(self, path: Path) -> None:
        self.stop()
        self._source = path
        self.player.setSource(QUrl.fromLocalFile(str(path)))

    def toggle_play(self) -> None:
        if self.is_playing():
            self.player.pause()
        else:
            self.player.play()

    def stop(self) -> None:
        self.player.stop()

    def is_playing(self) -> bool:
        return self.player.playbackState() == QMediaPlayer.PlaybackState.PlayingState

    def source_path(self) -> Path | None:
        return self._source

    def _on_error(self, _err, message: str) -> None:
        self.error.emit(message or "재생할 수 없는 영상입니다")
```

- [ ] **Step 4: 통과 확인**

Run: `python -m pytest tests/ui/test_video_view.py -v`
Expected: 2 PASS (미디어 백엔드 경고가 stderr에 찍힐 수 있으나 무시)

- [ ] **Step 5: Commit**

```bash
git add ui/video_view.py tests/ui/test_video_view.py
git commit -m "feat(ui): VideoView — QMediaPlayer 래퍼

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 11: MainWindow — 탐색, 별점/라벨 입력, 백그라운드 기록, 이미지 표시

**Files:**
- Create: `ui/main_window.py`
- Test: `tests/ui/test_main_window_core.py`

**Interfaces:**
- Consumes: 모든 `ui/*` 컴포넌트, `core.thumbnails.{ThumbnailCache, default_cache_dir}`, `core.models`, `core.filters`
- Produces (`ui/main_window.py`): `class MainWindow(QMainWindow)(thumb_cache: ThumbnailCache | None = None, settings: QSettings | None = None)`
  - 상태: `model: MediaListModel`, `current: int` (item index, 없으면 -1), `folder: Path | None`, `image_cache: ImageCache`, `signals: WorkerSignals`, `suppress_dialogs: bool`
  - 위젯: `header: QLabel`, `loupe: LoupeView`, `video: VideoView`, `content_stack: QStackedWidget`, `filmstrip: Filmstrip`, `grid: GridView`, `mode_stack: QStackedWidget`
  - 메서드: `load_items(items, folder)`, `open_folder(folder)`, `next_item()`, `prev_item()`, `first_item()`, `last_item()`, `set_rating(n)`, `toggle_reject()`, `set_label(label)`, `current_item() -> MediaItem | None`
  - 키(Task 11 범위): `→ Space` 다음(영상 표시 중 `Space`는 재생/일시정지), `← Backspace` 이전, `Home End`, `1~5 0 X`, `6~9`, `Z`

- [ ] **Step 1: 테스트 작성**

`tests/ui/test_main_window_core.py`:
```python
from __future__ import annotations

from pathlib import Path

import pytest
from PySide6.QtCore import QSettings, Qt

from core import metadata
from core.models import Label, MediaKind
from core.scanner import scan
from core.thumbnails import ThumbnailCache
from tests.helpers import make_jpeg
from ui import workers
from ui.main_window import MainWindow


@pytest.fixture
def folder(tmp_path: Path) -> Path:
    for i in range(1, 6):
        make_jpeg(tmp_path / f"IMG_{i}.jpg", size=(200 + i, 100))
    (tmp_path / "clip.mp4").write_bytes(b"\x00" * 8)
    return tmp_path


def _items(folder: Path):
    items = scan(folder)
    for it in items:
        metadata.populate(it)
    return items


@pytest.fixture
def win(qtbot, tmp_path: Path):
    settings = QSettings(str(tmp_path / "settings.ini"), QSettings.Format.IniFormat)
    w = MainWindow(thumb_cache=ThumbnailCache(tmp_path / "cache"), settings=settings)
    w.suppress_dialogs = True
    qtbot.addWidget(w)
    w.resize(1000, 700)
    w.show()
    qtbot.waitExposed(w)
    return w


def test_empty_state(win):
    assert win.current == -1
    assert win.current_item() is None
    assert "Ctrl+O" in win.header.text()
    win.next_item()
    win.set_rating(3)  # must not raise with no items


def test_load_items_selects_first_and_updates_header(win, folder, qtbot):
    win.load_items(_items(folder), folder)
    assert win.current == 0
    assert win.current_item().path.name == "clip.mp4"
    assert "1/6" in win.header.text()
    assert str(folder) in win.header.text()
    assert win.filmstrip.current_row() == 0


def test_navigation_keys_and_bounds(win, folder, qtbot):
    win.load_items(_items(folder), folder)
    qtbot.keyClick(win, Qt.Key.Key_Right)
    assert win.current == 1
    qtbot.keyClick(win, Qt.Key.Key_Space)
    assert win.current == 2
    qtbot.keyClick(win, Qt.Key.Key_Left)
    qtbot.keyClick(win, Qt.Key.Key_Backspace)
    assert win.current == 0
    qtbot.keyClick(win, Qt.Key.Key_Left)
    assert win.current == 0
    qtbot.keyClick(win, Qt.Key.Key_End)
    assert win.current == 5
    qtbot.keyClick(win, Qt.Key.Key_Right)
    assert win.current == 5
    qtbot.keyClick(win, Qt.Key.Key_Home)
    assert win.current == 0
    assert win.grid.current_row() == 0


def test_video_item_shows_video_view_and_space_toggles_play(win, folder):
    win.load_items(_items(folder), folder)
    assert win.current_item().kind is MediaKind.VIDEO
    assert win.content_stack.currentWidget() is win.video
    assert win.video.source_path() == folder / "clip.mp4"
    win.next_item()
    assert win.content_stack.currentWidget() is win.loupe


def test_image_loads_into_loupe_and_neighbors_preload(win, folder, qtbot):
    win.load_items(_items(folder), folder)
    win.next_item()  # IMG_1
    qtbot.waitUntil(lambda: win.loupe.has_image, timeout=5000)
    qtbot.waitUntil(lambda: all(i in win.image_cache for i in (1, 2, 3)), timeout=5000)
    assert 0 not in win.image_cache  # video is never decoded as image


def test_rating_keys_toggle_and_dispatch_write(win, folder, qtbot, monkeypatch):
    calls: list[tuple[str, int, Label]] = []
    real = metadata.write_rating_label

    def spy(path, kind, rating, label):
        calls.append((path.name, rating, label))
        real(path, kind, rating, label)

    monkeypatch.setattr(workers.metadata, "write_rating_label", spy)
    win.load_items(_items(folder), folder)
    win.next_item()
    item = win.current_item()

    with qtbot.waitSignal(win.signals.write_finished, timeout=5000):
        qtbot.keyClick(win, Qt.Key.Key_3)
    assert item.rating == 3 and "★★★☆☆" in win.header.text()
    with qtbot.waitSignal(win.signals.write_finished, timeout=5000):
        qtbot.keyClick(win, Qt.Key.Key_3)
    assert item.rating == 0
    with qtbot.waitSignal(win.signals.write_finished, timeout=5000):
        qtbot.keyClick(win, Qt.Key.Key_X)
    assert item.rating == -1 and "✕" in win.header.text()
    with qtbot.waitSignal(win.signals.write_finished, timeout=5000):
        qtbot.keyClick(win, Qt.Key.Key_X)
    assert item.rating == 0
    with qtbot.waitSignal(win.signals.write_finished, timeout=5000):
        qtbot.keyClick(win, Qt.Key.Key_7)
    assert item.label is Label.YELLOW and "[Yellow]" in win.header.text()
    with qtbot.waitSignal(win.signals.write_finished, timeout=5000):
        qtbot.keyClick(win, Qt.Key.Key_7)
    assert item.label is Label.NONE
    with qtbot.waitSignal(win.signals.write_finished, timeout=5000):
        qtbot.keyClick(win, Qt.Key.Key_0)
    assert [c[1:] for c in calls] == [(3, Label.NONE), (0, Label.NONE), (-1, Label.NONE), (0, Label.NONE),
                                      (0, Label.YELLOW), (0, Label.NONE), (0, Label.NONE)]
    assert metadata.read_rating_label(item.path, item.kind) == (0, Label.NONE)
    assert win.current == 1  # no auto-advance by default


def test_write_failure_marks_item_and_status(win, folder, qtbot, monkeypatch):
    def boom(*a, **k):
        raise metadata.MetadataError("locked")

    monkeypatch.setattr(workers.metadata, "write_rating_label", boom)
    win.load_items(_items(folder), folder)
    win.next_item()
    with qtbot.waitSignal(win.signals.write_finished, timeout=5000):
        qtbot.keyClick(win, Qt.Key.Key_4)
    item = win.current_item()
    assert item.rating == 4                       # in-memory state is kept
    assert item.write_error == "locked"
    assert "locked" in win.statusBar().currentMessage()
    assert "기록 실패" in win.header.text()


def test_filmstrip_click_changes_current(win, folder, qtbot):
    win.load_items(_items(folder), folder)
    win.filmstrip.row_activated.emit(3)
    assert win.current == 3


def test_thumbnails_arrive_in_model(win, folder, qtbot):
    win.load_items(_items(folder), folder)
    qtbot.waitUntil(lambda: win.model.thumbnail(1) is not None, timeout=5000)
    qtbot.waitUntil(lambda: win.model.data(win.model.index(0), win.model.FailedRole) is True, timeout=5000)


def test_z_toggles_zoom(win, folder, qtbot):
    win.load_items(_items(folder), folder)
    win.next_item()
    qtbot.waitUntil(lambda: win.loupe.has_image, timeout=5000)
    qtbot.keyClick(win, Qt.Key.Key_Z)
    assert not win.loupe.is_fit
    qtbot.keyClick(win, Qt.Key.Key_Z)
    assert win.loupe.is_fit


def test_open_folder_scans_in_background(win, folder, qtbot):
    with qtbot.waitSignal(win.signals.scan_finished, timeout=5000):
        win.open_folder(folder)
    qtbot.waitUntil(lambda: win.current == 0, timeout=2000)
    assert win.folder == folder
    assert len(win.model.items()) == 6


def test_open_missing_folder_reports_error(win, tmp_path, qtbot):
    with qtbot.waitSignal(win.signals.scan_failed, timeout=5000):
        win.open_folder(tmp_path / "nope")
    qtbot.waitUntil(lambda: "nope" in win.statusBar().currentMessage(), timeout=2000)
```

- [ ] **Step 2: 실패 확인**

Run: `python -m pytest tests/ui/test_main_window_core.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'ui.main_window'`

- [ ] **Step 3: main_window.py 구현**

`ui/main_window.py`:
```python
from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QSettings, Qt, QThreadPool
from PySide6.QtGui import QImage, QKeyEvent, QPixmap
from PySide6.QtWidgets import QLabel, QMainWindow, QMessageBox, QStackedWidget, QVBoxLayout, QWidget

from core.models import Label, MediaItem, MediaKind
from core.thumbnails import ThumbnailCache, default_cache_dir
from ui.image_cache import ImageCache
from ui.loupe_view import LoupeView
from ui.media_list_model import MediaListModel
from ui.thumb_views import Filmstrip, GridView
from ui.video_view import VideoView
from ui.workers import ImageLoadJob, MetadataWriteJob, ScanJob, ThumbnailJob, WorkerSignals

PRELOAD_OFFSETS = (1, -1, 2, -2)
EMPTY_TEXT = "폴더를 열어주세요 (Ctrl+O)"
_RATING_KEYS = {Qt.Key.Key_1: 1, Qt.Key.Key_2: 2, Qt.Key.Key_3: 3, Qt.Key.Key_4: 4, Qt.Key.Key_5: 5}
_LABEL_KEYS = {Qt.Key.Key_6: Label.RED, Qt.Key.Key_7: Label.YELLOW, Qt.Key.Key_8: Label.GREEN, Qt.Key.Key_9: Label.BLUE}


class MainWindow(QMainWindow):
    def __init__(self, thumb_cache: ThumbnailCache | None = None, settings: QSettings | None = None, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Photo Culling Viewer")
        self.resize(1280, 800)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.suppress_dialogs = False

        self.settings = settings or QSettings("WindowPhotoViewer", "WindowPhotoViewer")
        self.thumb_cache = thumb_cache or ThumbnailCache(default_cache_dir())
        self.image_cache = ImageCache(6)
        self.model = MediaListModel(self)
        self.folder: Path | None = None
        self._loading_folder: Path | None = None
        self.current: int = -1
        self._pending_images: set[int] = set()
        self._index_by_id: dict[int, int] = {}

        self.signals = WorkerSignals(self)
        self.signals.scan_finished.connect(self._on_scan_finished)
        self.signals.scan_failed.connect(self._on_scan_failed)
        self.signals.thumbnail_ready.connect(self._on_thumbnail_ready)
        self.signals.thumbnail_failed.connect(self._on_thumbnail_failed)
        self.signals.image_ready.connect(self._on_image_ready)
        self.signals.image_failed.connect(self._on_image_failed)
        self.signals.write_finished.connect(self._on_write_finished)

        self.thumb_pool = QThreadPool(self)
        self.thumb_pool.setMaxThreadCount(4)
        self.image_pool = QThreadPool(self)
        self.image_pool.setMaxThreadCount(2)
        self.write_pool = QThreadPool(self)
        self.write_pool.setMaxThreadCount(1)   # serialize writes: never two jobs on one file
        self.scan_pool = QThreadPool(self)
        self.scan_pool.setMaxThreadCount(1)

        self._build_ui()
        self._set_current(-1)

    # ---------------- UI ----------------
    def _build_ui(self) -> None:
        self.header = QLabel()
        self.header.setStyleSheet("padding:4px 8px; background:#181818; color:#dddddd;")

        self.loupe = LoupeView()
        self.video = VideoView()
        self.content_stack = QStackedWidget()
        self.content_stack.addWidget(self.loupe)
        self.content_stack.addWidget(self.video)

        self.filmstrip = Filmstrip()
        self.filmstrip.setModel(self.model)
        self.filmstrip.row_activated.connect(self._on_row_activated)

        self.loupe_page = QWidget()
        lp = QVBoxLayout(self.loupe_page)
        lp.setContentsMargins(0, 0, 0, 0)
        lp.setSpacing(0)
        lp.addWidget(self.content_stack, 1)
        lp.addWidget(self.filmstrip, 0)

        self.grid = GridView()
        self.grid.setModel(self.model)
        self.grid.row_activated.connect(self._on_row_activated)

        self.mode_stack = QStackedWidget()
        self.mode_stack.addWidget(self.loupe_page)
        self.mode_stack.addWidget(self.grid)

        central = QWidget()
        root = QVBoxLayout(central)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        root.addWidget(self.header, 0)
        root.addWidget(self.mode_stack, 1)
        self.setCentralWidget(central)
        self.statusBar()

    # ---------------- loading ----------------
    def load_items(self, items: list[MediaItem], folder: Path | None) -> None:
        self.folder = folder
        self.video.stop()
        self.image_cache.clear()
        self._pending_images.clear()
        self.model.set_items(items)
        self._index_by_id = {id(it): i for i, it in enumerate(self.model.items())}
        visible = self.model.visible_indices()
        self._set_current(visible[0] if visible else -1)
        self._request_thumbnails(self._priority_order())
        self.statusBar().showMessage(f"{len(items)}개 항목", 3000)

    def open_folder(self, folder: Path) -> None:
        self._loading_folder = folder
        self.statusBar().showMessage(f"불러오는 중: {folder}")
        self.scan_pool.start(ScanJob(folder, self.signals))

    def _on_scan_finished(self, items: list[MediaItem]) -> None:
        self.load_items(items, self._loading_folder)

    def _on_scan_failed(self, message: str) -> None:
        self._show_error(f"폴더를 열 수 없습니다: {message}")

    def _show_error(self, message: str) -> None:
        self.statusBar().showMessage(message, 10000)
        if not self.suppress_dialogs:
            QMessageBox.warning(self, "오류", message)

    # ---------------- lookup helpers ----------------
    def _index_of(self, item: MediaItem) -> int:
        return self._index_by_id.get(id(item), -1)

    def current_item(self) -> MediaItem | None:
        items = self.model.items()
        return items[self.current] if 0 <= self.current < len(items) else None

    def _active_view(self):
        return self.filmstrip

    # ---------------- thumbnails ----------------
    def _priority_order(self) -> list[int]:
        on_screen = [self.model.item_index_at_row(r) for r in self._active_view().visible_rows()]
        seen = set(on_screen)
        visible_rest = [i for i in self.model.visible_indices() if i not in seen]
        seen.update(visible_rest)
        hidden = [i for i in range(len(self.model.items())) if i not in seen]
        return on_screen + visible_rest + hidden

    def _request_thumbnails(self, indices: list[int]) -> None:
        items = self.model.items()
        for idx in indices:
            if self.model.has_thumbnail_request(idx):
                continue
            self.model.mark_requested(idx)
            self.thumb_pool.start(ThumbnailJob(items[idx], self.thumb_cache, self.signals))

    def _on_thumbnail_ready(self, item: MediaItem, path: str) -> None:
        idx = self._index_of(item)
        if idx < 0:
            return
        pixmap = QPixmap(path)
        if pixmap.isNull():
            self.model.set_thumbnail_failed(idx)
        else:
            self.model.set_thumbnail(idx, pixmap)

    def _on_thumbnail_failed(self, item: MediaItem, _message: str) -> None:
        idx = self._index_of(item)
        if idx >= 0:
            self.model.set_thumbnail_failed(idx)

    # ---------------- current item / display ----------------
    def _set_current(self, idx: int) -> None:
        self.current = idx
        row = self.model.row_for_item_index(idx) if idx >= 0 else -1
        if row >= 0:
            self.filmstrip.set_current_row(row)
            self.grid.set_current_row(row)
        self._show_current()
        self._preload_neighbors()
        self._update_header()

    def _show_current(self) -> None:
        self.video.stop()
        item = self.current_item()
        if item is None:
            self.content_stack.setCurrentWidget(self.loupe)
            self.loupe.set_placeholder(EMPTY_TEXT)
            return
        if item.kind is MediaKind.VIDEO:
            self.video.load(item.path)
            self.content_stack.setCurrentWidget(self.video)
            return
        self.content_stack.setCurrentWidget(self.loupe)
        image = self.image_cache.get(self.current)
        if image is not None:
            self.loupe.set_image(image)
        else:
            self.loupe.set_placeholder("불러오는 중…")
            self._request_image(self.current)

    def _request_image(self, idx: int) -> None:
        if idx in self._pending_images or idx in self.image_cache:
            return
        item = self.model.items()[idx]
        if item.kind is not MediaKind.IMAGE:
            return
        self._pending_images.add(idx)
        self.image_pool.start(ImageLoadJob(item, self.signals))

    def _preload_neighbors(self) -> None:
        visible = self.model.visible_indices()
        if self.current not in visible:
            return
        pos = visible.index(self.current)
        for offset in PRELOAD_OFFSETS:
            p = pos + offset
            if 0 <= p < len(visible):
                self._request_image(visible[p])

    def _on_image_ready(self, item: MediaItem, image: QImage) -> None:
        idx = self._index_of(item)
        if idx < 0:
            return
        self._pending_images.discard(idx)
        self.image_cache.put(idx, image)
        if idx == self.current:
            self.loupe.set_image(image)

    def _on_image_failed(self, item: MediaItem, message: str) -> None:
        idx = self._index_of(item)
        if idx < 0:
            return
        self._pending_images.discard(idx)
        if idx == self.current:
            self.loupe.set_placeholder(f"표시할 수 없음\n{item.path.name}\n{message}")

    def _update_header(self) -> None:
        item = self.current_item()
        if item is None:
            self.header.setText(EMPTY_TEXT)
            return
        visible = self.model.visible_indices()
        pos = visible.index(self.current) + 1 if self.current in visible else 0
        parts = [
            str(self.folder) if self.folder else "",
            item.stars(),
            f"[{item.label.value}]" if item.label is not Label.NONE else "",
            item.path.name,
            f"{pos}/{len(visible)}",
            item.exif.format() if item.exif else "",
            f"filter: {self.model.filter().describe()}" if self.model.filter().is_active else "",
            "⚠ 기록 실패" if item.write_error else "",
        ]
        self.header.setText("   ".join(p for p in parts if p))

    # ---------------- navigation ----------------
    def _step(self, delta: int) -> None:
        visible = self.model.visible_indices()
        if not visible:
            return
        if self.current not in visible:
            self._set_current(visible[0])
            return
        pos = max(0, min(len(visible) - 1, visible.index(self.current) + delta))
        if visible[pos] != self.current:
            self._set_current(visible[pos])

    def next_item(self) -> None:
        self._step(1)

    def prev_item(self) -> None:
        self._step(-1)

    def first_item(self) -> None:
        visible = self.model.visible_indices()
        if visible:
            self._set_current(visible[0])

    def last_item(self) -> None:
        visible = self.model.visible_indices()
        if visible:
            self._set_current(visible[-1])

    def _on_row_activated(self, row: int) -> None:
        if 0 <= row < self.model.rowCount():
            self._set_current(self.model.item_index_at_row(row))

    # ---------------- rating / label ----------------
    def set_rating(self, rating: int) -> None:
        item = self.current_item()
        if item is None:
            return
        new = 0 if (rating != 0 and item.rating == rating) else rating
        self._apply_change(item, rating=new)

    def toggle_reject(self) -> None:
        item = self.current_item()
        if item is None:
            return
        self._apply_change(item, rating=0 if item.is_rejected else -1)

    def set_label(self, label: Label) -> None:
        item = self.current_item()
        if item is None:
            return
        self._apply_change(item, label=Label.NONE if item.label is label else label)

    def _apply_change(self, item: MediaItem, *, rating: int | None = None, label: Label | None = None) -> None:
        idx = self.current
        if rating is not None:
            item.rating = rating
        if label is not None:
            item.label = label
        item.write_error = None
        self.model.item_changed(idx)
        self.write_pool.start(MetadataWriteJob(item, self.signals))
        self._update_header()

    def _on_write_finished(self, item: MediaItem, error: str) -> None:
        idx = self._index_of(item)
        if idx < 0:
            return
        item.write_error = error or None
        self.model.item_changed(idx)
        if error:
            self.statusBar().setStyleSheet("color:#ff4040;")
            self.statusBar().showMessage(f"기록 실패: {error}", 15000)
        else:
            self.statusBar().setStyleSheet("")
        if idx == self.current:
            self._update_header()

    # ---------------- keys ----------------
    def keyPressEvent(self, event: QKeyEvent) -> None:  # noqa: N802
        key = event.key()
        if event.modifiers() & (Qt.KeyboardModifier.AltModifier | Qt.KeyboardModifier.ControlModifier):
            super().keyPressEvent(event)
            return
        if key == Qt.Key.Key_Space and self.content_stack.currentWidget() is self.video:
            self.video.toggle_play()
        elif key in (Qt.Key.Key_Right, Qt.Key.Key_Space):
            self.next_item()
        elif key in (Qt.Key.Key_Left, Qt.Key.Key_Backspace):
            self.prev_item()
        elif key == Qt.Key.Key_Home:
            self.first_item()
        elif key == Qt.Key.Key_End:
            self.last_item()
        elif key in _RATING_KEYS:
            self.set_rating(_RATING_KEYS[key])
        elif key == Qt.Key.Key_0:
            self.set_rating(0)
        elif key == Qt.Key.Key_X:
            self.toggle_reject()
        elif key in _LABEL_KEYS:
            self.set_label(_LABEL_KEYS[key])
        elif key == Qt.Key.Key_Z:
            self.loupe.toggle_zoom()
        else:
            super().keyPressEvent(event)
            return
        event.accept()
```

- [ ] **Step 4: 통과 확인**

Run: `python -m pytest tests/ui/test_main_window_core.py -v`
Expected: 12 PASS. `test_rating_keys_toggle_and_dispatch_write`의 `calls` 비교가 순서 때문에 실패하면 `write_pool`이 1스레드인지(`setMaxThreadCount(1)`) 확인한다 — 직렬 실행이 보장돼야 순서가 맞는다.

- [ ] **Step 5: Commit**

```bash
git add ui/main_window.py tests/ui/test_main_window_core.py
git commit -m "feat(ui): MainWindow — 탐색, 별점/라벨 입력, 백그라운드 기록, 프리로드

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 12: MainWindow — 모드 전환, 필터, 전체화면, 폴더 열기 다이얼로그, 설정, 파일 소실 처리

**Files:**
- Modify: `ui/main_window.py`
- Test: `tests/ui/test_main_window_modes.py`

**Interfaces:**
- Produces (추가 메서드): `show_grid()`, `show_loupe()`, `is_grid -> bool`, `toggle_fullscreen()`, `set_filter(f: Filter)`, `clear_filter()`, `auto_advance -> bool` (property, setter; `QSettings` 키 `auto_advance`), `last_folder() -> Path | None` (`QSettings` 키 `last_folder`), `choose_folder()` (QFileDialog), `_remove_item(idx)`
- 키: `G`/`E`, `F`/`F11`, `Escape`(전체화면 해제), `Alt+1~5`, `Alt+X`, `Alt+0`, `Ctrl+O`, `Ctrl+Shift+A`(자동 전진 토글)

- [ ] **Step 1: 테스트 작성**

`tests/ui/test_main_window_modes.py`:
```python
from __future__ import annotations

from pathlib import Path

import pytest
from PySide6.QtCore import QSettings, Qt

from core import metadata
from core.filters import Filter
from core.scanner import scan
from core.thumbnails import ThumbnailCache
from tests.helpers import make_jpeg
from ui.main_window import MainWindow


@pytest.fixture
def folder(tmp_path: Path) -> Path:
    for i, rating in enumerate([0, 3, 5, -1, 2], start=1):
        p = make_jpeg(tmp_path / f"IMG_{i}.jpg", size=(120, 80))
        if rating:
            metadata.write_rating_label(p, metadata.MediaKind.IMAGE, rating, metadata.Label.NONE)
    return tmp_path


def _items(folder: Path):
    items = scan(folder)
    for it in items:
        metadata.populate(it)
    return items


@pytest.fixture
def win(qtbot, tmp_path: Path):
    settings = QSettings(str(tmp_path / "settings.ini"), QSettings.Format.IniFormat)
    w = MainWindow(thumb_cache=ThumbnailCache(tmp_path / "cache"), settings=settings)
    w.suppress_dialogs = True
    qtbot.addWidget(w)
    w.resize(1000, 700)
    w.show()
    qtbot.waitExposed(w)
    return w


def test_grid_and_loupe_switch(win, folder, qtbot):
    win.load_items(_items(folder), folder)
    assert win.is_grid is False
    qtbot.keyClick(win, Qt.Key.Key_G)
    assert win.is_grid is True and win.mode_stack.currentWidget() is win.grid
    qtbot.keyClick(win, Qt.Key.Key_E)
    assert win.is_grid is False


def test_grid_double_click_opens_loupe_on_item(win, folder):
    win.load_items(_items(folder), folder)
    win.show_grid()
    win.grid.row_double_clicked.emit(3)
    assert win.is_grid is False and win.current == 3


def test_filter_keys(win, folder, qtbot):
    win.load_items(_items(folder), folder)          # ratings: 0,3,5,-1,2
    qtbot.keyClick(win, Qt.Key.Key_3, Qt.KeyboardModifier.AltModifier)
    assert win.model.visible_indices() == [1, 2]
    assert win.current == 1                          # current was hidden → first visible
    assert "★3+" in win.header.text() and "1/2" in win.header.text()
    qtbot.keyClick(win, Qt.Key.Key_Right)
    qtbot.keyClick(win, Qt.Key.Key_Right)
    assert win.current == 2                          # stays within the filtered list
    qtbot.keyClick(win, Qt.Key.Key_X, Qt.KeyboardModifier.AltModifier)
    assert win.model.visible_indices() == [3] and win.current == 3
    qtbot.keyClick(win, Qt.Key.Key_0, Qt.KeyboardModifier.AltModifier)
    assert win.model.visible_indices() == [0, 1, 2, 3, 4]
    assert win.current == 3                          # clearing keeps current


def test_rating_change_drops_item_out_of_filter_and_advances(win, folder, qtbot):
    win.load_items(_items(folder), folder)
    win.set_filter(Filter(min_rating=3))             # visible [1, 2], current 1
    with qtbot.waitSignal(win.signals.write_finished, timeout=5000):
        qtbot.keyClick(win, Qt.Key.Key_1)            # IMG_2 → 1 star, no longer matches
    assert win.model.visible_indices() == [2]
    assert win.current == 2


def test_filter_with_no_matches_shows_empty_state(win, folder, qtbot):
    win.load_items(_items(folder), folder)
    win.set_filter(Filter(min_rating=5))
    win.set_rating(4)                                # only 5★ item drops out
    assert win.model.visible_indices() == []
    assert win.current == -1
    assert "filter" in win.header.text() or "Ctrl+O" in win.header.text()
    win.clear_filter()
    assert win.current == 0


def test_fullscreen_toggle(win, folder, qtbot):
    win.load_items(_items(folder), folder)
    qtbot.keyClick(win, Qt.Key.Key_F)
    assert win.isFullScreen()
    qtbot.keyClick(win, Qt.Key.Key_Escape)
    assert not win.isFullScreen()
    qtbot.keyClick(win, Qt.Key.Key_F11)
    assert win.isFullScreen()
    qtbot.keyClick(win, Qt.Key.Key_F11)
    assert not win.isFullScreen()


def test_auto_advance_setting(win, folder, qtbot):
    win.load_items(_items(folder), folder)
    assert win.auto_advance is False
    win.auto_advance = True
    assert win.settings.value("auto_advance", type=bool) is True
    with qtbot.waitSignal(win.signals.write_finished, timeout=5000):
        qtbot.keyClick(win, Qt.Key.Key_2)
    assert win.current == 1
    with qtbot.waitSignal(win.signals.write_finished, timeout=5000):
        qtbot.keyClick(win, Qt.Key.Key_0)            # clearing never advances
    assert win.current == 1


def test_open_folder_remembers_last_folder(win, folder, qtbot):
    assert win.last_folder() is None
    with qtbot.waitSignal(win.signals.scan_finished, timeout=5000):
        win.open_folder(folder)
    qtbot.waitUntil(lambda: win.folder == folder, timeout=2000)
    assert win.last_folder() == folder
    assert win.current == 0


def test_missing_file_is_removed_when_shown(win, folder, qtbot):
    items = _items(folder)
    win.load_items(items, folder)
    qtbot.waitUntil(lambda: 1 in win.image_cache and not win._pending_images, timeout=5000)  # preload settled
    (folder / "IMG_2.jpg").unlink()
    win.image_cache.clear()                          # force a fresh decode attempt of the missing file
    win.next_item()                                  # tries to show IMG_2 → load fails
    qtbot.waitUntil(lambda: len(win.model.items()) == 4, timeout=5000)
    assert [i.path.name for i in win.model.items()] == ["IMG_1.jpg", "IMG_3.jpg", "IMG_4.jpg", "IMG_5.jpg"]
    assert win.current_item().path.name == "IMG_3.jpg"
    assert "IMG_2.jpg" in win.statusBar().currentMessage()


def test_thumbnail_priority_uses_active_view(win, folder):
    win.load_items(_items(folder), folder)
    win.show_grid()
    assert win._active_view() is win.grid
    win.show_loupe()
    assert win._active_view() is win.filmstrip
```

- [ ] **Step 2: 실패 확인**

Run: `python -m pytest tests/ui/test_main_window_modes.py -v`
Expected: FAIL — `AttributeError: 'MainWindow' object has no attribute 'is_grid'` 등

- [ ] **Step 3: main_window.py 수정 — import 추가**

파일 상단 import 블록을 다음으로 교체:
```python
from PySide6.QtCore import QSettings, Qt, QThreadPool
from PySide6.QtGui import QAction, QImage, QKeyEvent, QKeySequence, QPixmap
from PySide6.QtWidgets import (
    QFileDialog, QLabel, QMainWindow, QMessageBox, QStackedWidget, QVBoxLayout, QWidget,
)

from core.filters import NO_FILTER, Filter
from core.models import Label, MediaItem, MediaKind
```
(`from core.models ...` 줄은 기존 그대로, `core.filters` import만 추가된다.)

- [ ] **Step 4: `__init__` 끝부분과 `_build_ui` 수정**

`__init__`의 `self._build_ui()` 바로 뒤에 `self._build_menu()` 를 추가:
```python
        self._build_ui()
        self._build_menu()
        self._set_current(-1)
```

`_build_ui` 안의 `self.grid.row_activated.connect(self._on_row_activated)` 다음 줄에 추가:
```python
        self.grid.row_double_clicked.connect(self._on_grid_double_clicked)
```

`_build_ui` 메서드 바로 아래에 새 메서드 추가:
```python
    def _build_menu(self) -> None:
        file_menu = self.menuBar().addMenu("파일(&F)")
        open_action = QAction("폴더 열기…", self)
        open_action.setShortcut(QKeySequence("Ctrl+O"))
        open_action.triggered.connect(self.choose_folder)
        file_menu.addAction(open_action)

        view_menu = self.menuBar().addMenu("보기(&V)")
        self.auto_advance_action = QAction("별점 후 자동 다음", self)
        self.auto_advance_action.setCheckable(True)
        self.auto_advance_action.setChecked(self.auto_advance)
        self.auto_advance_action.setShortcut(QKeySequence("Ctrl+Shift+A"))
        self.auto_advance_action.toggled.connect(lambda on: setattr(self, "auto_advance", on))
        view_menu.addAction(self.auto_advance_action)
```

- [ ] **Step 5: 설정·폴더 다이얼로그 메서드 추가**

`_show_error` 아래에 추가:
```python
    # ---------------- settings ----------------
    @property
    def auto_advance(self) -> bool:
        return bool(self.settings.value("auto_advance", False, type=bool))

    @auto_advance.setter
    def auto_advance(self, on: bool) -> None:
        self.settings.setValue("auto_advance", bool(on))
        if hasattr(self, "auto_advance_action") and self.auto_advance_action.isChecked() != bool(on):
            self.auto_advance_action.setChecked(bool(on))

    def last_folder(self) -> Path | None:
        value = self.settings.value("last_folder", "", type=str)
        return Path(value) if value else None

    def choose_folder(self) -> None:
        start = str(self.last_folder() or Path.home())
        chosen = QFileDialog.getExistingDirectory(self, "사진 폴더 선택", start)
        if chosen:
            self.open_folder(Path(chosen))
```

`_on_scan_finished`를 다음으로 교체:
```python
    def _on_scan_finished(self, items: list[MediaItem]) -> None:
        self.load_items(items, self._loading_folder)
        if self._loading_folder is not None:
            self.settings.setValue("last_folder", str(self._loading_folder))
```

- [ ] **Step 6: 모드 전환·전체화면·필터 메서드 추가**

`_active_view`를 다음으로 교체:
```python
    def _active_view(self):
        return self.grid if self.is_grid else self.filmstrip

    # ---------------- modes ----------------
    @property
    def is_grid(self) -> bool:
        return self.mode_stack.currentWidget() is self.grid

    def show_grid(self) -> None:
        self.video.stop()
        self.mode_stack.setCurrentWidget(self.grid)
        row = self.model.row_for_item_index(self.current)
        if row >= 0:
            self.grid.set_current_row(row)
        self._request_thumbnails(self._priority_order())

    def show_loupe(self) -> None:
        self.mode_stack.setCurrentWidget(self.loupe_page)
        self._show_current()

    def _on_grid_double_clicked(self, row: int) -> None:
        self._on_row_activated(row)
        self.show_loupe()

    def toggle_fullscreen(self) -> None:
        if self.isFullScreen():
            self.showNormal()
        else:
            self.showFullScreen()

    # ---------------- filter ----------------
    def set_filter(self, f: Filter) -> None:
        self.model.set_filter(f)
        self._reconcile_current_after_filter()

    def clear_filter(self) -> None:
        self.set_filter(NO_FILTER)

    def _reconcile_current_after_filter(self) -> None:
        """Keep current if still visible; otherwise move to the next visible item (or the last, or none)."""
        visible = self.model.visible_indices()
        if self.current in visible:
            self._set_current(self.current)   # re-sync views / header
            return
        if not visible:
            self._set_current(-1)
            return
        later = [i for i in visible if i > self.current]
        self._set_current(later[0] if later else visible[-1])
```

- [ ] **Step 7: `_apply_change` 교체 — 필터 탈락과 자동 전진**

```python
    def _apply_change(self, item: MediaItem, *, rating: int | None = None, label: Label | None = None) -> None:
        idx = self.current
        if rating is not None:
            item.rating = rating
        if label is not None:
            item.label = label
        item.write_error = None
        self.model.item_changed(idx)
        self.write_pool.start(MetadataWriteJob(item, self.signals))

        if self.model.filter().is_active and not self.model.filter().matches(item):
            self.model.refresh_filter()
            self._reconcile_current_after_filter()
            return
        if rating is not None and rating > 0 and self.auto_advance:
            self.next_item()
            return
        self._update_header()
```

- [ ] **Step 8: `_on_image_failed` 교체와 `_remove_item` 추가 — 파일 소실 처리**

```python
    def _on_image_failed(self, item: MediaItem, message: str) -> None:
        idx = self._index_of(item)
        if idx < 0:
            return
        self._pending_images.discard(idx)
        if not item.path.exists():
            self._remove_item(idx)
            return
        if idx == self.current:
            self.loupe.set_placeholder(f"표시할 수 없음\n{item.path.name}\n{message}")

    def _remove_item(self, idx: int) -> None:
        items = list(self.model.items())
        removed = items.pop(idx)
        was_current = self.current
        keep_filter = self.model.filter()
        self.image_cache.clear()
        self._pending_images.clear()
        self.model.set_items(items)
        self.model.set_filter(keep_filter)
        self._index_by_id = {id(it): i for i, it in enumerate(items)}
        visible = self.model.visible_indices()
        if not visible:
            self._set_current(-1)
        else:
            candidates = [i for i in visible if i >= min(was_current, len(items) - 1)]
            self._set_current(candidates[0] if candidates else visible[-1])
        self._request_thumbnails(self._priority_order())
        self.statusBar().showMessage(f"파일이 사라져 목록에서 제외: {removed.path.name}", 8000)
```

- [ ] **Step 9: `keyPressEvent` 교체 — 전체 키맵**

```python
    def keyPressEvent(self, event: QKeyEvent) -> None:  # noqa: N802
        key = event.key()
        mods = event.modifiers()
        if mods & Qt.KeyboardModifier.ControlModifier:
            super().keyPressEvent(event)   # Ctrl+O / Ctrl+Shift+A are QAction shortcuts
            return
        if mods & Qt.KeyboardModifier.AltModifier:
            if key in _RATING_KEYS:
                self.set_filter(Filter(min_rating=_RATING_KEYS[key]))
            elif key == Qt.Key.Key_X:
                self.set_filter(Filter(rejected_only=True))
            elif key == Qt.Key.Key_0:
                self.clear_filter()
            else:
                super().keyPressEvent(event)
                return
            event.accept()
            return

        if key == Qt.Key.Key_Space and self.content_stack.currentWidget() is self.video and not self.is_grid:
            self.video.toggle_play()
        elif key in (Qt.Key.Key_Right, Qt.Key.Key_Space):
            self.next_item()
        elif key in (Qt.Key.Key_Left, Qt.Key.Key_Backspace):
            self.prev_item()
        elif key == Qt.Key.Key_Home:
            self.first_item()
        elif key == Qt.Key.Key_End:
            self.last_item()
        elif key in _RATING_KEYS:
            self.set_rating(_RATING_KEYS[key])
        elif key == Qt.Key.Key_0:
            self.set_rating(0)
        elif key == Qt.Key.Key_X:
            self.toggle_reject()
        elif key in _LABEL_KEYS:
            self.set_label(_LABEL_KEYS[key])
        elif key == Qt.Key.Key_Z:
            self.loupe.toggle_zoom()
        elif key == Qt.Key.Key_G:
            self.show_grid()
        elif key == Qt.Key.Key_E:
            self.show_loupe()
        elif key in (Qt.Key.Key_F, Qt.Key.Key_F11):
            self.toggle_fullscreen()
        elif key == Qt.Key.Key_Escape and self.isFullScreen():
            self.showNormal()
        else:
            super().keyPressEvent(event)
            return
        event.accept()
```

- [ ] **Step 10: 통과 확인 (전체)**

Run: `python -m pytest -v`
Expected: 전부 PASS. `test_fullscreen_toggle`이 offscreen에서 `isFullScreen()` False를 돌려주면 `qtbot.waitUntil(lambda: win.isFullScreen(), timeout=1000)` 로 상태 반영을 기다리도록 테스트를 조정한다.

- [ ] **Step 11: Commit**

```bash
git add ui/main_window.py tests/ui/test_main_window_modes.py
git commit -m "feat(ui): 모드 전환, 필터, 전체화면, 폴더 열기, 설정, 파일 소실 처리

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 13: 진입점, README, PyInstaller 빌드, 수동 검증

**Files:**
- Modify: `app.py`
- Create: `README.md`, `build/viewer.spec`
- Test: `tests/test_app.py`

**Interfaces:**
- Produces (`app.py`): `resolve_start_folder(argv: list[str], fallback: Path | None) -> Path | None`, `main(argv: list[str] | None = None) -> int`

- [ ] **Step 1: 테스트 작성**

`tests/test_app.py`:
```python
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
```

- [ ] **Step 2: 실패 확인**

Run: `python -m pytest tests/test_app.py -v`
Expected: FAIL — `ImportError: cannot import name 'resolve_start_folder'`

- [ ] **Step 3: app.py 작성**

`app.py`:
```python
"""Photo Culling Viewer entry point.

Usage: python app.py [folder-or-image-path]
Without an argument the last opened folder (QSettings) is restored.
"""
from __future__ import annotations

import sys
from pathlib import Path

from PySide6.QtWidgets import QApplication

from ui.main_window import MainWindow


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
    app.setApplicationName("WindowPhotoViewer")
    app.setOrganizationName("WindowPhotoViewer")
    window = MainWindow()
    window.show()
    folder = resolve_start_folder(argv, window.last_folder())
    if folder is not None:
        window.open_folder(folder)
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 4: 통과 확인**

Run: `python -m pytest tests/test_app.py -v`
Expected: 4 PASS

- [ ] **Step 5: PyInstaller 스펙**

`build/viewer.spec`:
```python
# -*- mode: python ; coding: utf-8 -*-
# Build:  python -m PyInstaller build/viewer.spec --noconfirm
from PyInstaller.utils.hooks import collect_data_files, collect_dynamic_libs

binaries = collect_dynamic_libs("pyexiv2")
datas = collect_data_files("imageio_ffmpeg")   # bundled ffmpeg.exe

a = Analysis(
    ["../app.py"],
    pathex=[".."],
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
```

Run: `python -m PyInstaller build/viewer.spec --noconfirm --distpath dist --workpath build/work`
Expected: `dist/WindowPhotoViewer/WindowPhotoViewer.exe` 생성. 실행해 창이 뜨는지 확인. `pyexiv2` DLL 로드 오류가 나면 `collect_dynamic_libs("pyexiv2")` 결과가 비었는지 `python -c "from PyInstaller.utils.hooks import collect_dynamic_libs; print(collect_dynamic_libs('pyexiv2'))"` 로 확인하고, 비어 있으면 `binaries += collect_data_files("pyexiv2", includes=["**/*.dll", "**/*.pyd"])` 를 추가한다.

- [ ] **Step 6: README 작성**

`README.md`:
```markdown
# Photo Culling Viewer

촬영 직후 JPEG 폴더를 키보드로 훑으며 별점을 매기는 Windows 뷰어.
별점·색 라벨은 **JPEG 내장 XMP**(`xmp:Rating`, `xmp:Label`)에 기록되어 Lightroom Classic 임포트 시 그대로 읽힌다.

## 실행

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -e ".[dev]"
python app.py D:\Photos\2026-08-27
```

인자를 생략하면 마지막으로 열었던 폴더를 다시 연다.

## 단축키

| 키 | 동작 |
|---|---|
| `→` `Space` / `←` `Backspace` | 다음 / 이전 (영상 표시 중 `Space`는 재생·일시정지) |
| `Home` `End` | 첫 / 마지막 |
| `Z` 또는 클릭 | fit ↔ 100% (100%에서 드래그로 이동) |
| `1`~`5` | 별점 (같은 키 다시 누르면 해제) |
| `0` | 별점 해제 |
| `X` | Reject 토글 |
| `6` `7` `8` `9` | 라벨 Red / Yellow / Green / Blue (다시 누르면 해제) |
| `G` / `E` | 그리드 ↔ 루프 |
| `F` `F11` / `Esc` | 전체화면 토글 / 해제 |
| `Ctrl+O` | 폴더 열기 |
| `Alt+1`~`Alt+5` | N성 이상만 보기 |
| `Alt+X` | reject만 보기 |
| `Alt+0` | 필터 해제 |
| `Ctrl+Shift+A` | 별점 후 자동 다음 (기본 꺼짐) |

## Lightroom Classic 호환 주의

- 임포트 **전**에 별점을 매기면 LR이 자동으로 읽는다.
- 이미 카탈로그에 있는 파일을 바꿨다면 LR에서 `Metadata → Read Metadata from File` 을 실행해야 반영된다.
- LR은 Pick/Reject 플래그를 파일로 주고받지 않는다. 이 뷰어의 Reject(`xmp:Rating=-1`)는 LR에서 "별점 없음"으로 보인다. Reject 모아보기(`Alt+X`)로 확인 후 삭제는 직접 한다.
- 영상(`.mp4` `.mov`)의 별점은 옆에 생기는 `영상명.xmp` 사이드카에 저장된다. LR은 이를 읽지 않는다 (뷰어 내부용, Bridge/digiKam 호환).

## 파일에 미치는 영향

- JPEG: XMP 패킷만 교체한다. 픽셀 데이터와 EXIF는 그대로. 임시 파일에 쓴 뒤 원자적으로 교체하므로 중간에 꺼져도 원본이 깨지지 않는다. 파일 수정 시각은 갱신된다.
- 썸네일 캐시: `%LOCALAPPDATA%\WindowPhotoViewer\thumbs\` — 지워도 다시 생성된다.

## 배포 빌드

```powershell
python -m PyInstaller build/viewer.spec --noconfirm --distpath dist --workpath build/work
dist\WindowPhotoViewer\WindowPhotoViewer.exe
```

## 테스트

```powershell
python -m pytest
```
```

- [ ] **Step 7: 전체 테스트와 수동 검증**

Run: `python -m pytest -v`
Expected: 전부 PASS (exiftool 없으면 1 SKIP).

수동 검증 (실제 사진 폴더로 `python app.py <폴더>`):
1. 300장 폴더 열기 → 1초 내 첫 사진 표시, 필름스트립 썸네일이 왼쪽부터 채워진다.
2. `→` 연타 → 지연 없이 넘어간다. `Z` → 100%, 드래그 패닝, `Z` → fit.
3. `3` → 헤더 ★★★☆☆, 필름스트립에 ★★★. 탐색기에서 파일 속성 → 자세히 → "등급" 3점 확인.
4. Lightroom Classic 임포트 → 별점 3 확인. 라벨 `6`(Red) → LR 색 라벨 Red 확인.
5. 영상 항목 → `Space` 재생/일시정지. `→`로 넘어가면 재생이 멈춘다.
6. `G` 그리드 → 더블클릭 → 해당 사진 루프.
7. `Alt+3` → 3성 이상만, 헤더 `filter: ★3+`. 그 상태에서 `1` → 사진이 목록에서 사라지고 다음으로 이동.
8. 읽기 전용 파일에 별점 → 상태바 빨간 "기록 실패", 썸네일 `!`.

- [ ] **Step 8: Commit**

```bash
git add app.py README.md build/viewer.spec tests/test_app.py
git commit -m "feat: 진입점, README, PyInstaller 빌드 스펙

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

## 스펙 대비 커버리지 (자체 점검)

| 스펙 항목 | 태스크 |
|---|---|
| §1 JPEG+영상, 폴더 단위 | 2 (스캐너), 11 (로드) |
| §3 core/ui 분리, Qt 무의존 가드 | 1, 전체 |
| §3.2 데이터 흐름: 스캔→썸네일(보이는 것 우선)→±2 프리로드→백그라운드 기록 | 6, 11 |
| §4.1 두 모드, 헤더, 필름스트립, 영상 교체 표시 | 8, 11, 12 |
| §4.2 단축키 전체 | 11, 12 |
| §4.3 자동 전진 off 기본·설정, 필터 내 이동, 항상 첫 항목 | 11, 12 |
| §5 내장 XMP, 태그 삭제, 원자 교체, mtime 갱신, 영상 사이드카, Reject 의미 | 3 |
| §5.2 LR "Read Metadata from File" 안내 | 13 (README) |
| §6 draft 디코딩·4워커·디스크 캐시·LRU 6장·ffmpeg 프레임 | 4, 6, 11 |
| §7 손상 파일 플레이스홀더, 폴더 실패, 파일 삭제됨, 쓰기 실패 표시, 영상 재생 불가, 캐시 디렉토리 실패 | 4, 11, 12 (캐시 디렉토리 생성 실패는 `ThumbnailError` → 썸네일 `✗` 표시로 처리) |
| §8 테스트 범위 | 각 태스크 |
| §9 pyproject, PyInstaller onedir | 1, 13 |
