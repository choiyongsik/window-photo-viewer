# Photo Culling Viewer — 설계 스펙

- 날짜: 2026-08-27
- 상태: 설계 승인, 구현 계획 작성 전
- 대상: Windows 11, Python 3.12+, PySide6

## 1. 목적과 범위

취미 사진가(개발자)가 촬영 직후 폴더 단위로 사진을 빠르게 훑어보며 별점을 매기는 **컬링 전용 뷰어**.
결과(별점·색 라벨)는 JPEG 파일 내장 XMP에 기록되어 Lightroom Classic이 임포트 시 그대로 읽는다.

### 1.1 요구사항 요약

| 항목 | 결정 |
|---|---|
| 핵심 시나리오 | 촬영 후 셀렉(컬링) |
| 파일 포맷 | JPEG (`.jpg`, `.jpeg`) + 짧은 영상 (`.mp4`, `.mov`) |
| 규모 | 디렉토리당 최대 200~300개. 사용자는 폴더를 잘게 쪼개 관리 |
| 출구 | 별점/색 라벨을 파일에 기록 → Lightroom Classic |
| 영상 처리 | 썸네일 표시 + 앱 내 재생 |
| 스택 | Python + PySide6 (Qt Widgets) |

### 1.2 1차 범위 밖 (YAGNI)

RAW 지원, 파일 삭제/이동, 2장 비교 뷰, 히스토그램, 편집 기능, 다중 폴더 카탈로그/DB, 사이드카 `.xmp` 생성.

## 2. 접근법 선택

| 접근법 | 판단 |
|---|---|
| **A. PySide6 데스크톱 앱 (채택)** | `QGraphicsView`로 줌/패닝, `QMediaPlayer`로 영상, 키보드 완전 제어. 단일 프로세스, PyInstaller 배포. 300장 규모에 성능 여유 충분 |
| B. Python 백엔드 + 웹 UI | 이미지를 HTTP 서빙해야 하고 대형 JPEG 줌 성능·영상 코덱·단축키가 브라우저에 종속. 두 계층 복잡도가 앱 규모에 비해 과함 |
| C. Electron/Tauri 등 | 스택 선호(Python)와 불일치 |

## 3. 아키텍처

```
window-photo-viewer/
├── core/                 # Qt 의존 없음 — pytest 단위 테스트 대상
│   ├── models.py         # MediaItem, MediaKind, Label 열거형
│   ├── scanner.py        # 폴더 → MediaItem 목록 (확장자 필터, 파일명 자연 정렬, 숨김 제외)
│   ├── metadata.py       # xmp:Rating / xmp:Label 읽기·쓰기 (pyexiv2), EXIF 방향·촬영정보 요약
│   ├── thumbnails.py     # Pillow draft 모드 축소, 영상은 ffmpeg 프레임 추출, 디스크 캐시
│   └── filters.py        # 별점/라벨/reject 필터 → 인덱스 목록
├── ui/                   # PySide6
│   ├── main_window.py    # 레이아웃, 단축키 바인딩, 모드 전이, 상태 헤더
│   ├── loupe_view.py     # 단일 이미지 (QGraphicsView) — fit/100% 토글, 드래그 패닌
│   ├── filmstrip.py      # 하단 썸네일 스트립 + 별점/라벨/영상 아이콘 오버레이
│   ├── grid_view.py      # 전체 썸네일 그리드 (개요 모드)
│   ├── video_view.py     # QMediaPlayer + QVideoWidget
│   └── workers.py        # QThreadPool용 QRunnable: 썸네일 생성, 풀사이즈 프리로드, 메타 쓰기
├── app.py                # 진입점
├── tests/
│   ├── core/             # pytest
│   └── ui/               # pytest-qt (상태 전이만)
└── pyproject.toml
```

원칙: `core/`는 Qt를 import하지 않는다. UI는 `core`를 호출하는 얇은 층으로 유지한다.

### 3.1 데이터 모델

```python
class MediaKind(Enum): IMAGE, VIDEO

class Label(Enum): NONE="", RED="Red", YELLOW="Yellow", GREEN="Green", BLUE="Blue"

@dataclass
class MediaItem:
    path: Path
    kind: MediaKind
    rating: int            # -1(reject), 0~5
    label: Label
    mtime: float
    size: int
    exif: ExifSummary | None   # 셔터, 조리개, ISO, 초점거리, 촬영시각, 방향
    write_error: str | None    # 메타 쓰기 실패 메시지, 없으면 None
```

### 3.2 데이터 흐름

