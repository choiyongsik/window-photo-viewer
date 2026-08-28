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

### 10.4 정렬·정확히-N점 필터·새로고침 (2026-08-27 추가)

**정렬 모드** — `core/sorting.py`의 `SortMode`(Qt 미사용): `NAME_ASC`(파일명 자연 정렬 ↑, 기본값), `CAPTURE_DESC`(촬영일시 ↓ — EXIF `DateTimeOriginal`, 파싱 실패나 값 없음(영상 포함)은 `mtime`로 대체), `MTIME_DESC`(수정시각 ↓). 동률은 항상 파일명 자연 정렬 오름차순으로 2차 정렬한다. 키 `S`로 세 모드를 순환하며, 메뉴 보기 → "정렬" 서브메뉴(배타적 체크 액션 3개)로도 선택할 수 있다. 선택된 모드는 `QSettings`의 `sort_mode` 키(`"name_asc"`/`"capture_desc"`/`"mtime_desc"`)에 저장되어 다음 실행에도 유지된다. `MainWindow`는 스캔 결과를 정렬 전 원본(`_unsorted_items`)으로 보관해 두었다가 정렬 모드가 바뀔 때마다(`_resort`) 그 원본에서 다시 정렬하고, 현재 보던 항목을 경로 기준으로 이어서 선택한다. `NAME_ASC`가 아닐 때만 헤더에 `sort: {설명}`을 표시한다.

**정확히 N점 필터** — `core/filters.py`의 `Filter`에 `exact_rating: int | None` 필드 추가. `matches()` 우선순위: `rejected_only` → `exact_rating`(정확히 일치) → `min_rating`(이상). `Alt+Shift+1`~`Alt+Shift+5`로 지정하며, 기존 `Alt+1`~`Alt+5`(N성 이상)와는 구분된다. Shift가 눌린 상태에서는 US 배열 기준으로 Qt가 `Key_1`~`Key_5` 대신 `Key_Exclam` 등 기호 키를 보고할 수 있어, `MainWindow._digit_from_event`가 `event.key()`(오프스크린 플랫폼이 실제로 보내는 값)를 먼저 확인하고 없으면 `event.nativeVirtualKey()`(VK_1~VK_5)로 한 번 더 확인한다. 헤더에는 `filter: ★N`(이상 필터는 `★N+`)로 표시된다.

**새로고침 (F5) 과 폴더 자동 감시** — `F5`(메뉴 파일 → "새로고침")는 `refresh_folder()`를 호출해 현재 선택 항목의 경로를 기억해 두고(`_restore_path`) 같은 폴더를 다시 스캔한다. 스캔 결과가 돌아왔을 때(`_on_scan_finished`) 그 폴더가 이미 열려 있던 폴더라면: 새 결과의 경로 집합이 현재 모델의 경로 집합과 완전히 같으면 — 화면 갱신 없이 상태 표시줄에 "변경 없음"(2초)만 띄운다. 이는 뷰어 자신이 별점/라벨을 기록할 때 만드는 tmp+rename이 폴더 변경 이벤트를 유발하는 것을 무해하게 흡수하기 위함이다. 경로 집합이 달라졌으면 목록을 갱신하고, `_restore_path`가 새 목록에 있으면 그 항목을 다시 선택한 뒤(없으면 첫 항목), 상태 표시줄에 `"{개수}개 항목 (새로고침)"`을 띄운다.

폴더가 열릴 때마다(`load_items`) `QFileSystemWatcher`(`MainWindow._watcher`)가 감시 경로를 그 폴더 하나로 교체한다. `directoryChanged` 시그널이 오면 700ms 단발 타이머(`_watch_timer`)를 (재)시작하고, 만료되면(`_on_watch_timeout`) `refresh_folder()`를 호출한다 — 여러 변경이 짧은 시간에 몰려도 한 번만 새로고침하기 위한 디바운스. 뷰어 자신의 XMP 쓰기가 이 감시를 다시 촉발하는 것을 막기 위해, 별점/라벨 변경을 시작할 때(`_apply_change`)와 그 쓰기가 끝났을 때(`_on_write_finished`) 모두 `_suppress_watch_until = time.monotonic() + 2.0`을 설정하고, `_on_watch_timeout`은 그 시각 이전이면 새로고침을 건너뛴다 — 위에서 설명한 경로-집합 비교가 이를 보강하는 2차 방어선이다. 창을 닫을 때(`closeEvent`) 타이머를 멈추고 감시 경로를 비운다.

### 10.5 폴더 트리 패널 (2026-08-27 변경)

