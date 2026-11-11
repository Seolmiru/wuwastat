"""스탯 정의: 내부 키, 한국어 표기, 에코 옵션 수치 범위.

수치는 5성 에코 +25 기준이다.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

# ---------------------------------------------------------------------------
# 스탯 키
# ---------------------------------------------------------------------------
CRIT_RATE = "crit_rate"
CRIT_DMG = "crit_dmg"
ATK = "atk"  # 총 공격력 (캐릭터 스탯 화면)
ATK_PCT = "atk_pct"
ATK_FLAT = "atk_flat"
HP = "hp"
HP_PCT = "hp_pct"
HP_FLAT = "hp_flat"
DEF = "def"
DEF_PCT = "def_pct"
DEF_FLAT = "def_flat"
ENERGY_REGEN = "energy_regen"
HEALING = "healing"
BASIC_DMG = "basic_dmg"
HEAVY_DMG = "heavy_dmg"
SKILL_DMG = "skill_dmg"
LIBERATION_DMG = "liberation_dmg"
GLACIO_DMG = "glacio_dmg"
FUSION_DMG = "fusion_dmg"
ELECTRO_DMG = "electro_dmg"
AERO_DMG = "aero_dmg"
SPECTRO_DMG = "spectro_dmg"
HAVOC_DMG = "havoc_dmg"

ELEMENT_DMG = {
    "glacio": GLACIO_DMG,
    "fusion": FUSION_DMG,
    "electro": ELECTRO_DMG,
    "aero": AERO_DMG,
    "spectro": SPECTRO_DMG,
    "havoc": HAVOC_DMG,
}
# 공식 공략 API의 element.gbId
ELEMENT_BY_GBID = {"1": "glacio", "2": "fusion", "3": "electro", "4": "aero", "5": "spectro", "6": "havoc"}
ELEMENT_KO = {"glacio": "응결", "fusion": "용융", "electro": "전도", "aero": "기류", "spectro": "회절", "havoc": "인멸"}

LABEL_KO = {
    CRIT_RATE: "크리티컬",
    CRIT_DMG: "크리티컬 피해",
    ATK: "공격력",
    ATK_PCT: "공격력%",
    ATK_FLAT: "공격력",
    HP: "HP",
    HP_PCT: "HP%",
    HP_FLAT: "HP",
    DEF: "방어력",
    DEF_PCT: "방어력%",
    DEF_FLAT: "방어력",
    ENERGY_REGEN: "공명 효율",
    HEALING: "치료 효과 보너스",
    BASIC_DMG: "일반 공격 피해 보너스",
    HEAVY_DMG: "강공격 피해 보너스",
    SKILL_DMG: "공명 스킬 피해 보너스",
    LIBERATION_DMG: "공명 해방 피해 보너스",
    **{v: f"{ELEMENT_KO[k]} 피해 보너스" for k, v in ELEMENT_DMG.items()},
}

PERCENT_STATS = {
    CRIT_RATE, CRIT_DMG, ATK_PCT, HP_PCT, DEF_PCT, ENERGY_REGEN, HEALING,
    BASIC_DMG, HEAVY_DMG, SKILL_DMG, LIBERATION_DMG, *ELEMENT_DMG.values(),
}

# 총 스탯 화면에서 쓰는 키 (에코 옵션 키와 구분)
TOTAL_STAT_KEYS = [
    HP, ATK, DEF, ENERGY_REGEN, CRIT_RATE, CRIT_DMG, HEALING,
    *ELEMENT_DMG.values(), BASIC_DMG, HEAVY_DMG, SKILL_DMG, LIBERATION_DMG,
]


def label(key: str) -> str:
    return LABEL_KO.get(key, key)


def fmt(key: str, value: float) -> str:
    if key in PERCENT_STATS:
        return f"{value:.1f}%"
    return f"{value:,.0f}"


# ---------------------------------------------------------------------------
# 에코 서브옵션 수치 (5성 +25). 각 단계 값은 게임 내 고정 롤 값이다.
# ---------------------------------------------------------------------------
SUBSTAT_ROLLS: dict[str, list[float]] = {
    CRIT_RATE: [6.3, 6.9, 7.5, 8.1, 8.7, 9.3, 9.9, 10.5],
    CRIT_DMG: [12.6, 13.8, 15.0, 16.2, 17.4, 18.6, 19.8, 21.0],
    ATK_PCT: [6.4, 7.1, 7.9, 8.6, 9.4, 10.1, 10.9, 11.6],
    HP_PCT: [6.4, 7.1, 7.9, 8.6, 9.4, 10.1, 10.9, 11.6],
    DEF_PCT: [8.1, 9.0, 10.0, 10.9, 11.8, 12.8, 13.8, 14.7],
    ENERGY_REGEN: [6.8, 7.6, 8.4, 9.2, 10.0, 10.8, 11.6, 12.4],
    BASIC_DMG: [6.4, 7.1, 7.9, 8.6, 9.4, 10.1, 10.9, 11.6],
    HEAVY_DMG: [6.4, 7.1, 7.9, 8.6, 9.4, 10.1, 10.9, 11.6],
    SKILL_DMG: [6.4, 7.1, 7.9, 8.6, 9.4, 10.1, 10.9, 11.6],
    LIBERATION_DMG: [6.4, 7.1, 7.9, 8.6, 9.4, 10.1, 10.9, 11.6],
    ATK_FLAT: [30, 40, 50, 60],
    HP_FLAT: [320, 360, 390, 430, 470, 510, 540, 580],
    DEF_FLAT: [40, 50, 60, 70],
}
SUBSTAT_MAX = {k: v[-1] for k, v in SUBSTAT_ROLLS.items()}

# 수치별 출현 확률(%). 출처: 쿠로게임즈 한국 확률 공시
# https://wutheringwaves.kurogames.com/p/language_ko/product_info.html (2. 주파수 조정기 및 튜너 확률 공시)
# - 서브옵션 13종은 각각 7.6923%로 동일하게 나오고, 한 에코 안에서 같은 종류는 중복되지 않는다.
_PROB_8 = [6.7961, 7.7670, 20.3883, 24.2718, 17.4757, 14.5631, 5.8252, 2.9126]
_PROB_CRIT = [23.3333, 23.3333, 23.3333, 8.0, 8.0, 8.0, 3.0, 3.0]
SUBSTAT_PROBS: dict[str, list[float]] = {
    CRIT_RATE: _PROB_CRIT,
    CRIT_DMG: _PROB_CRIT,
    ATK_FLAT: [6.7961, 52.4272, 37.8641, 2.9126],
    DEF_FLAT: [14.5631, 44.6602, 32.0388, 8.7379],
    **{k: _PROB_8 for k in (ATK_PCT, HP_PCT, DEF_PCT, ENERGY_REGEN, HP_FLAT,
                            BASIC_DMG, HEAVY_DMG, SKILL_DMG, LIBERATION_DMG)},
}
SUBSTAT_TYPES = list(SUBSTAT_ROLLS)  # 13종
SUBSTATS_PER_ECHO = 5
# 1줄의 기대값 (확률 가중 평균)
SUBSTAT_EXPECTED = {
    k: sum(v * p for v, p in zip(SUBSTAT_ROLLS[k], SUBSTAT_PROBS[k])) / sum(SUBSTAT_PROBS[k])
    for k in SUBSTAT_ROLLS
}
SUBSTAT_AVG = SUBSTAT_EXPECTED  # 예전 이름 호환

# 메인옵션 최대치 (+25)
MAIN_STAT_MAX: dict[int, dict[str, float]] = {
    4: {CRIT_RATE: 22.0, CRIT_DMG: 44.0, ATK_PCT: 33.0, HP_PCT: 33.0, DEF_PCT: 41.8, HEALING: 26.4},
    3: {ATK_PCT: 30.0, HP_PCT: 30.0, DEF_PCT: 38.0, ENERGY_REGEN: 32.0,
        **{v: 30.0 for v in ELEMENT_DMG.values()}},
    1: {ATK_PCT: 18.0, HP_PCT: 22.8, DEF_PCT: 18.0},
}
# 코스트별 고정 부옵션 (+25)
SECONDARY_MAIN = {4: (ATK_FLAT, 150.0), 3: (ATK_FLAT, 100.0), 1: (HP_FLAT, 2280.0)}

# 평 수치 옵션을 같은 계열 % 옵션 대비 몇 배 가치로 볼지
FLAT_TO_PCT = {ATK_FLAT: ATK_PCT, HP_FLAT: HP_PCT, DEF_FLAT: DEF_PCT}
FLAT_VALUE_RATIO = 0.5


# ---------------------------------------------------------------------------
# 텍스트 → 스탯 키 매핑
# ---------------------------------------------------------------------------
_ELEMENT_WORDS = {
    "응결": GLACIO_DMG, "용융": FUSION_DMG, "전도": ELECTRO_DMG,
    "기류": AERO_DMG, "회절": SPECTRO_DMG, "인멸": HAVOC_DMG,
}


def _norm(text: str) -> str:
    return re.sub(r"[\s·.:：]", "", text)


def classify_stat_name(text: str, *, percent: bool | None = None, prev: str | None = None) -> str | None:
    """한국어 스탯 이름(축약어, 오타 포함)을 스탯 키로 바꾼다.

    percent: 값에 %가 붙었는지 여부. 공격력/HP/방어력의 % 옵션과 고정 수치를 구분할 때 쓴다.
    prev: 바로 앞 토큰의 키. "크리티컬 확률/피해"처럼 앞 단어가 생략된 경우를 처리한다.
    """
    t = _norm(text)
    if not t:
        return None
    upper = t.upper()
    if "크리티컬피해" in t or "크피" in t or "치명타피해" in t:
        return CRIT_DMG
    if ("크리" in t or "치명" in t) and "피해" in t:  # '크리들犀 피해'처럼 가운데 글자를 잘못 읽은 경우
        return CRIT_DMG
    if t in ("피해", "피해량") and prev in (CRIT_RATE, CRIT_DMG):
        return CRIT_DMG
    if "크리티컬" in t or "크확" in t or "크리" in t or "치명타" in t:
        return CRIT_RATE
    if "공명효율" in t or "공효" in t:
        return ENERGY_REGEN
    if "치료" in t or "힐" in t:
        return HEALING
    for word, key in _ELEMENT_WORDS.items():
        if word in t:
            return key
    if "공명해방" in t or t.startswith("해방"):
        return LIBERATION_DMG
    if "공명스킬" in t or t.startswith("스킬"):
        return SKILL_DMG
    if "강공격" in t or "강공" in t:
        return HEAVY_DMG
    if "일반공격" in t or "평타" in t or "일공" in t:
        return BASIC_DMG
    is_pct = percent if percent is not None else ("%" in t or "백분" in t or "퍼" in t)
    if "HP" in upper or "체력" in t or "생명" in t:
        return HP_PCT if is_pct else HP_FLAT
    if "방어" in t or "방퍼" in t:
        return DEF_PCT if is_pct else DEF_FLAT
    if "공격" in t or "공퍼" in t:
        return ATK_PCT if is_pct else ATK_FLAT
    return None


def to_total_key(key: str) -> str:
    """에코 옵션 키를 총 스탯 화면 키로 바꾼다 (공격력 고정치 → 총 공격력)."""
    return {ATK_FLAT: ATK, HP_FLAT: HP, DEF_FLAT: DEF}.get(key, key)
