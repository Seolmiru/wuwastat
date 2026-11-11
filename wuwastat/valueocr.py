"""스탯 숫자 영역만 잘라서 정밀하게 읽는다.

게임 화면은 숫자 뒤에 밝은 붓 자국 같은 배경이 겹쳐 있어서 전체 화면 OCR로는 숫자가 자주 틀린다.
1. 글자는 무채색에 가까운 밝은 색이므로 '밝고 채도가 낮은 픽셀'만 남긴 마스크를 만든다.
2. 마스크에서 숫자 크기와 맞지 않는 얼룩(배경 붓 자국 조각)을 연결 성분 단위로 지운다.
3. 숫자만 남은 흑백 이미지를 영어 OCR 엔진으로 읽는다 (한국어 엔진은 숫자를 한글로 착각한다).
4. 밝기 기준과 확대 배율을 바꿔 여러 번 읽고, 형식에 맞는 결과 중 다수결로 고른다.
"""

from __future__ import annotations

import asyncio
import io
import re
from collections import Counter

import numpy as np
from PIL import Image, ImageFilter, ImageOps

from .ocr import OcrUnavailable, _recognize_async, available_languages

_NUM_LANG: str | None = None


def _num_lang() -> str:
    global _NUM_LANG
    if _NUM_LANG is None:
        try:
            langs = available_languages()
        except OcrUnavailable:
            langs = []
        _NUM_LANG = next((l for l in langs if l.lower().startswith("en")), "ko")
    return _NUM_LANG


def _components(m: np.ndarray) -> tuple[np.ndarray, list[tuple[int, int, int, int, int, int]]]:
    """8방향 연결 성분. 반환: (라벨 배열, [(번호, y0, y1, x0, x1, 넓이)])"""
    h, w = m.shape
    lab = np.zeros((h, w), dtype=np.int32)
    comps = []
    n = 0
    ys_all, xs_all = np.nonzero(m)
    for y0, x0 in zip(ys_all.tolist(), xs_all.tolist()):
        if lab[y0, x0]:
            continue
        n += 1
        lab[y0, x0] = n
        stack = [(y0, x0)]
        miny = maxy = y0
        minx = maxx = x0
        area = 0
        while stack:
            y, x = stack.pop()
            area += 1
            miny, maxy = min(miny, y), max(maxy, y)
            minx, maxx = min(minx, x), max(maxx, x)
            for yy in (y - 1, y, y + 1):
                if 0 <= yy < h:
                    for xx in (x - 1, x, x + 1):
                        if 0 <= xx < w and m[yy, xx] and not lab[yy, xx]:
                            lab[yy, xx] = n
                            stack.append((yy, xx))
        comps.append((n, miny, maxy, minx, maxx, area))
    return lab, comps


def clean_mask(m: np.ndarray) -> np.ndarray:
    """숫자/소수점 크기에 맞는 성분만 남긴다."""
    lab, comps = _components(m)
    sized = [c for c in comps if c[5] >= 4]
    if not sized:
        return m
    # 숫자 높이: 너무 길쭉한(가로로 긴) 얼룩을 빼고 가장 큰 높이
    cand = [c[2] - c[1] + 1 for c in sized if (c[4] - c[3] + 1) <= 1.5 * (c[2] - c[1] + 1)]
    if not cand:
        return m
    th = max(cand)
    big = [c for c in sized if (c[2] - c[1] + 1) >= 0.6 * th]
    top = float(np.median([c[1] for c in big]))
    bot = float(np.median([c[2] for c in big]))
    keep = np.zeros_like(m)
    for n, y0, y1, x0, x1, area in comps:
        h, w = y1 - y0 + 1, x1 - x0 + 1
        digit = h >= 0.6 * th and y0 >= top - 0.25 * th and y1 <= bot + 0.25 * th and w <= 1.2 * th
        dot = h <= 0.35 * th and w <= 0.35 * th and y1 >= bot - 0.2 * th and area >= 2
        if digit or dot:
            keep |= lab == n
    return keep


def _text_brightness(a: np.ndarray, sat: int) -> float:
    mn, mx = a.min(-1), a.max(-1)
    low_sat = mn[(mx - mn) < sat]
    if low_sat.size == 0:
        return 220.0
    return float(np.percentile(low_sat, 99))


def _to_ocr_image(m: np.ndarray, scale: int) -> Image.Image:
    im = Image.fromarray(np.where(m, 0, 255).astype("uint8"))
    im = im.resize((im.width * scale, im.height * scale), Image.LANCZOS)
    im = im.filter(ImageFilter.GaussianBlur(0.3 * scale))
    return ImageOps.expand(im, 24, fill=255)


def _ocr(im: Image.Image) -> str:
    buf = io.BytesIO()
    im.save(buf, format="PNG")
    words = asyncio.run(_recognize_async(buf.getvalue(), _num_lang()))
    return " ".join(w.text for w in words)


_TRANS = str.maketrans({"o": "0", "O": "0", "D": "0", "l": "1", "I": "1", "|": "1", "S": "5", ",": "."})


def normalize(text: str) -> str:
    t = text.replace(" ", "")
    t = re.sub(r"[oO0°]/[oO0]|%o", "%", t)
    return t.translate(_TRANS)


NUM_TOKEN = re.compile(r"\d+(?:\.\d+)?")


def candidates(crop: Image.Image, sat: int = 40) -> list[str]:
    a = np.asarray(crop.convert("RGB")).astype(np.int16)
    bright = _text_brightness(a, sat)
    mn, mx = a.min(-1), a.max(-1)
    low_sat = (mx - mn) < sat
    out: list[str] = []
    for drop in (55, 35, 20):
        th = min(max(bright - drop, 90), 245)
        m = clean_mask((mn > th) & low_sat)
        if not m.any():
            continue
        for scale in (2, 3):
            toks = NUM_TOKEN.findall(normalize(_ocr(_to_ocr_image(m, scale))))
            if toks:
                out.append(toks[-1])
    return out


def choose(cands: list[str], expect_decimal: bool | None) -> str | None:
    """형식에 맞는 후보 중 다수결. expect_decimal=None 이면 형식 제한 없음."""
    def ok(c: str) -> bool:
        if expect_decimal is True:
            return bool(re.fullmatch(r"\d{1,3}\.\d", c))
        if expect_decimal is False:
            return bool(re.fullmatch(r"\d{1,6}", c))
        return bool(re.fullmatch(r"\d{1,3}\.\d|\d{1,6}", c))

    valid = [c for c in cands if ok(c)]
    if not valid:
        return None
    counts = Counter(valid)
    # 동률이면 더 긴 쪽 (얼룩과 붙은 숫자가 빠져서 짧게 읽히는 경우가 더 흔하다)
    return max(counts, key=lambda c: (counts[c], len(c)))


def read_value(crop: Image.Image, expect_decimal: bool | None = None) -> float | None:
    v = choose(candidates(crop), expect_decimal)
    return float(v) if v is not None else None
