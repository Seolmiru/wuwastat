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


ECHOES = {
    # 파일: (이름, 코스트, 메인옵션, 서브옵션 5개)
    "echo_corosaurus.png": ("코로사우루스", 3, (S.FUSION_DMG, 30.0),
                            [(S.HEAVY_DMG, 7.9), (S.CRIT_RATE, 6.3), (S.CRIT_DMG, 12.6), (S.DEF_FLAT, 40), (S.HP_FLAT, 390)]),
    "echo_leviathan.png": ("공명의 메아리 · 영식 · 레비아탄", 4, (S.CRIT_RATE, 22.0),
                           [(S.ENERGY_REGEN, 8.4), (S.HEAVY_DMG, 7.1), (S.DEF_FLAT, 40), (S.CRIT_RATE, 7.5), (S.ATK_FLAT, 40)]),
    "echo_chameleon.png": ("악몽 · 그린멜팅카멜레온", 3, (S.FUSION_DMG, 30.0),
                           [(S.CRIT_DMG, 13.8), (S.SKILL_DMG, 10.9), (S.DEF_FLAT, 50), (S.CRIT_RATE, 7.5), (S.ENERGY_REGEN, 11.6)]),
    "echo_chameleon_fluid.png": ("악몽 · 그린멜팅카멜레온(유체)", 1, (S.ATK_PCT, 18.0),
                                 [(S.ATK_PCT, 10.9), (S.CRIT_RATE, 9.3), (S.CRIT_DMG, 12.6), (S.LIBERATION_DMG, 10.1), (S.HP_FLAT, 360)]),
    "echo_drake.png": ("드레이크 · 전도", 1, (S.ATK_PCT, 18.0),
                       [(S.CRIT_DMG, 13.8), (S.ENERGY_REGEN, 9.2), (S.DEF_PCT, 10.0), (S.CRIT_RATE, 6.3), (S.BASIC_DMG, 9.4)]),
}


@pytest.mark.parametrize("fname", sorted(ECHOES))
def test_echo_screens(fname):
    """에코 상세 화면: 메인옵션 1개 + 서브옵션 5개만 (고정 부옵션, 이름, 레벨 줄 제외)."""
    from wuwastat.parser import parse_echo

    name, cost, main, subs = ECHOES[fname]
    e = parse_echo(FIXTURES / fname)
    assert e.name == name
    assert e.cost == cost
    assert e.level == 25
    assert (e.main.stat, e.main.value) == main
    assert [(s.stat, s.value) for s in e.substats] == subs
