"""포터블 배포판 만들기: 설치 없이 압축만 풀면 실행되는 ZIP.

구성
  wuwastat/
    wuwastat.bat          ← 더블클릭으로 실행
    README.txt
    python/               ← python.org 공식 임베디드 Python + 패키지
    app/                  ← 앱 코드(wuwastat), data, assets

실행: python tools/build_portable.py
결과: build/wuwastat-<버전>-win64.zip

우리가 만든 .exe가 없으므로 코드 서명이 필요 없다. 실행되는 pythonw.exe는 Python 재단이 서명한 공식 파일이다.
"""

from __future__ import annotations

import hashlib
import os
import shutil
import subprocess
import sys
import urllib.request
import zipfile
from pathlib import Path

# CI(Windows) 콘솔 기본 인코딩(cp1252)에서 한글 출력이 실패하지 않게 한다
for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        _stream.reconfigure(encoding="utf-8", errors="replace")

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from wuwastat import __version__  # noqa: E402

PY_VERSION = "3.14.10"
PY_TAG = "".join(PY_VERSION.split(".")[:2])  # "314"
EMBED_URL = f"https://www.python.org/ftp/python/{PY_VERSION}/python-{PY_VERSION}-embed-amd64.zip"

BUILD = ROOT / "build"
CACHE = BUILD / "cache"
NAME = f"wuwastat-{__version__}-win64"
STAGE = BUILD / "wuwastat"  # 짧은 경로에서 만든다 (PySide6 경로가 길어서)
ZIP_PATH = BUILD / f"{NAME}.zip"

APP_ITEMS = ["wuwastat", "data", "assets"]
APP_EXCLUDE = {"__pycache__", "characters.local.json", "devreload.py"}  # devreload: 개발용 새로고침

# 쓰지 않는 Qt 구성요소 (QtWidgets 앱이라 QML·디자이너 도구·문서가 필요 없다)
PRUNE_DIRS = ["PySide6/qml", "PySide6/include", "PySide6/typesystems", "PySide6/glue", "PySide6/examples",
              "PySide6/doc", "PySide6/metatypes", "PySide6/scripts", "PySide6/resources"]
PRUNE_FILES = ["PySide6/designer.exe", "PySide6/linguist.exe", "PySide6/assistant.exe", "PySide6/lupdate.exe",
               "PySide6/lrelease.exe", "PySide6/qmllint.exe", "PySide6/qmlformat.exe", "PySide6/qmlls.exe",
               "PySide6/balsam.exe", "PySide6/balsamui.exe", "PySide6/qsb.exe", "PySide6/svgtoqml.exe",
               "PySide6/rcc.exe", "PySide6/uic.exe"]
PRUNE_GLOBS = ["PySide6/Qt6Quick*.dll", "PySide6/Qt6Qml*.dll", "PySide6/Qt6Pdf*.dll", "PySide6/Qt6Designer*.dll",
               "PySide6/QtQuick*.pyd", "PySide6/QtQml*.pyd", "PySide6/QtDesigner*.pyd", "PySide6/QtPdf*.pyd",
               "**/*.pyi", "**/__pycache__"]

LAUNCHER = "\r\n".join([
    "@echo off",
    'start "" "%~dp0python\\pythonw.exe" -m wuwastat',
    "",
])

README = """명조 육성 등급 계산기 {version} (포터블)

[실행]
  wuwastat.bat 을 더블클릭하세요. 설치할 것은 없습니다.

[필요 조건]
  - Windows 10/11 (64비트)
  - Windows 한국어 OCR 언어팩
    (한국어 Windows에는 보통 기본으로 들어 있습니다. 없으면 설정 > 시간 및 언어 > 언어 > 한국어 > 언어 옵션에서 설치)

[처음 실행할 때]
  - 인터넷에서 받은 ZIP은 Windows가 '확인되지 않은 파일'로 표시할 수 있습니다.
    ZIP 파일을 우클릭 > 속성 > 아래쪽 '차단 해제'에 체크 > 확인 한 뒤 압축을 풀면 경고 없이 실행됩니다.
  - 압축은 짧은 경로에 푸세요 (예: C:\\wuwastat). 너무 깊은 폴더에 풀면 Windows 경로 길이 제한에 걸릴 수 있습니다.

[저장 위치]
  - 입력값: %APPDATA%\\wuwastat\\wuwastat\\builds
  - 이 폴더를 지워도 프로그램은 다시 실행됩니다.

[업데이트]
  - 프로그램 상단의 '프로그램 업데이트 확인' 버튼을 누르면 새 버전을 받아 설치하고 다시 실행합니다.
    배포자의 서명이 확인된 공식 파일만 설치합니다. 입력값은 위 저장 위치에 그대로 남아 있습니다.
  - 직접 바꾸려면 새 버전 ZIP을 받아 새 폴더에 풀어 쓰면 됩니다.
"""