1. `Ctrl+O` 또는 인자로 폴더 지정 → `scanner.scan(folder)` → `list[MediaItem]` (별점은 이 시점에 파일에서 읽음)
2. `workers`가 썸네일을 백그라운드 생성. 우선순위: 현재 화면에 보이는 항목 → 나머지 순차
3. 사용자가 이동하면 현재 ±2장을 풀사이즈 `QImage`로 프리로드 (메모리 LRU, 최대 6장)
4. 별점/라벨 키 입력 → 모델 즉시 갱신(UI 반영) → 백그라운드 `metadata.write()` → 실패 시 `write_error` 세팅, 상태바 경고

## 4. UI와 단축키

### 4.1 화면 구성

```
┌────────────────────────────────────────────────────────┐
│ 폴더경로  [★★★☆☆] [Label]  DSC_0123.JPG  42/280  1/250 f2.8 ISO400 │  상태 헤더
├────────────────────────────────────────────────────────┤
│                                                        │
│                 Loupe (단일 이미지 / 영상)               │  기본 모드
│                                                        │
├────────────────────────────────────────────────────────┤
│ ▢ ▢ ▢ [▣] ▢ ▢ ▢ ▢ ▢ ▢ ▢ ▢ ▢ ▢ ▢ ▢ ▢ ▢ ▢ ▢ ▢ ▢ ▢ ▢ ▢ ▢  │  필름스트립
└────────────────────────────────────────────────────────┘
```

- 두 모드: **Loupe**(기본) / **Grid**. 그리드는 원본 비율 유지, 셀 하단에 별점·라벨·영상 아이콘·파일명.
- 영상 항목은 Loupe에서 `QVideoWidget`으로 교체 표시.

### 4.2 단축키

| 키 | 동작 |
|---|---|
| `→` `←` / `Space` `Backspace` | 다음/이전 (영상 재생 중 `Space`는 재생/일시정지) |
| `Home` `End` | 첫/마지막 |
| `Z` 또는 좌클릭 | fit ↔ 100% 토글. 100%일 때 클릭 지점 중심, 드래그로 패닝 |
| `1`~`5` | 별점 설정. 현재와 같은 값 재입력 시 0으로 해제 |
| `0` | 별점 해제 |
| `X` | Reject (`rating = -1`). 재입력 시 0 |
| `6` `7` `8` `9` | 라벨 Red / Yellow / Green / Blue (LR 관례). 재입력 시 해제 |
| `G` / `E` | Grid ↔ Loupe |
| `F` / `F11` | 전체화면 토글 |
| `Ctrl+O` | 폴더 열기 (마지막 폴더 기억, `QSettings`) |
| `Alt+1`~`Alt+5` | 필터: N성 이상만 표시 |
| `Alt+X` | 필터: reject만 표시 |
| `Alt+0` | 필터 해제 |

### 4.3 동작 규칙

- 별점 입력 후 **자동 전진하지 않는다** (기본). 설정에서 "별점 후 자동 다음"을 켤 수 있다.
- 필터 적용 중 이동·Home/End·필름스트립은 필터된 목록 안에서만 동작. 현재 항목이 필터에서 빠지면 다음 항목으로 이동.
- 폴더 열기 시 마지막으로 보던 항목 위치는 기억하지 않는다 (항상 첫 항목).

## 5. 메타데이터 기록 규칙 (Lightroom 호환)

| 항목 | 결정 |
|---|---|
| 기록 위치 | JPEG **내장 XMP 패킷**. 사이드카 `.xmp`는 만들지 않는다 (LR은 JPEG 사이드카를 읽지 않음) |
| 별점 | `Xmp.xmp.Rating` — 정수 문자열 `"-1"`, `"0"`~`"5"`. 0이면 태그 삭제 |
| 라벨 | `Xmp.xmp.Label` — `"Red"`, `"Yellow"`, `"Green"`, `"Blue"`. 해제 시 태그 삭제 |
| 라이브러리 | `pyexiv2` (exiv2 번들, Windows wheel 제공). XMP 패킷만 갱신하므로 픽셀·EXIF 무손상 |
| 파일 시각 | `mtime`은 갱신되게 둔다 (LR이 메타데이터 변경 감지에 사용) |
| 안전한 쓰기 | 임시 파일에 복사 → 메타 기록 → `os.replace`로 원자 교체 |
| 읽기 | 기존 `Xmp.xmp.Rating`/`Label`을 로드 시 표시. LR에서 이미 별점 매긴 폴더도 일관 |
| 영상 | `.mp4`/`.mov`에도 같은 방식으로 기록 시도 (exiv2가 지원). LR의 영상 XMP 반영은 제한적임을 사용자 문서에 명시 |

