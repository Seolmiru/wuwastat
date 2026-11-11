"""프로그램 아이콘 만들기: 갈브레나 초상화를 32x32 도트 그림으로 바꾼다.

실행: python tools/make_icon.py
결과: assets/icon.png (256px 미리보기), assets/icon.ico (16~256px)
"""

from pathlib import Path

from PIL import Image, ImageDraw, ImageEnhance

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "assets" / "portraits" / "1208.png"  # 갈브레나
OUT_PNG = ROOT / "assets" / "icon.png"
OUT_ICO = ROOT / "assets" / "icon.ico"

PIXELS = 32          # 도트 그림 크기
COLORS = 16          # 색 수
FACE_CROP = (14, 6, 146, 138)  # 160px 초상화에서 얼굴 쪽 (정사각형)
OUTLINE = (30, 22, 28, 255)    # 바깥 어두운 테두리
ACCENT = (232, 120, 40, 255)   # 안쪽 주황 테두리 (용융 속성 색)


def pixel_icon() -> Image.Image:
    face = Image.open(SRC).convert("RGB").crop(FACE_CROP)
    face = ImageEnhance.Contrast(face).enhance(1.35)   # 작은 크기에서도 눈·머리선이 보이게
    face = ImageEnhance.Color(face).enhance(1.2)
    inner = PIXELS - 4
    dots = face.resize((inner, inner), Image.LANCZOS)
    dots = dots.quantize(colors=COLORS, method=Image.Quantize.MEDIANCUT, dither=Image.Dither.NONE).convert("RGBA")

    icon = Image.new("RGBA", (PIXELS, PIXELS), (0, 0, 0, 0))
    d = ImageDraw.Draw(icon)
    n = PIXELS
    # 모서리를 한 칸씩 깎은 도트식 둥근 사각형: 바깥 어두운 선 + 안쪽 주황 선
    d.rectangle((1, 0, n - 2, n - 1), fill=OUTLINE)
    d.rectangle((0, 1, n - 1, n - 2), fill=OUTLINE)
    d.rectangle((2, 1, n - 3, n - 2), fill=ACCENT)
    d.rectangle((1, 2, n - 2, n - 3), fill=ACCENT)
    icon.paste(dots, (2, 2))
    return icon


def scaled(icon: Image.Image, size: int) -> Image.Image:
    # 32px 이상은 픽셀을 그대로 키우고(도트 유지), 32px보다 작은 크기만 부드럽게 줄인다
    method = Image.NEAREST if size >= PIXELS else Image.LANCZOS
    return icon.resize((size, size), method)


def main() -> None:
    icon = pixel_icon()
    scaled(icon, 256).save(OUT_PNG)
    sizes = [16, 24, 32, 48, 64, 128, 256]
    big = scaled(icon, 256)
    big.save(OUT_ICO, format="ICO", sizes=[(s, s) for s in sizes],
             append_images=[scaled(icon, s) for s in sizes if s != 256])
    print(f"저장: {OUT_PNG}, {OUT_ICO}")


if __name__ == "__main__":
    main()