§10.2의 형제 목록을 대체한다: `FolderPanel`은 이제 QListWidget이 아니라 **루트 폴더 기준의 지연 로딩 QTreeWidget**이다 (단일 컬럼, 헤더 숨김, `NoFocus` — 방향키는 여전히 MainWindow가 처리하며 트리는 절대 키보드 포커스를 갖지 않는다).

**트리 구조와 지연 로딩** — `set_root(root)`가 루트 노드를 만들어 펼친 상태로 보여준다. 펼쳐지지 않은 모든 디렉터리 노드는 자리표시자(placeholder) 자식 하나를 갖고 있다가, `itemExpanded`가 발생하면 그때 `list_child_folders(path)`(자연 정렬, `.`으로 시작하거나 Windows 숨김 속성인 항목 제외, `OSError` → 빈 목록)로 실제 자식을 채운다 — 한 번 로드된 노드는 다시 펼쳐도 재조회하지 않는다. 노드 텍스트는 처음엔 `folder.name`이고, `FolderCountJob`(`QRunnable`, 2-스레드 `QThreadPool`)이 그 노드가 로드될 때 새로 나타난 자식들의 이미지·영상 개수를 백그라운드에서 세어 `counts_ready` 시그널로 돌려주면 `name   (23장 · 1영상)` 형태로 갱신된다 (영상이 0이면 영상 부분 생략). 결과가 돌아왔을 때 그 경로가 더 이상 트리에 없으면(루트가 바뀌는 등) 조용히 무시한다. 루트 노드 자체는 개수를 세지 않고 이름만 보여주며, 전체 경로는 툴팁과 트리 위 헤더 레이블(elided)로 노출한다.

**공개 인터페이스** — `set_root`/`root`/`set_folder`/`current_folder`/`visible_folders`/`next_folder`/`prev_folder`/`contains`/`folder_activated`. `set_folder(folder)`는 `folder`가 루트 밖이면 아무 것도 하지 않는다(루트를 바꿀지는 MainWindow가 결정한다) — 루트 안이면 조상 노드들을 필요한 만큼 펼치며(로드하며) 그 폴더를 굵게 표시하고 스크롤한다. `visible_folders()`는 현재 펼쳐진 노드들을 DFS 순서(루트 먼저)로 나열한 것이고, `next_folder`/`prev_folder`는 그 리스트에서 현재 위치의 앞뒤다. `contains(folder)`는 순수 경로 비교(`Path.relative_to`)로, 디스크 접근이 없다.

**루트 규칙 (`MainWindow`)** — `QSettings`의 `root_folder` 키에 저장되는 `root_folder` 프로퍼티가 있다. `open_folder(folder)`는 `root_folder is None`이거나 `folder`가 현재 루트 밖(`not folder_panel.contains(folder)`)이면 `folder`를 새 루트로 삼은 뒤 스캔한다 — 그 외(루트 안)에는 루트를 바꾸지 않는다. 즉 `Ctrl+O`로 트리 안의 폴더를 열면 트리는 그대로이고, 트리 밖의 폴더를 열면 그 폴더가 새 루트가 된다. 시작 시 인자로 준 폴더나 `last_folder`도 동일한 규칙을 따른다(`MainWindow.__init__`이 저장된 `root_folder`가 아직 존재하면 `folder_panel.set_root()`로 복원하고, 사라졌으면 설정을 지운 뒤 이어서 `open_folder`가 규칙대로 루트를 정한다).

**`Alt+↑` / "현재 폴더를 루트로"** — 메뉴 파일 → "루트 한 단계 위로"(`Alt+↑`, `MainWindow.go_to_parent_root`)는 루트가 최상위(`root.parent == root`)가 아니면 `root_folder`를 그 부모로 바꿔 트리를 위로 한 단계 확장한다 — 현재 열려 있는 폴더는 그대로이고 트리에서도 계속 강조 표시된다. 메뉴 파일 → "현재 폴더를 루트로"(`MainWindow.set_root_to_current_folder`)는 지금 열린 폴더를 새 루트로 지정한다. 두 동작 모두 루트를 바꾼 뒤 `folder_panel.set_folder(self.folder)`를 다시 호출해 현재 폴더의 강조 표시를 새 루트 기준으로 복원한다.

**패널 폭 조절·저장** — `FolderPanel`은 더 이상 고정 폭이 아니라 `setMinimumWidth(180)`만 가지며, `QSplitter`의 경계를 드래그해 넓힐 수 있다. 창을 닫을 때(`closeEvent`) `self.splitter.saveState()`를 `QSettings`의 `splitter_state` 키에 저장하고, `_build_ui`에서 그 값이 `QByteArray`이면 `restoreState()`로 복원한다. `Ctrl+B`(폴더 패널 표시/숨김)와 그 영속 동작은 §10.2와 동일하다.