### 5.1 Reject의 의미

XMP 스펙상 `Rating=-1`은 "rejected"이지만 **Lightroom Classic은 Pick/Reject 플래그를 XMP로 읽거나 쓰지 않으며** `-1`을 별점 0으로 취급한다. 따라서:

- 뷰어 안에서 reject는 회색 X로 표시, `Alt+X`로 모아 볼 수 있다.
- LR로 넘어가면 "별점 없음"과 구별되지 않는다. 실제 삭제는 사용자가 탐색기 등에서 직접 한다 (1차 범위 밖).

### 5.2 사용자 문서에 명시할 사항

이미 LR 카탈로그에 임포트된 파일에 별점을 바꾼 경우 LR에서 `Metadata → Read Metadata from File`을 실행해야 반영된다. 임포트 전 셀렉(주 워크플로우)은 자동 반영.

## 6. 성능

목표: 300장 폴더를 열고 **1초 내 첫 화면**, 좌우 이동 시 **체감 지연 없음**.

| 항목 | 설계 |
|---|---|
| 썸네일 | 긴 변 256px. Pillow `Image.draft("RGB", (512, 512))`로 DCT 스케일 디코딩 후 리사이즈. EXIF 방향 적용. 24MP JPEG 장당 약 20ms |
| 썸네일 워커 | `QThreadPool` 4스레드. 보이는 항목 우선 큐 |
| 디스크 캐시 | `%LOCALAPPDATA%\WindowPhotoViewer\thumbs\{sha1(path + mtime + size)}.jpg`. mtime/size 변경 시 자동 무효화 |
| 풀사이즈 프리로드 | 현재 ±2장을 `QImage`로 메모리 LRU. 최대 6장 (24MP × 4B ≈ 100MB/장 → 상한 약 600MB) |
| 영상 썸네일 | `imageio-ffmpeg`가 번들하는 ffmpeg 바이너리로 1초 지점(영상이 짧으면 0초) 프레임 추출. 별도 설치 불필요 |
| 메타 쓰기 | 백그라운드 워커 1스레드 직렬 처리 (같은 파일 동시 쓰기 방지) |

## 7. 에러 처리

| 상황 | 동작 |
|---|---|
| 손상/디코딩 불가 파일 | 깨진 이미지 플레이스홀더 + 파일명. 이동은 정상 진행 |
| 폴더 접근 실패 | 다이얼로그 안내, 이전 상태 유지 |
| 열람 중 파일 삭제/이동됨 | 해당 항목 제거, 상태바 안내 |
| 메타 쓰기 실패 (읽기전용·잠김·XMP 손상) | 메모리 상태 유지, 상태바 빨간 경고, 필름스트립 썸네일에 `!` 표시. 재시도는 같은 키 재입력 |
| 영상 재생 불가 (코덱) | 썸네일 위에 "재생 불가" 표시, 별점 기록은 가능 |
| 썸네일 캐시 디렉토리 생성 실패 | 메모리 캐시만 사용, 경고 1회 |

## 8. 테스트

### 8.1 `core/` — pytest

- `scanner`: 확장자 대소문자, 자연 정렬(`IMG_2` < `IMG_10`), 숨김/시스템 파일 제외, 빈 폴더
- `metadata`: 픽스처 JPEG에 Rating/Label 쓰고 읽기 라운드트립. **픽셀 스캔 세그먼트 바이트 불변** 검증. 0/해제 시 태그 삭제 확인. 읽기전용 파일에서 예외 → `write_error`. exiftool이 PATH에 있으면 교차 검증(없으면 skip)
- `thumbnails`: 캐시 미스 → 생성, 히트 → 재생성 안 함, mtime 변경 → 무효화, EXIF 방향 6/8 회전 결과 크기 검증
- `filters`: 별점 이상 필터, reject 필터, 해제

### 8.2 `ui/` — pytest-qt

- 단축키 → 모델 변경 → `metadata.write` 호출 (mock) 검증: `1`~`5`, `0`, `X`, `6`~`9`, 토글 해제 동작
- 이동 키가 필터된 목록 경계를 지키는지
- `G`/`E` 모드 전이
- 렌더링·영상 재생은 수동 검증

## 9. 배포와 의존성

- `pyproject.toml`, Python 3.12+
- 런타임 의존: `PySide6`, `pyexiv2`, `Pillow`, `imageio-ffmpeg`
- 개발 의존: `pytest`, `pytest-qt`
- 배포: PyInstaller 단일 폴더(`--onedir`) 빌드. ffmpeg 바이너리와 exiv2 DLL 포함 확인
