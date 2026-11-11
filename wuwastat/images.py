"""캐릭터·에코 초상화 이미지: 공식 공략 서버에서 받아 PC 캐시 폴더에만 저장한다.

이미지는 쿠로게임즈 저작물이므로 저장소에 넣지 않는다. 데이터 파일에는 주소만 있다.
보안: 공식 CDN의 https 주소만 받고, 크기를 제한하고, 실제 이미지인지 확인한 뒤 쓴다.
"""

from __future__ import annotations

import hashlib
import io
import re
import urllib.request
from difflib import SequenceMatcher
from pathlib import Path
from urllib.parse import urlparse

from PIL import Image

ALLOWED_HOSTS = {"guide-res.aki-game.net"}
MAX_BYTES = 5 * 1024 * 1024
MAX_PIXELS = 2048 * 2048
TIMEOUT = 15


def is_allowed(url: str) -> bool:
    try:
        u = urlparse(url)
    except ValueError:
        return False
    return u.scheme == "https" and u.hostname in ALLOWED_HOSTS


def _cache_file(cache_dir: Path, url: str) -> Path:
    return cache_dir / (hashlib.sha256(url.encode("utf-8")).hexdigest()[:32] + ".png")


def _validate(data: bytes) -> bytes | None:
    """실제 이미지인지 확인하고 PNG로 다시 저장한다 (이상한 파일이면 None)."""
    try:
        with Image.open(io.BytesIO(data)) as im:
            if im.format not in ("PNG", "JPEG", "WEBP") or im.width * im.height > MAX_PIXELS:
                return None
            im.load()
            out = io.BytesIO()
            im.convert("RGBA").save(out, format="PNG")
            return out.getvalue()
    except Exception:
        return None


def fetch(url: str, cache_dir: Path) -> bytes | None:
    """이미지 PNG 바이트. 캐시에 있으면 캐시에서, 없으면 공식 서버에서 받는다. 실패하면 None."""
    if not url or not is_allowed(url):
        return None
    cache_dir.mkdir(parents=True, exist_ok=True)
    f = _cache_file(cache_dir, url)
    if f.exists():
        try:
            return f.read_bytes()
        except OSError:
            pass
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "wuwastat/0.1"})
        with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
            if not is_allowed(resp.geturl()):  # 다른 곳으로 리다이렉트되면 받지 않는다
                return None
            raw = resp.read(MAX_BYTES + 1)
    except Exception:
        return None
    if len(raw) > MAX_BYTES:
        return None
    png = _validate(raw)
    if png is None:
        return None
    try:
        tmp = f.with_suffix(".tmp")
        tmp.write_bytes(png)
        tmp.replace(f)
    except OSError:
        pass
    return png


def _norm(s: str) -> str:
    return re.sub(r"[\s·.\-:：()（）]", "", s or "")


def match_echo_image(name: str, echo_images: dict[str, str]) -> str | None:
    """OCR로 읽은 에코 이름(오타 가능)에 가장 가까운 공식 에코 이미지 주소.

    '악몽 · 헤카테'와 '헤카테'처럼 접두어만 다른 에코가 따로 있으므로 거의 같은 이름일 때만 고른다.
    """
    n = _norm(name)
    if not n:
        return None
    best, best_r = None, 0.0
    for k, url in echo_images.items():
        kn = _norm(k)
        r = 1.0 if kn == n else SequenceMatcher(None, n, kn).ratio()
        if r > best_r:
            best, best_r = url, r
    return best if best_r >= 0.85 else None
