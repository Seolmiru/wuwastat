"""캐릭터·에코 초상화 이미지.

이미지는 저장소의 assets 폴더에 들어 있고, 앱은 이 파일만 읽는다 (실행 중에 사이트에 접속하지 않는다).

  assets/portraits/<캐릭터 ID>.png
  assets/echoes/<파일 이름>.png + assets/echoes/index.json  (에코 이름 → 파일 이름)

이미지를 새로 받는 것은 개발자가 명령으로 할 때만이다:
  python -m wuwastat.scraper --export-slim --download-images
받을 때는 공식 CDN의 https 주소만 허용하고, 크기를 제한하고, 실제 이미지인지 확인한다.
"""

from __future__ import annotations

import hashlib
import io
import json
import re
import urllib.request
from difflib import SequenceMatcher
from pathlib import Path
from urllib.parse import urlparse

from PIL import Image

ASSETS_DIR = Path(__file__).resolve().parent.parent / "assets"
PORTRAIT_DIR = ASSETS_DIR / "portraits"
ECHO_DIR = ASSETS_DIR / "echoes"
ECHO_INDEX = ECHO_DIR / "index.json"

ALLOWED_HOSTS = {"guide-res.aki-game.net"}
MAX_BYTES = 5 * 1024 * 1024
MAX_PIXELS = 2048 * 2048
TIMEOUT = 15
SAVE_SIZE = 160  # 저장할 때 이 크기로 줄인다 (화면에는 최대 120px로 표시)


# ---------------------------------------------------------------------------
# 앱: 저장소의 이미지 파일 읽기 (네트워크 없음)
# ---------------------------------------------------------------------------
def portrait_path(char_id: str) -> Path | None:
    if not re.fullmatch(r"\d{1,8}", char_id or ""):
        return None
    p = PORTRAIT_DIR / f"{char_id}.png"
    return p if p.is_file() else None


def load_echo_index() -> dict[str, str]:
    try:
        data = json.loads(ECHO_INDEX.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    # 파일 이름은 우리가 만든 형식(16진수 해시.png)만 받는다
    return {k: v for k, v in data.items() if isinstance(v, str) and re.fullmatch(r"[0-9a-f]{16}\.png", v)}


def _norm(s: str) -> str:
    return re.sub(r"[\s·.\-:：()（）]", "", s or "")


def match_echo_image(name: str, echo_images: dict[str, str]) -> str | None:
    """OCR로 읽은 에코 이름(오타 가능)에 가장 가까운 에코의 값(파일 이름).

    '악몽 · 헤카테'와 '헤카테'처럼 접두어만 다른 에코가 따로 있으므로 거의 같은 이름일 때만 고른다.
    """
    n = _norm(name)
    if not n:
        return None
    best, best_r = None, 0.0
    for k, v in echo_images.items():
        kn = _norm(k)
        r = 1.0 if kn == n else SequenceMatcher(None, n, kn).ratio()
        if r > best_r:
            best, best_r = v, r
    return best if best_r >= 0.85 else None


def echo_image_path(name: str, index: dict[str, str]) -> Path | None:
    f = match_echo_image(name, index)
    if not f:
        return None
    p = ECHO_DIR / f
    return p if p.is_file() else None


# ---------------------------------------------------------------------------
# 개발자용: 공식 CDN에서 받아 assets에 저장
# ---------------------------------------------------------------------------
def is_allowed(url: str) -> bool:
    try:
        u = urlparse(url)
    except ValueError:
        return False
    return u.scheme == "https" and u.hostname in ALLOWED_HOSTS


def _validate(data: bytes) -> bytes | None:
    """실제 이미지인지 확인하고 256px PNG로 다시 저장한다 (이상한 파일이면 None)."""
    try:
        with Image.open(io.BytesIO(data)) as im:
            if im.format not in ("PNG", "JPEG", "WEBP") or im.width * im.height > MAX_PIXELS:
                return None
            im.load()
            im = im.convert("RGBA")
            if max(im.size) > SAVE_SIZE:
                im.thumbnail((SAVE_SIZE, SAVE_SIZE), Image.LANCZOS)
            out = io.BytesIO()
            im.save(out, format="PNG", optimize=True)
            return out.getvalue()
    except Exception:
        return None


def fetch_remote(url: str) -> bytes | None:
    if not url or not is_allowed(url):
        return None
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "wuwastat/0.1"})
        with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
            if not is_allowed(resp.geturl()):
                return None
            raw = resp.read(MAX_BYTES + 1)
    except Exception:
        return None
    if len(raw) > MAX_BYTES:
        return None
    return _validate(raw)


def echo_file_name(name: str) -> str:
    return hashlib.sha256(name.encode("utf-8")).hexdigest()[:16] + ".png"


def download_assets(data: dict, progress=print) -> tuple[int, int]:
    """수집 데이터의 이미지 주소로 assets 폴더를 채운다. 이미 있는 파일은 건너뛴다. 반환: (초상화 수, 에코 수)"""
    PORTRAIT_DIR.mkdir(parents=True, exist_ok=True)
    ECHO_DIR.mkdir(parents=True, exist_ok=True)
    n_portrait = 0
    members = [{"id": k, **v} for k, v in (data.get("team_members") or {}).items() if isinstance(v, dict)]
    for c in [*data.get("characters", []), *members]:
        cid, url = str(c.get("id", "")), c.get("portrait") or ""
        if not re.fullmatch(r"\d{1,8}", cid) or not url:
            continue
        dest = PORTRAIT_DIR / f"{cid}.png"
        if not dest.exists():
            png = fetch_remote(url)
            if png is None:
                progress(f"초상화 받기 실패: {c.get('name')}")
                continue
            dest.write_bytes(png)
        n_portrait += 1
    index = load_echo_index()
    for name, url in (data.get("echo_images") or {}).items():
        fname = echo_file_name(name)
        dest = ECHO_DIR / fname
        if not dest.exists():
            png = fetch_remote(url)
            if png is None:
                progress(f"에코 이미지 받기 실패: {name}")
                continue
            dest.write_bytes(png)
        index[name] = fname
    ECHO_INDEX.write_text(json.dumps(dict(sorted(index.items())), ensure_ascii=False, indent=1), encoding="utf-8")
    return n_portrait, len(index)
