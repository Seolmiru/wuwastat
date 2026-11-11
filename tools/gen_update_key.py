"""업데이트 서명 키 만들기 (배포하는 사람이 처음 한 번만 실행).

- 비밀 키: 저장소 밖에 저장한다 (기본 %USERPROFILE%\\.wuwastat-signing\\update_key.pem).
  이 파일이 있어야 릴리즈에 서명할 수 있다. 잃어버리면 기존 앱이 새 업데이트를 받을 수 없으니 꼭 백업한다.
  절대 저장소에 올리거나 남에게 보내지 않는다.
- 공개 키: wuwastat/update_key.py 의 PUBLIC_KEY_B64 를 자동으로 바꾼다 (커밋해서 앱에 넣는다).

사용
  .venv\\Scripts\\python tools\\gen_update_key.py              # 비밀 키를 암호 없이 저장
  .venv\\Scripts\\python tools\\gen_update_key.py --encrypt    # 비밀 키를 암호로 잠가 저장 (서명할 때 암호 입력)
  --out PATH  저장 위치, --force  이미 있는 키 덮어쓰기 (주의: 기존 앱들이 새 키 업데이트를 거부하게 됨)
"""

from __future__ import annotations

import argparse
import base64
import getpass
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
KEY_MODULE = ROOT / "wuwastat" / "update_key.py"
DEFAULT_KEY = Path.home() / ".wuwastat-signing" / "update_key.pem"


def main() -> int:
    for s in (sys.stdout, sys.stderr):
        s.reconfigure(encoding="utf-8", errors="replace")
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

    ap = argparse.ArgumentParser(description="업데이트 서명 키 만들기")
    ap.add_argument("--out", type=Path, default=DEFAULT_KEY)
    ap.add_argument("--encrypt", action="store_true", help="비밀 키를 암호로 잠근다")
    ap.add_argument("--force", action="store_true", help="이미 있는 키를 덮어쓴다")
    args = ap.parse_args()

    out: Path = args.out.expanduser().resolve()
    if ROOT in out.parents:
        print(f"비밀 키는 저장소 폴더 밖에 저장해야 합니다: {out}", file=sys.stderr)
        return 1
    if out.exists() and not args.force:
        print(f"이미 키가 있습니다: {out}\n덮어쓰면 기존 앱들이 새 업데이트를 거부합니다. 정말이면 --force", file=sys.stderr)
        return 1

    if args.encrypt:
        pw = getpass.getpass("비밀 키 암호: ")
        if not pw or pw != getpass.getpass("한 번 더: "):
            print("암호가 비었거나 서로 다릅니다", file=sys.stderr)
            return 1
        enc = serialization.BestAvailableEncryption(pw.encode("utf-8"))
    else:
        enc = serialization.NoEncryption()

    key = Ed25519PrivateKey.generate()
    pem = key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, enc)
    pub = key.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
    pub_b64 = base64.b64encode(pub).decode("ascii")

    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_bytes(pem)
    src = KEY_MODULE.read_text(encoding="utf-8")
    src, n = re.subn(r'^PUBLIC_KEY_B64 = ".*"$', f'PUBLIC_KEY_B64 = "{pub_b64}"', src, flags=re.M)
    if n != 1:
        print(f"{KEY_MODULE}에서 PUBLIC_KEY_B64 줄을 찾지 못했습니다. 직접 넣으세요: {pub_b64}", file=sys.stderr)
        return 1
    KEY_MODULE.write_text(src, encoding="utf-8")

    print(f"비밀 키 저장: {out}{' (암호 잠금)' if args.encrypt else ''}")
    print(f"공개 키: {pub_b64}  → {KEY_MODULE.relative_to(ROOT)} 에 넣었습니다. 커밋하세요.")
    print("\n[중요] 비밀 키 파일을 USB나 비밀번호 관리자 등에 백업하세요. 저장소에는 절대 올리지 마세요.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
