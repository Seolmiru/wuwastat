"""프로그램 안에서 새 버전으로 업데이트하기.

흐름
1. GitHub 최신 릴리즈를 확인한다 (check_latest). 보내는 정보는 없고 공개 릴리즈 목록만 읽는다.
2. 릴리즈의 ZIP과 서명 파일(.sig)을 받는다. 이 저장소의 릴리즈 다운로드 주소만 허용한다.
3. 서명 파일을 앱에 들어 있는 공개 키(update_key.py)로 확인한다 (verify_update).
   서명은 배포하는 사람의 PC에만 있는 비밀 키로 만들기 때문에, GitHub 계정이 털려 가짜 ZIP과 가짜 서명이
   올라와도 설치되지 않는다. 서명 내용에 버전·파일 이름·SHA-256이 들어가므로 예전 버전을 새 버전인 척
   다시 올리는 것도 막는다.
4. 프로그램 폴더 안 _update/stage 에 압축을 풀고, 임시 폴더에 복사한 작은 Python으로 update_apply.py 를
   실행한 뒤 앱을 끝낸다. update_apply 가 앱 종료를 기다렸다가 폴더를 바꾸고 다시 실행한다.

입력 기록·설정은 %APPDATA%와 레지스트리에 있어 업데이트와 관계없이 그대로 남는다.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import urllib.request
import zipfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Callable

from . import __version__
from .update_key import PUBLIC_KEY_B64

REPO = "Seolmiru/wuwastat"
API_LATEST = f"https://api.github.com/repos/{REPO}/releases/latest"
API_TAG = f"https://api.github.com/repos/{REPO}/releases/tags/"
DOWNLOAD_PREFIX = f"https://github.com/{REPO}/releases/download/"
PAGE_PREFIX = f"https://github.com/{REPO}/releases/"
USER_AGENT = "wuwastat-updater"

SIGN_CONTEXT = "wuwastat-update-v1"
SIG_FORMAT = 1
MAX_JSON_BYTES = 2 * 1024 * 1024
MAX_SIG_BYTES = 64 * 1024
MAX_ZIP_BYTES = 600 * 1024 * 1024

WORK_NAME = "_update"  # update_apply.WORK_NAME 과 같아야 한다
RUNNER_PREFIX = "wuwastat-updater-"
REQUIRED_ITEMS = ("wuwastat.bat", "python/pythonw.exe", "app/wuwastat/__init__.py", "app/wuwastat/update_apply.py")
VERSION_RE = re.compile(r"v?(\d+(?:\.\d+){1,3})")

Progress = Callable[[object], None]


class UpdateError(Exception):
    """사용자에게 그대로 보여줄 수 있는 업데이트 오류."""


# ---------------------------------------------------------------------------
# 버전
# ---------------------------------------------------------------------------
def version_str(text: str) -> str:
    m = VERSION_RE.fullmatch(str(text).strip())
    if not m:
        raise UpdateError(f"버전 형식이 아닙니다: {text!r}")
    return m.group(1)


def parse_version(text: str) -> tuple[int, ...]:
    parts = tuple(int(x) for x in version_str(text).split("."))
    return parts + (0,) * (4 - len(parts))


def is_newer(latest: str, current: str = __version__) -> bool:
    return parse_version(latest) > parse_version(current)


def zip_name(version: str) -> str:
    return f"wuwastat-{version}-win64.zip"


# ---------------------------------------------------------------------------
# 릴리즈 정보
# ---------------------------------------------------------------------------
@dataclass
class Release:
    version: str
    tag: str
    notes: str
    page_url: str
    zip_name: str
    zip_url: str
    sig_url: str | None

    @property
    def signed(self) -> bool:
        return self.sig_url is not None


def parse_release(data: dict) -> Release:
    if not isinstance(data, dict):
        raise UpdateError("릴리즈 정보 형식이 올바르지 않습니다")
    if data.get("draft") or data.get("prerelease"):
        raise UpdateError("정식 릴리즈가 아닙니다")
    tag = str(data.get("tag_name", ""))
    version = version_str(tag)
    name = zip_name(version)
    assets = {a.get("name"): str(a.get("browser_download_url", ""))
              for a in data.get("assets") or [] if isinstance(a, dict)}
    zip_url = assets.get(name)
    if not zip_url:
        raise UpdateError(f"릴리즈에 {name} 파일이 없습니다")
    sig_url = assets.get(name + ".sig")
    for url in filter(None, (zip_url, sig_url)):
        if not url.startswith(DOWNLOAD_PREFIX):
            raise UpdateError(f"허용되지 않은 다운로드 주소입니다: {url}")
    page = str(data.get("html_url") or "")
    if not page.startswith(PAGE_PREFIX):
        page = PAGE_PREFIX + "latest"
    return Release(version, tag, str(data.get("body") or "")[:4000], page, name, zip_url, sig_url)


def _open(url: str, timeout: float):
    if not url.startswith("https://"):
        raise UpdateError(f"https 주소가 아닙니다: {url}")
    accept = "application/vnd.github+json" if url.startswith("https://api.github.com/") else "application/octet-stream"
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": accept})
    resp = urllib.request.urlopen(req, timeout=timeout)
    if not resp.geturl().startswith("https://"):  # 리다이렉트로 http가 되면 거부
        resp.close()
        raise UpdateError("안전하지 않은 주소로 연결되어 중단했습니다")
    return resp


def fetch_json(url: str, timeout: float = 10) -> dict:
    with _open(url, timeout) as r:
        raw = r.read(MAX_JSON_BYTES + 1)
    if len(raw) > MAX_JSON_BYTES:
        raise UpdateError("릴리즈 정보가 너무 큽니다")
    return json.loads(raw.decode("utf-8"))


def check_latest(timeout: float = 10) -> Release:
    return parse_release(fetch_json(API_LATEST, timeout))


def download(url: str, dest: Path, max_bytes: int, progress: Callable[[int, int], None] | None = None,
             timeout: float = 60) -> Path:
    if not url.startswith(DOWNLOAD_PREFIX):
        raise UpdateError(f"허용되지 않은 다운로드 주소입니다: {url}")
    with _open(url, timeout) as r:
        total = int(r.headers.get("Content-Length") or 0)
        if total > max_bytes:
            raise UpdateError("파일이 너무 큽니다")
        done = 0
        with open(dest, "wb") as f:
            while chunk := r.read(1 << 16):
                done += len(chunk)
                if done > max_bytes:
                    raise UpdateError("파일이 너무 큽니다")
                f.write(chunk)
                if progress:
                    progress(done, total)
    return dest


# ---------------------------------------------------------------------------
# 서명
# ---------------------------------------------------------------------------
def file_sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while chunk := f.read(1 << 20):
            h.update(chunk)
    return h.hexdigest()


def signed_message(version: str, name: str, sha256: str) -> bytes:
    return f"{SIGN_CONTEXT}\n{version}\n{name}\n{sha256}\n".encode("utf-8")


def make_signature_doc(private_key, version: str, name: str, sha256: str) -> dict:
    """tools/sign_release.py 가 쓴다. private_key: Ed25519PrivateKey."""
    sig = private_key.sign(signed_message(version, name, sha256))
    return {"format": SIG_FORMAT, "version": version, "file": name, "sha256": sha256,
            "signature": base64.b64encode(sig).decode("ascii")}


def load_public_key(b64: str):
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

    try:
        raw = base64.b64decode(b64, validate=True)
    except ValueError as e:
        raise UpdateError("앱의 업데이트 공개 키가 올바르지 않습니다") from e
    if len(raw) != 32:
        raise UpdateError("앱의 업데이트 공개 키가 올바르지 않습니다")
    return Ed25519PublicKey.from_public_bytes(raw)


def verify_update(zip_path: Path, sig_bytes: bytes, version: str, name: str,
                  public_key_b64: str = PUBLIC_KEY_B64) -> None:
    """서명이 맞지 않으면 UpdateError."""
    from cryptography.exceptions import InvalidSignature

    if not public_key_b64:
        raise UpdateError("이 버전에는 업데이트 공개 키가 없어 자동 업데이트를 할 수 없습니다")
    try:
        doc = json.loads(sig_bytes.decode("utf-8"))
    except (UnicodeDecodeError, ValueError) as e:
        raise UpdateError("서명 파일 형식이 올바르지 않습니다") from e
    if not isinstance(doc, dict) or doc.get("format") != SIG_FORMAT:
        raise UpdateError("서명 파일 형식이 올바르지 않습니다")
    if doc.get("version") != version or doc.get("file") != name:
        raise UpdateError("서명 파일의 버전 또는 파일 이름이 릴리즈와 다릅니다")
    sha = file_sha256(zip_path)
    if doc.get("sha256") != sha:
        raise UpdateError("내려받은 파일이 서명된 파일과 다릅니다")
    try:
        sig = base64.b64decode(str(doc.get("signature", "")), validate=True)
    except ValueError as e:
        raise UpdateError("서명 값이 올바르지 않습니다") from e
    try:
        load_public_key(public_key_b64).verify(sig, signed_message(version, name, sha))
    except InvalidSignature as e:
        raise UpdateError("서명이 올바르지 않습니다. 공식 배포 파일이 아닐 수 있어 설치하지 않습니다") from e


# ---------------------------------------------------------------------------
# 설치 준비
# ---------------------------------------------------------------------------
def portable_root(module_file: str | Path = __file__) -> Path | None:
    """포터블 배포판으로 실행 중이면 프로그램 폴더, 아니면(소스 코드 실행) None."""
    pkg = Path(module_file).resolve().parent
    app = pkg.parent
    root = app.parent
    if app.name == "app" and (root / "wuwastat.bat").is_file() and (root / "python" / "pythonw.exe").is_file():
        return root
    return None


def safe_extract(zip_path: Path, dest: Path) -> Path:
    """ZIP 안 경로를 확인하고 푼다. 반환: dest/wuwastat"""
    with zipfile.ZipFile(zip_path) as z:
        for info in z.infolist():
            fn = info.filename
            parts = PurePosixPath(fn).parts
            if (fn.startswith("/") or "\\" in fn or ":" in fn or ".." in parts
                    or not parts or parts[0] != "wuwastat"):
                raise UpdateError(f"압축 파일에 허용되지 않는 경로가 있습니다: {fn}")
        z.extractall(dest)
    root = dest / "wuwastat"
    missing = [rel for rel in REQUIRED_ITEMS if not (root / rel).exists()]
    if missing:
        raise UpdateError("압축 파일에 필요한 파일이 없습니다: " + ", ".join(missing))
    return root


def staged_version(stage: Path) -> str:
    text = (stage / "app" / "wuwastat" / "__init__.py").read_text(encoding="utf-8")
    m = re.search(r'__version__\s*=\s*"([^"]+)"', text)
    if not m:
        raise UpdateError("새 버전의 버전 번호를 읽지 못했습니다")
    return m.group(1)


def prepare_runner(python_dir: Path, script: Path, dest: Path) -> Path:
    """임베디드 Python의 핵심 파일만 dest로 복사해 표준 라이브러리만 쓰는 작은 실행기를 만든다."""
    dest.mkdir(parents=True, exist_ok=True)
    for f in python_dir.iterdir():
        if f.is_file() and f.suffix.lower() in (".exe", ".dll", ".pyd", ".zip"):
            shutil.copy2(f, dest / f.name)
    stdlib = sorted(p for p in dest.glob("python3*.zip"))
    if not stdlib or not (dest / "pythonw.exe").is_file():
        raise UpdateError("업데이트 실행기를 만들지 못했습니다 (Python 파일 없음)")
    # site-packages·앱 폴더 없이 표준 라이브러리와 이 폴더만 쓰도록 경로 파일을 새로 쓴다
    (dest / f"{stdlib[0].stem}._pth").write_text(f"{stdlib[0].name}\n.\n", encoding="ascii")
    shutil.copy2(script, dest / "update_apply.py")
    return dest / "pythonw.exe"


def cleanup_leftovers(root: Path | None) -> None:
    """지난 업데이트가 남긴 임시 실행기와 작업 폴더를 지운다 (앱 시작 시)."""
    for p in Path(tempfile.gettempdir()).glob(RUNNER_PREFIX + "*"):
        shutil.rmtree(p, ignore_errors=True)
    if root is not None:
        shutil.rmtree(root / WORK_NAME, ignore_errors=True)


@dataclass
class Plan:
    root: Path
    stage: Path
    runner: Path
    log: Path
    version: str


def prepare_update(release: Release, root: Path, progress: Progress | None = None) -> Plan:
    """내려받기 → 서명 확인 → 압축 풀기 → 실행기 준비. 백그라운드 작업에서 호출한다."""
    if not release.signed:
        raise UpdateError("새 버전의 서명 파일이 아직 올라오지 않았습니다. 잠시 후 다시 시도하세요.")
    if not is_newer(release.version):
        raise UpdateError("이미 최신 버전입니다")
    work = root / WORK_NAME
    shutil.rmtree(work, ignore_errors=True)
    try:
        work.mkdir()
    except OSError as e:
        raise UpdateError(f"프로그램 폴더에 쓸 수 없어 업데이트할 수 없습니다: {e}\n"
                          "C:\\wuwastat 처럼 관리자 권한이 필요 없는 폴더에 압축을 풀어 쓰세요.") from e
    report = progress or (lambda _p: None)
    try:
        report(("sig", 0, 0))
        sig = download(release.sig_url, work / (release.zip_name + ".sig"), MAX_SIG_BYTES).read_bytes()
        zip_path = work / release.zip_name
        download(release.zip_url, zip_path, MAX_ZIP_BYTES, lambda d, t: report(("download", d, t)))
        report(("verify", 0, 0))
        verify_update(zip_path, sig, release.version, release.zip_name)
        report(("extract", 0, 0))
        stage = safe_extract(zip_path, work / "stage")
        if staged_version(stage) != release.version:
            raise UpdateError("압축 파일 안의 버전이 릴리즈 버전과 다릅니다")
        zip_path.unlink()
        runner_dir = Path(tempfile.mkdtemp(prefix=RUNNER_PREFIX))
        exe = prepare_runner(stage / "python", stage / "app" / "wuwastat" / "update_apply.py", runner_dir)
    except Exception:
        shutil.rmtree(work, ignore_errors=True)
        raise
    return Plan(root, stage, exe, runner_dir / "update.log", release.version)


def launch_apply(plan: Plan, pid: int | None = None) -> None:
    """update_apply 를 앱과 분리된 프로세스로 실행한다. 호출한 뒤 앱을 끝내야 한다."""
    args = [str(plan.runner), str(plan.runner.parent / "update_apply.py"),
            "--pid", str(pid or os.getpid()), "--root", str(plan.root), "--stage", str(plan.stage),
            "--work", str(plan.root / WORK_NAME), "--log", str(plan.log)]
    flags = 0x00000008 | 0x00000200 if sys.platform == "win32" else 0  # DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP
    subprocess.Popen(args, cwd=str(plan.runner.parent), close_fds=True, creationflags=flags)
