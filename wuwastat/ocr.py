"""Windows 내장 OCR(Windows.Media.Ocr)로 스크린샷의 글자를 읽는다.

한국어 OCR 언어팩이 필요하다. (설정 > 시간 및 언어 > 언어 > 한국어 > 언어 옵션 > 광학 문자 인식)
"""

from __future__ import annotations

import asyncio
import io
from dataclasses import dataclass
from pathlib import Path

from PIL import Image, ImageFilter, ImageOps


@dataclass
class Word:
    text: str
    x: float
    y: float
    w: float
    h: float

    @property
    def cx(self) -> float:
        return self.x + self.w / 2

    @property
    def cy(self) -> float:
        return self.y + self.h / 2


class OcrUnavailable(RuntimeError):
    pass


def available_languages() -> list[str]:
    try:
        from winrt.windows.media.ocr import OcrEngine
    except ImportError as e:
        raise OcrUnavailable("winrt OCR 패키지가 설치되어 있지 않습니다") from e
    return [lang.language_tag for lang in OcrEngine.available_recognizer_languages]


VARIANTS = ("clean", "raw", "mask", "mask_hi")
_MASK_THRESHOLD = {"mask": 180, "mask_hi": 200}


def _text_mask(img: Image.Image, threshold: int, max_sat: int = 50) -> Image.Image:
    """밝고 채도가 낮은 픽셀(게임 UI 글자)만 검은색으로 남긴다. 화려한 배경을 지우는 데 효과적이다."""
    import numpy as np

    a = np.asarray(img.convert("RGB")).astype(np.int16)
    mn, mx = a.min(-1), a.max(-1)
    m = (mn > threshold) & ((mx - mn) < max_sat)
    return Image.fromarray(np.where(m, 0, 255).astype("uint8"))


def preprocess(img: Image.Image, variant: str = "clean", max_dim: int = 2600) -> tuple[Image.Image, float]:
    """OCR 전에 이미지를 다듬는다. 반환값: (처리된 이미지, 원본 대비 배율)

    clean  : 흑백 + 대비 보정 + (어두운 배경이면) 반전 + 확대
    raw    : 원본 그대로 (크기만 제한)
    mask   : 밝은 무채색 글자만 남긴 흑백 이미지 (배경 그림이 복잡할 때 강함)
    mask_hi: mask보다 높은 밝기 기준
    blur   : clean + 약한 블러
    """
    if variant in _MASK_THRESHOLD:
        g = _text_mask(img, _MASK_THRESHOLD[variant])
        scale = 1.0
        if max(g.size) < 1600:  # 작은 이미지(조각)는 2배로 키워 작은 글자도 읽히게
            scale = min(2.0, max_dim / max(g.size))
            g = g.resize((round(g.width * scale), round(g.height * scale)), Image.LANCZOS)
        elif max(g.size) > max_dim:
            scale = max_dim / max(g.size)
            g = g.resize((round(g.width * scale), round(g.height * scale)), Image.LANCZOS)
        return g.filter(ImageFilter.GaussianBlur(0.5)), scale

    scale = 1.0
    longest = max(img.size)
    if variant != "raw" and longest < 1600:
        scale = min(2.0, max_dim / longest)
    elif longest > max_dim:
        scale = max_dim / longest

    if variant == "raw":
        g = img.convert("RGB")
    else:
        g = ImageOps.grayscale(img.convert("RGB"))
        g = ImageOps.autocontrast(g, cutoff=1)
        # 게임 UI는 어두운 배경에 밝은 글자가 많다. OCR은 흰 배경 검은 글자에서 정확하다.
        mean = sum(i * c for i, c in enumerate(g.histogram())) / max(g.width * g.height, 1)
        if mean < 128:
            g = ImageOps.invert(g)
    if scale != 1.0:
        g = g.resize((round(g.width * scale), round(g.height * scale)), Image.LANCZOS)
    if variant == "blur":
        g = g.filter(ImageFilter.GaussianBlur(1))
    return g, scale


async def _recognize_async(png: bytes, lang: str) -> list[Word]:
    from winrt.windows.globalization import Language
    from winrt.windows.graphics.imaging import BitmapDecoder
    from winrt.windows.media.ocr import OcrEngine
    from winrt.windows.storage.streams import DataWriter, InMemoryRandomAccessStream

    engine = OcrEngine.try_create_from_language(Language(lang))
    if engine is None:
        raise OcrUnavailable(f"'{lang}' OCR 언어팩이 설치되어 있지 않습니다")

    stream = InMemoryRandomAccessStream()
    writer = DataWriter(stream)
    writer.write_bytes(png)
    await writer.store_async()
    await writer.flush_async()
    writer.detach_stream()
    stream.seek(0)
    decoder = await BitmapDecoder.create_async(stream)
    bitmap = await decoder.get_software_bitmap_async()
    result = await engine.recognize_async(bitmap)

    words: list[Word] = []
    for line in result.lines:
        for w in line.words:
            r = w.bounding_rect
            words.append(Word(w.text, r.x, r.y, r.width, r.height))
    return words


def recognize(image: Image.Image | str | Path, lang: str = "ko", variant: str = "clean") -> list[Word]:
    """이미지에서 단어와 위치(원본 이미지 좌표)를 읽는다."""
    try:
        import winrt.windows.media.ocr  # noqa: F401
    except ImportError as e:
        raise OcrUnavailable("winrt OCR 패키지가 설치되어 있지 않습니다") from e

    img = Image.open(image) if isinstance(image, (str, Path)) else image
    processed, scale = preprocess(img, variant)
    buf = io.BytesIO()
    processed.save(buf, format="PNG")
    words = asyncio.run(_recognize_async(buf.getvalue(), lang))
    if scale != 1.0:
        words = [Word(w.text, w.x / scale, w.y / scale, w.w / scale, w.h / scale) for w in words]
    return words


def recognize_tiled(image: Image.Image, cols: int = 2, rows: int = 2, overlap: float = 0.12,
                    lang: str = "ko", variant: str = "clean") -> list[Word]:
    """큰 화면을 겹치는 조각으로 나눠 각각 확대해서 읽는다.

    Windows OCR은 1080p 게임 화면을 통째로 읽으면 작은 글자를 많이 놓치지만,
    작은 조각은 2배로 확대해서 읽으므로 정확하다. 겹치는 부분에서 두 번 읽힌 단어는 하나만 남긴다.
    """
    w, h = image.size
    tw, th = w / cols, h / rows
    ox, oy = tw * overlap, th * overlap
    words: list[Word] = []
    for r in range(rows):
        for c in range(cols):
            x0, y0 = int(max(c * tw - ox, 0)), int(max(r * th - oy, 0))
            x1, y1 = int(min((c + 1) * tw + ox, w)), int(min((r + 1) * th + oy, h))
            for wd in recognize(image.crop((x0, y0, x1, y1)), lang=lang, variant=variant):
                words.append(Word(wd.text, wd.x + x0, wd.y + y0, wd.w, wd.h))
    unique: list[Word] = []
    for wd in sorted(words, key=lambda x: -len(x.text)):
        if any(u.text == wd.text and abs(u.cx - wd.cx) < wd.h and abs(u.cy - wd.cy) < wd.h for u in unique):
            continue
        unique.append(wd)
    return sorted(unique, key=lambda x: (x.y, x.x))
