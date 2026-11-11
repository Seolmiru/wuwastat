"""릴리즈 ZIP에 업데이트 서명 만들기 (배포하는 사람의 PC에서 실행).

GitHub Actions가 릴리즈와 ZIP을 만든 뒤에 실행한다. 서명 파일(.sig)이 릴리즈에 올라가야
사용자들의 프로그램에 '새 버전 설치' 버튼이 뜬다.

사용
  .venv\\Scripts\\python tools\\sign_release.py v0.3.0
      → GitHub 릴리즈에서 ZIP을 받아 확인하고 build\\wuwastat-0.3.0-win64.zip.sig 를 만든다.
        GitHub 릴리즈 편집 화면(Edit)에서 이 파일을 끌어다 놓고 Update release 를 누르면 끝.
  .venv\\Scripts\\python tools\\sign_release.py v0.3.0 --upload
      → 환경 변수 GITHUB_TOKEN(이 저장소 Contents 쓰기 권한)으로 서명 파일을 릴리즈에 바로 올린다.
  .venv\\Scripts\\python tools\\sign_release.py build\\wuwastat-0.3.0-win64.zip
      → 직접 만든 ZIP에 서명한다.

옵션: --key PATH (기본: 환경 변수 WUWASTAT_SIGNING_KEY 또는 %USERPROFILE%\\.wuwastat-signing\\update_key.pem)
암호로 잠근 키는 환경 변수 WUWASTAT_SIGNING_PASSPHRASE 가 없으면 암호를 물어본다.

주의: 본인이 직접 태그를 올려 만든 릴리즈에만 서명하세요. 서명은 '이 파일을 내가 배포한다'는 보증입니다.
"""

from __future__ import annotations

import argparse
import getpass
import json
import os
import re
import sys
import urllib.request
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from wuwastat import updater  # noqa: E402
from wuwastat.update_key import PUBLIC_KEY_B64  # noqa: E402

BUILD = ROOT / "build"
DEFAULT_KEY = Path.home() / ".wuwastat-signing" / "update_key.pem"
ZIP_RE = re.compile(r"wuwastat-(\d+(?:\.\d+){1,3})-win64\.zip")


def load_private_key(path: Path):
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

    data = path.read_bytes()
    try:
        key = serialization.load_pem_private_key(data, password=None)
    except TypeError:  # 암호로 잠긴 키
        pw = os.environ.get("WUWASTAT_SIGNING_PASSPHRASE") or getpass.getpass("비밀 키 암호: ")
        key = serialization.load_pem_private_key(data, password=pw.encode("utf-8"))
    if not isinstance(key, Ed25519PrivateKey):
        raise SystemExit("Ed25519 비밀 키가 아닙니다")
    return key


def check_key_matches_app(key) -> None:
    import base64

    from cryptography.hazmat.primitives import serialization

    pub = key.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
    if not PUBLIC_KEY_B64:
        raise SystemExit("wuwastat/update_key.py 에 공개 키가 없습니다. tools/gen_update_key.py 를 먼저 실행하세요.")
    if base64.b64encode(pub).decode("ascii") != PUBLIC_KEY_B64:
        raise SystemExit("이 비밀 키는 앱에 들어 있는 공개 키(wuwastat/update_key.py)와 짝이 아닙니다.")


def zip_inner_version(zip_path: Path) -> str:
    with zipfile.ZipFile(zip_path) as z:
        text = z.read("wuwastat/app/wuwastat/__init__.py").decode("utf-8")
    m = re.search(r'__version__\s*=\s*"([^"]+)"', text)
    if not m:
        raise SystemExit("ZIP 안에서 버전을 읽지 못했습니다")
    return m.group(1)