`PgUp`/`PgDn`, 클릭으로 폴더 열기(`folder_activated` → `MainWindow.open_folder`) 동작은 §10.2와 동일한 원리이지만 이제 형제 목록이 아니라 트리의 현재 보이는 순서를 기준으로 한다.

### 10.6 ★ 별점 있는 사진 모아보기 + 헤더 기반 XMP 읽기 (2026-08-28 추가)

**동기** — 별점은 DB 없이 JPEG 안의 XMP에만 있으므로, 루트 아래 전체에서 별점 사진을 모으려면 파일마다 XMP를 읽어야 한다. 기존 `_read_jpeg`는 `read_bytes()`로 파일 전체를 pyexiv2에 넘겼다(측정: 22MB JPEG 기준 ~20ms/장 → 1만 장에 수 분, HDD면 라이브러리 전체 용량을 읽음). JPEG의 XMP는 SOS 마커 이전 APP1 세그먼트에 있으므로 헤더만 읽으면 된다(측정 ~0.1–0.2ms/장, 200배).

**`core/metadata.py`** — `_read_jpeg`는 `.jpg/.jpeg`이면 `_read_jpeg_xmp_packet`(SOI 확인 → 세그먼트 순회 → APP1 중 `http://ns.adobe.com/xap/1.0/\0` 서명인 페이로드 반환, SOS를 만나면 None)으로 패킷을 꺼내 `_parse_xmp_packet`(사이드카 파서를 분리한 것 — attr/element 양쪽 지원, defusedxml)으로 파싱한다. 패킷이 없으면 곧바로 `(0, NONE)`이며 pyexiv2로 재확인하지 않는다(전체 읽기를 피하는 것이 목적). 헤더 파싱 예외나 비-JPEG(PNG)는 `_read_with_pyexiv2`(기존 전체 읽기)로 폴백. 쓰기 경로는 변경 없음.

**`core/scanner.py`** — `is_hidden(entry)`(폴더 패널의 `_is_hidden`을 이동)와 `iter_media_folders(root)`(`os.walk`, 숨김 디렉터리 in-place 가지치기, 형제는 자연 정렬, OSError 무시). **`core/collection.py`** — `collect_rated(root, is_cancelled, on_progress) -> list[MediaItem] | None`: 폴더마다 `scanner.scan` → 각 파일 `read_rating_label` → `rating >= 1`만 채택(Reject(-1) 제외), 채택된 항목만 EXIF를 읽는다. 폴더 경계마다 `on_progress(CollectProgress(folders, files, rated))`를 호출하고 `is_cancelled()`가 참이면 None을 반환한다.

**`ui/workers.py`** — `RatedCollectJob(root, signals)`: `cancel()`(threading.Event), 진행 emit은 0.25초 스로틀(첫 폴더는 항상). 시그널 `collect_progress(job, progress)` / `collect_finished(job, items|None)`은 **잡 객체 자체**를 넘긴다 — 수신 측은 `job is self._collect_job` 신원 비교로 취소된 옛 잡의 None과 현재 잡의 실패를 구분한다(루트 비교로는 같은 루트로 재수집한 경우를 구분할 수 없다).

**`ui/folder_panel.py`** — `set_root`가 루트 노드 위(top-level 0)에 가상 노드 `"★ 별점 있는 사진"`(`_ROLE_VIRTUAL="rated"`, `_ROLE_PATH=None`, 자식 없음)을 추가한다. 클릭 시 `rated_collection_activated` 시그널(이미 활성이어도 emit — 재클릭 = 재수집). `set_collection_active(on)`은 현재 폴더 강조를 지우고 가상 노드를 굵게/현재로 표시하며 `_current=None`으로 둔다. `set_rated_count(n)`은 `"★ 별점 있는 사진   (n장)"`. `visible_folders`/`_ensure_item`은 `_root_item`에서만 시작하므로 `PgUp`/`PgDn`은 가상 노드를 건너뛴다.

