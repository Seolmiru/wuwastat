# 배포 가이드 (배포하는 사람용)

지인들에게 프로그램을 나눠 주는 절차입니다. 지인에게 보낼 설치·사용 안내는 [INSTALL.md](INSTALL.md)입니다.

## 배포 방식 요약

- **포터블 ZIP**: python.org 공식 임베디드 Python + 패키지 + 앱을 한 폴더에 담은 ZIP.
  받는 사람은 압축을 풀고 `wuwastat.bat`만 실행하면 됩니다.
- 이 프로젝트가 만든 `.exe`가 없어서 **코드 서명이 필요 없습니다.** 실행되는 `pythonw.exe`는 Python 재단 서명 파일입니다.
- **GitHub Releases**에 올리면 지인에게는 링크 하나만 보내면 됩니다:
  https://github.com/Seolmiru/wuwastat/releases

---

## 1. 처음 한 번만: GitHub 설정 확인

1. 저장소 **Settings > Actions > General**
   - **Actions permissions**: 'Allow all actions and reusable workflows' (또는 GitHub 공식 액션 허용)
   - **Workflow permissions**: **'Read and write permissions'** 선택 → Save
     (릴리스 워크플로가 Releases에 ZIP을 올리려면 쓰기 권한이 필요합니다)
2. 저장소가 **Public**이어야 지인들이 로그인 없이 Releases에서 받을 수 있습니다.

---

## 2. 새 버전 내기 (자동)

1. 배포할 코드를 `main` 브랜치에 합칩니다. (기능 브랜치에서 작업했다면 GitHub에서 Pull Request로 합치기)
2. `wuwastat/__init__.py`의 버전을 올리고 커밋·푸시합니다.
   ```python
   __version__ = "0.2.0"
   ```
3. 같은 이름의 **태그**를 만들어 올립니다.
   ```
   git checkout main
   git pull
   git tag v0.2.0
   git push origin v0.2.0
   ```
4. GitHub의 **Actions** 탭에서 `release` 워크플로 진행을 확인합니다 (5~10분).
   - 태그와 `__version__`이 다르면 실패합니다 → 버전을 맞추고 태그를 다시 만드세요.
   - 테스트 실패 시 ZIP을 만들지 않습니다.
5. 끝나면 **Releases**에 `wuwastat-0.2.0-win64.zip`이 올라갑니다.
6. 지인들에게 Releases 링크와 [INSTALL.md](INSTALL.md) 링크를 보냅니다.

> 태그를 잘못 만들었을 때:
> ```
> git tag -d v0.2.0
> git push origin :refs/tags/v0.2.0
> ```
> GitHub Releases 페이지에서 해당 릴리스도 지운 뒤 다시 만드세요.

---

## 3. 직접 만들어서 보내기 (GitHub 없이)

```
.venv\Scripts\python tools\build_portable.py
```

- 결과: `build\wuwastat-<버전>-win64.zip` (약 80MB)
- 카카오톡, 구글 드라이브, USB 등으로 직접 보내도 됩니다.
- 빌드는 python.org에서 임베디드 Python을 받아 **Python 재단 서명을 확인**한 뒤 진행합니다. 서명이 이상하면 멈춥니다.

---

## 4. 배포 전에 확인할 것

- [ ] 테스트 통과: `.venv\Scripts\python -m pytest tests`
- [ ] 캐릭터 데이터가 최신인가 (새 캐릭터가 나왔다면)
  ```
  .venv\Scripts\python -m wuwastat.scraper --export-slim --download-images
  ```
  → `data/characters.json`(숫자와 이름만)과 `assets/`(초상화)가 갱신됩니다. 바뀐 파일을 커밋하세요.
  (`data/characters.local.json`은 공략 설명 글이 들어 있어 저장소에 올리지 않습니다)
- [ ] 직접 만든 ZIP을 **새 폴더에 풀어서** `wuwastat.bat`이 실행되는지 한 번 확인
- [ ] 버전 번호를 올렸는가

---

## 5. 지인에게 보낼 메시지 예시

```
명조 육성 등급 계산기 공유해요!
캐릭터 스탯이랑 에코를 읽어서 등급 매기고, 어떤 에코부터 바꿔야 하는지 알려줘요.

다운로드: https://github.com/Seolmiru/wuwastat/releases
설치·사용법: https://github.com/Seolmiru/wuwastat/blob/main/docs/INSTALL.md

1) 최신 버전의 wuwastat-...-win64.zip 받기
2) ZIP 우클릭 > 속성 > '차단 해제' 체크
3) C:\wuwastat 같은 곳에 압축 풀고 wuwastat.bat 더블클릭
```

---

## 6. 지인에게 문제를 전달받았을 때

받아 두면 좋은 것:
- 어떤 화면에서 문제가 생겼는지 **스크린샷** (UID 등 개인정보는 가리고)
- 프로그램 아래쪽·상태 줄에 뜬 **메시지**
- 모니터 해상도와 게임 화면 모드 (창 모드 / 테두리 없는 창 / 전체 화면)

인식 문제는 그 스크린샷을 `tests/fixtures/`에 넣고 테스트로 만들어 고치면 다시 생기지 않습니다.
