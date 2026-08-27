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
| 파일 포맷 | JPEG (`.jpg`, `.jpeg`) + PNG (`.png`, 2026-08-27 추가 — 내장 XMP 동일 처리) + 짧은 영상 (`.mp4`, `.mov`) |
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
│   ├── main_window.py      # 레이아웃, 단축키 바인딩, 모드 전이, 상태 헤더
│   ├── loupe_view.py       # 단일 이미지 (QGraphicsView) — fit/100% 토글, 드래그 패닝
│   ├── media_list_model.py # QAbstractListModel: MediaItem 목록 + 필터 + 썸네일 픽스맵
│   ├── thumb_delegate.py   # 썸네일 셀 페인팅 (별점/라벨/영상/reject/! 오버레이)
│   ├── thumb_views.py      # Filmstrip(가로 스트립) / GridView(그리드) — 같은 모델·델리게이트 공유
│   ├── video_view.py       # QMediaPlayer + QVideoWidget
│   ├── image_cache.py      # 풀사이즈 QImage LRU (최대 6장)
│   └── workers.py          # QThreadPool용 QRunnable: 스캔, 썸네일, 풀사이즈 프리로드, 메타 쓰기
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
| 안전한 쓰기 | 파일 바이트를 읽어 `pyexiv2.ImageData`로 메모리에서 XMP 갱신 → 임시 파일(`원본명.tmp`)에 기록 → `os.replace`로 원자 교체. 바이트 기반이므로 한글 경로 인코딩 문제 없음 |
| 읽기 | 기존 `Xmp.xmp.Rating`/`Label`을 로드 시 표시. LR에서 이미 별점 매긴 폴더도 일관. EXIF 촬영정보는 Pillow `getexif()`로 읽음 |
| 영상 | exiv2는 MP4/MOV **쓰기를 지원하지 않음**. 영상은 같은 폴더의 사이드카 `영상명.xmp`(확장자 교체, 예 `clip.mp4` → `clip.xmp`)에 표준 XMP 패킷을 직접 기록·읽음. Lightroom Classic은 영상 사이드카를 읽지 않으므로 영상 별점은 뷰어 내부용(및 Bridge/digiKam 등 호환)임을 사용자 문서에 명시 |

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

## 10. 추가 (2026-08-27): 영상 자동 재생, 폴더 패널

### 10.1 영상 자동 재생

- Loupe 모드에서 영상 항목에 도달하면 자동으로 재생을 시작한다 (`MainWindow._show_current`가 `video.load()` 후 `video.play()` 호출; Grid 모드에서는 화면에 보이지 않으므로 재생하지 않는다 — Grid에서 Loupe로 전환하며 영상 위에 서면 그때 재생 시작).
- 재생이 끝(`QMediaPlayer.MediaStatus.EndOfMedia`)에 도달하면 위치를 처음으로 되감고(`setPosition(0)`) 일시정지한다 — 첫 프레임을 보여준 채로 대기하며, `Space` 또는 클릭으로 다시 재생할 수 있다. `player.setLoops(1)`로 자동 반복은 명시적으로 끈다.
- `Space`는 기존과 동일하게 영상이 표시 중일 때 재생/일시정지를 토글한다. 영상 위를 좌클릭해도 동일하게 토글된다 (`VideoView.mousePressEvent`; 내부 `QVideoWidget`은 `WA_TransparentForMouseEvents`로 클릭을 부모에 넘긴다).

### 10.2 좌측 폴더 패널

- 창 왼쪽에 `FolderPanel`이 `QSplitter`로 본문과 나란히 배치된다. 현재 폴더의 형제 폴더들(`folder.parent`의 하위 디렉터리, 현재 폴더 포함)을 자연 정렬로 나열하고, 각 행에 이미지·영상 개수를 보여준다. 숨김 폴더(`.`으로 시작하거나 Windows 숨김 속성)는 제외된다.
- 목록의 항목을 클릭하면 그 폴더를 연다 (`folder_activated` 시그널 → `MainWindow.open_folder`). 현재 폴더를 클릭하는 것은 아무 동작도 하지 않는다.
- `PgUp` / `PgDn` 키로 형제 폴더 사이를 앞뒤로 이동한다 (`MainWindow.prev_folder` / `next_folder`). 양쪽 끝에서는 아무 동작도 하지 않는다.
- `Ctrl+B` (메뉴 보기 → "폴더 패널")로 패널을 표시/숨김 전환한다. 이 설정은 `QSettings`의 `folder_panel_visible` 키로 저장되어 다음 실행에도 유지된다 (기본값: 표시).

### 10.3 영상 컨트롤 바 (2026-08-27 추가)

영상 화면 아래 32px 컨트롤 바: `▶/❚❚` 버튼, 클릭·드래그로 탐색하는 슬라이더(드래그 중에는 재생 위치 피드백을 무시), `mm:ss / mm:ss` 시간, 🔊/🔇 음소거. 모든 컨트롤은 포커스를 갖지 않는다. 키 `,`/`.`는 영상 표시 중 5초 뒤/앞 탐색(`SEEK_STEP_MS`), 미디어 길이 범위로 클램프. 영상 화면 클릭만 재생 토글이며 컨트롤 바 빈 영역 클릭은 무시한다.