**`ui/main_window.py`** — 상태 `_collection_root`(모아보기 중인 루트, 이때 `self.folder is None`)와 `_collect_job`, 전용 1-스레드 `collect_pool`(scan_pool은 FIFO 1스레드라 거기 넣으면 다음 폴더 열기가 막힌다). `show_rated_collection()`(가상 노드 클릭, `Ctrl+Shift+R`, 메뉴 보기): 루트 없으면 상태바 안내 후 종료; 이전 수집 취소, `_loading_folder=None`(늦게 오는 폴더 스캔 무시), 잡 시작. `_on_collect_finished`: 신원이 다르면 무시, `items is None`이면 실패 안내, 아니면 `load_items(items, None)` → 패널 가상 노드 강조·개수 → `_restore_path` 복원. `load_items(…, folder)`에 실제 폴더가 오면 모아보기 상태를 해제(취소·`_collection_root=None`·패널 강조 해제)하고, `open_folder`는 루트 규칙을 적용하기 **전에** 수집을 취소한다(재루팅이 새 루트 재수집을 잠깐 시작시키지 않도록). `refresh_folder`는 모아보기 중이면 현재 경로를 기억해 재수집. `root_folder` setter는 모아보기 중이고 루트가 실제로 바뀌면 재수집(`Alt+↑`가 범위를 넓힌다). 헤더는 `★ 별점 있는 사진 (루트)` + `상위폴더/파일명`, 빈 결과는 `NO_RATED_TEXT`. `closeEvent`는 수집을 취소하고 `collect_pool`도 2초 드레인 대상에 포함한다. 모아보기 중엔 `QFileSystemWatcher` 감시 경로가 없다(`load_items(…, None)`이 비운다).

**결정/비범위** — 범위는 루트 안, 포함 기준은 `rating >= 1`, 스트리밍 없이 완료 후 일괄 로드(정렬 정확성, `load_items` 재사용), 인덱스 캐시(sqlite)는 만들지 않음 — 헤더 읽기로 충분하며 HDD에서 느리다고 판명될 때 추가한다.

### 10.7 별점 캐시 (2026-08-28 추가)

**동기** — §10.6의 헤더 읽기로도 파일마다 `open` 1회는 남고, HDD에서는 그것이 헤드 이동(~10ms) → 1만 장에 1~2분이다. SSD에서도 3~10초. 한 번 읽은 답을 기억하면 재수집은 파일을 열지 않고 `stat` 비교만으로 끝난다(장당 ~0.05ms).

**`core/rating_cache.py` — `RatingCache(file)`** — `str(path) → [mtime, size, rating, label]` 딕셔너리를 JSON(`{"version": 1, "entries": {...}}`)으로 `%LOCALAPPDATA%\WindowPhotoViewer\ratings.json`에 둔다(썸네일 캐시 옆; `MainWindow`는 `thumb_cache.cache_dir.parent / "ratings.json"`을 기본값으로 쓴다). `lookup(path, mtime, size)`는 mtime·size가 **모두** 같을 때만 히트. **별점 없는 파일도 0으로 저장**한다 — 다수인 무별점 파일을 다시 열지 않는 것이 이득의 본체다. `store()`/`lookup()`/`retain_under()`는 `threading.Lock`으로 보호(수집 잡은 워커 스레드, 뷰어 쓰기 기록은 쓰기 잡 스레드에서 들어온다). `save()`는 dirty일 때만 tmp+`os.replace`로 원자적 저장, 실패는 무시(캐시는 가속기일 뿐). 손상된 파일은 빈 캐시로 취급. `retain_under(root, seen)`은 완주한 수집이 보지 못한 root 아래 항목(삭제·이동)을 지운다.

**연결** — `metadata.read_rating_label_cached(path, kind, mtime, size, cache, refresh)`가 유일한 진입점: `refresh`가 아니고 히트면 파일을 열지 않고, 미스/refresh면 읽어서 `store`. `populate(item, cache, refresh)`와 `collect_rated(..., cache, refresh)`가 이를 쓴다(`collect_rated`는 끝에 `retain_under` + `save`). `ScanJob(folder, signals, cache, refresh)`, `RatedCollectJob(root, signals, cache, refresh)`, `MetadataWriteJob(item, signals, cache)`(쓰기 성공 직후 파일을 `stat`해 새 mtime/size로 `store` — 뷰어 자신의 쓰기가 다음에 미스가 되지 않도록). `MainWindow.closeEvent`는 풀 드레인 뒤 `save()`.

**F5 = 전체 재읽기** — `refresh_folder()`는 `refresh = not _refresh_from_watcher`로 `open_folder(folder, refresh=…)` / `show_rated_collection(refresh=…)`를 부른다. 명시적 F5는 캐시를 무시하고 모두 다시 읽어 캐시를 고친다(mtime을 보존하며 XMP를 바꾸는 `exiftool -P` 류의 유일한 탈출구). 폴더 감시가 촉발한 새로고침은 캐시를 그대로 쓴다 — 디스크에서 바뀐 파일은 mtime/size가 달라 스스로 미스가 난다.

**비범위** — 앱 시작 시 캐시만으로 `★ (N장)` 개수를 미리 표시하는 것은 하지 않았다(아직 안 본 파일이 있으면 틀린 숫자가 된다). 캐시는 루트 밖의 죽은 항목을 정리하지 않는다(1만 항목 ≈ 1~2MB, 무해).