def github_request(url: str, token: str, method: str = "GET", data: bytes | None = None,
                   content_type: str | None = None) -> dict | None:
    headers = {"User-Agent": updater.USER_AGENT, "Accept": "application/vnd.github+json",
               "Authorization": f"Bearer {token}", "X-GitHub-Api-Version": "2022-11-28"}
    if content_type:
        headers["Content-Type"] = content_type
    req = urllib.request.Request(url, data=data, method=method, headers=headers)
    with urllib.request.urlopen(req, timeout=120) as r:
        body = r.read()
    return json.loads(body) if body else None


def upload_sig(release_json: dict, sig_path: Path, token: str) -> None:
    for a in release_json.get("assets", []):
        if a.get("name") == sig_path.name:
            print("기존 서명 파일을 지웁니다")
            github_request(f"https://api.github.com/repos/{updater.REPO}/releases/assets/{a['id']}", token, "DELETE")
    rid = release_json["id"]
    url = f"https://uploads.github.com/repos/{updater.REPO}/releases/{rid}/assets?name={sig_path.name}"
    github_request(url, token, "POST", sig_path.read_bytes(), "application/octet-stream")
    print(f"릴리즈에 올렸습니다: {sig_path.name}")


def main() -> int:
    for s in (sys.stdout, sys.stderr):
        s.reconfigure(encoding="utf-8", errors="replace")
    ap = argparse.ArgumentParser(description="릴리즈 ZIP에 업데이트 서명 만들기")
    ap.add_argument("target", help="태그 (예: v0.3.0) 또는 로컬 ZIP 경로")
    ap.add_argument("--key", type=Path, default=Path(os.environ.get("WUWASTAT_SIGNING_KEY") or DEFAULT_KEY))
    ap.add_argument("--upload", action="store_true", help="GITHUB_TOKEN으로 릴리즈에 바로 올린다")
    args = ap.parse_args()

    key = load_private_key(args.key.expanduser())
    check_key_matches_app(key)

    release_json = None
    target = Path(args.target)
    if target.suffix.lower() == ".zip":
        m = ZIP_RE.fullmatch(target.name)
        if not m:
            raise SystemExit("ZIP 이름이 wuwastat-<버전>-win64.zip 형식이 아닙니다")
        version, zip_path = m.group(1), target
    else:
        version = updater.version_str(args.target)
        tag = args.target if args.target.startswith("v") else f"v{version}"
        release_json = updater.fetch_json(updater.API_TAG + tag)
        rel = updater.parse_release(release_json)
        BUILD.mkdir(exist_ok=True)
        zip_path = BUILD / rel.zip_name
        print(f"내려받는 중: {rel.zip_url}")
        updater.download(rel.zip_url, zip_path, updater.MAX_ZIP_BYTES)

    name = updater.zip_name(version)
    if zip_path.name != name:
        raise SystemExit(f"파일 이름이 {name}이어야 합니다")
    inner = zip_inner_version(zip_path)
    if inner != version:
        raise SystemExit(f"ZIP 안의 버전({inner})과 릴리즈 버전({version})이 다릅니다")

    sha = updater.file_sha256(zip_path)
    doc = updater.make_signature_doc(key, version, name, sha)
    sig_path = zip_path.with_name(name + ".sig")
    sig_path.write_text(json.dumps(doc, indent=1), encoding="utf-8")
    updater.verify_update(zip_path, sig_path.read_bytes(), version, name)  # 앱과 같은 방법으로 다시 확인
    print(f"버전 {version}, SHA-256 {sha}")
    print(f"서명 파일: {sig_path}")

    if args.upload:
        token = os.environ.get("GITHUB_TOKEN")
        if not token:
            raise SystemExit("GITHUB_TOKEN 환경 변수가 없습니다. 서명 파일을 릴리즈 편집 화면에 직접 올려 주세요.")
        if release_json is None:
            release_json = updater.fetch_json(updater.API_TAG + f"v{version}")
        upload_sig(release_json, sig_path, token)
    else:
        print("\nGitHub 릴리즈 페이지 > 해당 릴리즈 Edit > 위 .sig 파일을 끌어다 놓기 > Update release")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