def run(cmd: list[str]) -> None:
    print("$", " ".join(cmd))
    subprocess.run(cmd, check=True)


def download_embed() -> Path:
    CACHE.mkdir(parents=True, exist_ok=True)
    dest = CACHE / Path(EMBED_URL).name
    if not dest.exists():
        assert EMBED_URL.startswith("https://www.python.org/"), EMBED_URL
        print("내려받는 중:", EMBED_URL)
        with urllib.request.urlopen(EMBED_URL, timeout=60) as r:
            dest.write_bytes(r.read())
    print("embed sha256:", hashlib.sha256(dest.read_bytes()).hexdigest())
    return dest


def verify_signatures(py: Path) -> None:
    """받은 임베디드 Python의 실행 파일이 Python 재단 서명을 가진 공식 파일인지 Windows로 확인한다."""
    if sys.platform != "win32":
        print("서명 확인 건너뜀 (Windows가 아님)")
        return
    for exe in ("python.exe", "pythonw.exe"):
        ps = (f"$s = Get-AuthenticodeSignature '{py / exe}'; "
              "Write-Output ($s.Status.ToString() + '|' + $s.SignerCertificate.Subject)")
        # GitHub Actions(pwsh)에서 실행하면 PSModulePath에 PowerShell 7 경로가 섞여 Windows PowerShell 5.1이
        # Microsoft.PowerShell.Security 모듈(Get-AuthenticodeSignature)을 못 불러온다. 그 변수를 빼고 실행한다.
        env = {k: v for k, v in os.environ.items() if k.upper() != "PSMODULEPATH"}
        r = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", ps],
                           capture_output=True, text=True, env=env)
        out = r.stdout.strip()
        status, _, subject = out.partition("|")
        if status != "Valid" or "Python Software Foundation" not in subject:
            raise SystemExit(f"{exe} 서명 확인 실패: {out or '(출력 없음)'} {r.stderr.strip()}")
        print(f"{exe} 서명 확인: {subject.split(',')[0]}")


def prune(site: Path) -> None:
    for d in PRUNE_DIRS:
        shutil.rmtree(site / d, ignore_errors=True)
    for f in PRUNE_FILES:
        (site / f).unlink(missing_ok=True)
    for pattern in PRUNE_GLOBS:
        for p in site.glob(pattern):
            if p.is_dir():
                shutil.rmtree(p, ignore_errors=True)
            else:
                p.unlink(missing_ok=True)


def copy_app(dest: Path) -> None:
    def ignore(_dir, names):
        return [n for n in names if n in APP_EXCLUDE or n.endswith((".tmp", ".pyc"))]
    for item in APP_ITEMS:
        shutil.copytree(ROOT / item, dest / item, ignore=ignore)


def build() -> Path:
    shutil.rmtree(STAGE, ignore_errors=True)
    py = STAGE / "python"
    py.mkdir(parents=True)
    with zipfile.ZipFile(download_embed()) as z:
        z.extractall(py)
    verify_signatures(py)

    site = py / "Lib" / "site-packages"
    run([sys.executable, "-m", "pip", "install", "--quiet", "--no-compile", "--disable-pip-version-check",
         "--only-binary=:all:", "--platform", "win_amd64", "--implementation", "cp",
         "--python-version", ".".join(PY_VERSION.split(".")[:2]),
         "--target", str(site), "-r", str(ROOT / "requirements.txt")])
    prune(site)

    # 임베디드 Python이 site-packages와 앱 폴더를 찾도록 경로 파일을 고친다
    pth = py / f"python{PY_TAG}._pth"
    pth.write_text("\n".join([f"python{PY_TAG}.zip", ".", "Lib\\site-packages", "..\\app", "import site", ""]),
                   encoding="ascii")

    copy_app(STAGE / "app")
    (STAGE / "wuwastat.bat").write_bytes(LAUNCHER.encode("ascii"))
    (STAGE / "README.txt").write_text(README.format(version=__version__), encoding="utf-8-sig")

    longest = max(len(str(p.relative_to(STAGE.parent))) for p in STAGE.rglob("*"))
    print("가장 긴 경로 (압축 안):", longest, "자")

    ZIP_PATH.unlink(missing_ok=True)
    with zipfile.ZipFile(ZIP_PATH, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as z:
        for p in sorted(STAGE.rglob("*")):
            if p.is_file():
                z.write(p, Path("wuwastat") / p.relative_to(STAGE))
    size = ZIP_PATH.stat().st_size / 1024 / 1024
    print(f"완료: {ZIP_PATH} ({size:.1f} MB)")
    return ZIP_PATH


if __name__ == "__main__":
    build()
