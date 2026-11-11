"""실제 게임 스크린샷 OCR 회귀 테스트 (Windows OCR이 있는 환경에서만 실행)."""

from pathlib import Path

import pytest

from wuwastat import stats as S

FIXTURES = Path(__file__).parent / "fixtures"

try:
    from wuwastat.ocr import available_languages
    HAS_OCR = "ko" in [l.split("-")[0] for l in available_languages()]
except Exception:
    HAS_OCR = False

pytestmark = pytest.mark.skipif(not HAS_OCR, reason="Windows 한국어 OCR 없음")


def test_totals_galbrena_busy_background():
    """숫자 뒤에 밝은 붓 자국이 겹친 속성 상세 화면 (갈브레나)."""
    from wuwastat.parser import parse_totals

    totals = parse_totals(FIXTURES / "totals_galbrena.png")
    assert totals == {
        S.HP: 15610, S.ATK: 2182, S.DEF: 1353,
        S.ENERGY_REGEN: 129.2, S.CRIT_RATE: 63.9, S.CRIT_DMG: 267.4,
    }
