# Photo Culling Viewer

촬영 직후 JPEG/PNG 폴더를 키보드로 훑으며 별점을 매기는 Windows 뷰어.
지원 포맷: `.jpg` `.jpeg` `.png` 이미지, `.mp4` `.mov` 영상.
별점·색 라벨은 **이미지 내장 XMP**(`xmp:Rating`, `xmp:Label`)에 기록되어 Lightroom Classic 임포트 시 그대로 읽힌다.

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

- JPEG/PNG: XMP 패킷(PNG는 iTXt 청크)만 교체한다. 픽셀 데이터와 EXIF는 그대로. 임시 파일에 쓴 뒤 원자적으로 교체하므로 중간에 꺼져도 원본이 깨지지 않는다. 파일 수정 시각은 갱신된다.
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
