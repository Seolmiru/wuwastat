"""테스트용 합성 스크린샷 생성 (게임 UI처럼 어두운 배경 + 밝은 글자)."""
import sys
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont

FONT = "C:/Windows/Fonts/malgunbd.ttf"

def render(rows, path, size=(900, 60 + 46 * 12)):
    img = Image.new("RGB", size, (22, 24, 30))
    d = ImageDraw.Draw(img)
    f = ImageFont.truetype(FONT, 24)
    y = 30
    for left, right in rows:
        d.text((40, y), left, font=f, fill=(225, 225, 225))
        if right:
            w = d.textlength(right, font=f)
            d.text((size[0] - 40 - w, y), right, font=f, fill=(240, 220, 160))
        y += 46
    img.save(path)

out = Path(sys.argv[1]); out.mkdir(parents=True, exist_ok=True)
render([("악몽 · 애곡하는 아익스", "+25"), ("COST 4", None), ("크리티컬 피해", "44.0%"), ("공격력", "150"),
        ("크리티컬", "8.1%"), ("크리티컬 피해", "16.2%"), ("공격력", "8.6%"), ("공명 효율", "9.2%"), ("공격력", "40"),
        ("영원의 광채", None), ("회절 피해가 10% 증가된다", None)], out / "echo1.png")
render([("HP", "13,245"), ("공격력", "2,105"), ("방어력", "1,120"), ("공명 효율", "118.4%"),
        ("크리티컬", "63.2%"), ("크리티컬 피해", "231.0%"), ("회절 피해 보너스", "70.0%"),
        ("강공격 피해 보너스", "12.0%"), ("치료 효과 보너스", "0.0%")], out / "totals.png")
print("ok")
