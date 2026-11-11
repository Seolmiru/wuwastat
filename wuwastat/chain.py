"""공명 체인(돌파) DPS 상승률 추정.

공식 공략의 공명 체인 효과 문구에서 수치를 읽어, 돌파할 때마다 DPS가 몇 % 늘어나는지 추정한다.
버프를 캐릭터 스탯에 더하지 않고, 현재 스탯을 넣은 데미지 공식에서 '그 효과가 있을 때 / 없을 때'의
비율만 계산한다.

데미지 ∝ 공격력 × 배율 × (1 + 피해 보너스) × (1 + 부스트) × 크리티컬 기대값 × 방어/저항 계수
- 피해 배율 X% 상승        → 해당 스킬 비중 × X%
- 크리티컬 피해 +X%        → 크리티컬 기대값 (1 + CR×(CD-1)) 의 증가율
- 크리티컬 +X%             → 위와 같은 방식 (CR 최대 100%)
- 공격력 +X%               → X% × 기본 공격력 / 총 공격력
- 피해 보너스 / 피해 +X%   → X / (100 + 기존 피해 보너스)
- 피해 X% 부스트           → X / (100 + 기존 부스트)
- 저항 X% 감소             → X / (100 - 기존 저항 10%)
- 방어력 X% 무시           → 방어 계수 증가 (적 방어 계수 약 0.5 가정)

한계 (추정치인 이유)
- 스킬별 딜 비중을 모르므로, 공략 서브옵션 우선순위에서 주력 스킬을 골라 기본 비중을 조정해 쓴다.
- 버프 유지율은 100%로, '최대 N%' 표기는 최대치로 본다.
- 다른 효과의 수치를 키우는 효과("…가 제공하는 공격력 보너스가 350% 증가")처럼 문구만으로 크기를
  알 수 없는 돌파는 공략이 꼽은 핵심 돌파면 8%, 아니면 2%로 둔다.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, field

from . import stats as S
from .models import Character

# 스킬 종류별 기본 딜 비중 (합 1.0). 주력 스킬은 공략 우선순위로 키운다.
BASE_SHARES = {
    "basic": 0.18, "heavy": 0.14, "skill": 0.18, "liberation": 0.18,
    "intro": 0.08, "echo": 0.07, "outro": 0.03, "named": 0.14,
}
SKILL_WORDS = {
    "basic": ("일반 공격", "회피 반격", "공중 공격", "평타"),
    "heavy": ("강공격",),
    "skill": ("공명 스킬",),
    "liberation": ("공명 해방",),
    "intro": ("변주 스킬",),
    "outro": ("반주 스킬",),
    "echo": ("에코 어빌리티",),
}
PRIORITY_TO_SKILL = {S.BASIC_DMG: "basic", S.HEAVY_DMG: "heavy", S.SKILL_DMG: "skill", S.LIBERATION_DMG: "liberation"}
MAIN_SKILL_BOOST = 3.0
UNKNOWN_MOVE_SHARE = 0.3  # 이름만 나온 특수 기술의 비중 (스킬 종류를 알 수 없을 때)
EXISTING_DMG_BONUS = 80.0  # 속성 피해 보너스 3코스트 2개 + 세트 등 기존 피해 보너스 (%)
EXISTING_AMPLIFY = 0.0
ENEMY_RES = 10.0
DEFAULT_CORE_GAIN = 0.08
DEFAULT_OTHER_GAIN = 0.02
MAX_SINGLE_GAIN = 1.5

NUM = r"(\d+(?:\.\d+)?)%"


@dataclass
class ChainEstimate:
    seq: int
    gain: float  # 이 돌파 하나로 늘어나는 DPS 비율 (0.12 = +12%)
    parts: list[str] = field(default_factory=list)  # 계산 근거
    estimated_default: bool = False  # 문구에서 수치를 못 읽어 기본값을 씀


def skill_shares(char: Character) -> dict[str, float]:
    shares = dict(BASE_SHARES)
    main = next((PRIORITY_TO_SKILL[k] for tier in char.substat_priority for k in tier if k in PRIORITY_TO_SKILL), None)
    if main:
        shares[main] *= MAIN_SKILL_BOOST
    total = sum(shares.values())
    return {k: v / total for k, v in shares.items()}


def _share_of(sentence: str, shares: dict[str, float], default: float) -> float:
    kinds = {k for k, words in SKILL_WORDS.items() if any(w in sentence for w in words)}
    if not kinds:
        return default
    return min(sum(shares[k] for k in kinds), 1.0)


def _max_value(sentence: str, m: re.Match) -> float:
    """'1pt 당 2% … 최대 80%' 처럼 최대치가 있으면 최대치."""
    mx = re.search(r"최대\s*" + NUM, sentence[m.start():])
    return float(mx.group(1)) if mx else float(m.group(1))


@dataclass
class StatContext:
    crit_rate: float = 0.6  # 비율
    crit_dmg: float = 2.5
    atk_base_ratio: float = 0.48  # 기본 공격력 / 총 공격력

    @classmethod
    def from_totals(cls, totals: dict[str, float]) -> "StatContext":
        cr = min(totals.get(S.CRIT_RATE, 60.0), 100.0) / 100
        cd = totals.get(S.CRIT_DMG, 250.0) / 100
        atk = totals.get(S.ATK)
        ratio = 0.48
        if atk:
            base = max((atk - 350) / 1.8, atk * 0.3, 1)
            ratio = base / atk
        return cls(cr, cd, ratio)

    def crit_mult(self, cr: float | None = None, cd: float | None = None) -> float:
        cr = min(self.crit_rate if cr is None else cr, 1.0)
        cd = self.crit_dmg if cd is None else cd
        return 1 + cr * (cd - 1)


def _sentences(text: str) -> list[str]:
    # 소수점(0.875%)은 문장 끝이 아니다
    return [s.strip() for s in re.split(r"(?<!\d)\.(?!\d)|[\n。]|(?<=다)\s", text) if s.strip()]


TRIGGER = re.compile(r"^.*?(?:발동|명중|사용|진입|등장|입힐|추가)\S*\s*시[,，]?\s*")


def _scope(sentence: str) -> str:
    """'변주 스킬 … 발동 시, 크리티컬 피해가 28% 증가' → 조건 부분을 뺀 '크리티컬 피해가 28% 증가'.

    스킬 이름이 조건에만 나오면 그 스킬에만 적용되는 효과가 아니라 일정 시간 동안의 자기 버프다.
    """
    return TRIGGER.sub("", sentence, count=1)


def estimate_one(description: str, shares: dict[str, float], ctx: StatContext) -> tuple[float, list[str]]:
    """효과 문구 하나 → (DPS 배수 - 1, 근거). 수치를 하나도 못 읽으면 (0, [])."""
    mult = 1.0
    parts: list[str] = []

    def add(gain: float, why: str) -> None:
        nonlocal mult
        if gain > 0:
            mult *= 1 + gain
            parts.append(f"{why} → +{gain * 100:.1f}%")

    sentences = _sentences(description)
    # 같은 돌파 안에서 배율 상승이 여러 번 나오면 (예: 상태별로 다른 배율) 그 스킬 비중을 나눠 갖는다
    mult_hits = sum(len(re.findall(r"배율(?:이|을|가)?\s*(?:추가로\s*)?" + NUM + r"\s*(?:상승|증가)", x))
                    for x in sentences) or 1
    for s in sentences:
        scope = _scope(s)
        if re.search(r"회복|실드|치료|스태미나|협주 에너지|공명 에너지|쿨타임", s) and not re.search(r"피해|크리티컬|공격력", s):
            continue
        if "제공하는" in s or "효과가 " in s and "배율 증가 효과" not in s:
            # 다른 효과의 수치를 키우는 문구는 크기를 알 수 없다
            continue
        for m in re.finditer(r"배율(?:이|을|가)?\s*(?:추가로\s*)?" + NUM + r"\s*(?:상승|증가)", s):
            v = _max_value(s, m)
            sh = _share_of(s, shares, UNKNOWN_MOVE_SHARE) / mult_hits
            add(v / 100 * sh, f"피해 배율 +{v:g}% (딜 비중 {sh * 100:.0f}%)")
        for m in re.finditer(r"크리티컬 피해(?:가|를|이)?\s*(?:추가로\s*)?" + NUM + r"\s*(?:증가|상승)", s):
            v = _max_value(s, m)
            sh = _share_of(scope, shares, 1.0)
            g = ctx.crit_mult(cd=ctx.crit_dmg + v / 100) / ctx.crit_mult() - 1
            add(g * sh, f"크리티컬 피해 +{v:g}% (적용 비중 {sh * 100:.0f}%)")
        for m in re.finditer(r"크리티컬(?!\s*피해)(?:이|가|을|를)?\s*(?:추가로\s*)?" + NUM + r"\s*(?:증가|상승)", s):
            v = _max_value(s, m)
            sh = _share_of(scope, shares, 1.0)
            g = ctx.crit_mult(cr=ctx.crit_rate + v / 100) / ctx.crit_mult() - 1
            add(g * sh, f"크리티컬 +{v:g}% (적용 비중 {sh * 100:.0f}%)")
        for m in re.finditer(r"공격력(?:이|가|을|를)?\s*(?:추가로\s*)?" + NUM + r"\s*(?:증가|상승)", s):
            v = _max_value(s, m)
            add(v / 100 * ctx.atk_base_ratio, f"공격력 +{v:g}%")
        for m in re.finditer(r"피해 보너스(?:가|를|이)?\s*(?:추가로\s*)?" + NUM + r"\s*(?:증가|상승)", s):
            v = _max_value(s, m)
            sh = _share_of(scope, shares, 1.0)
            add(v / (100 + EXISTING_DMG_BONUS) * sh, f"피해 보너스 +{v:g}% (적용 비중 {sh * 100:.0f}%)")
        for m in re.finditer(r"(?<!받는 )(?<!크리티컬 )피해(?:가|를|이)?\s*(?:추가로\s*)?" + NUM + r"\s*증가", s):
            if "보너스" in s[max(0, m.start() - 6):m.start() + 4]:
                continue
            v = _max_value(s, m)
            sh = _share_of(scope, shares, 1.0)
            add(v / (100 + EXISTING_DMG_BONUS) * sh, f"피해 +{v:g}% (적용 비중 {sh * 100:.0f}%)")
        for m in re.finditer(r"(?:피해(?:가|를|이)?\s*(?:추가로\s*)?)?" + NUM + r"\s*부스트", s):
            v = _max_value(s, m)
            sh = _share_of(scope, shares, 1.0)
            add(v / (100 + EXISTING_AMPLIFY) * sh, f"피해 부스트 {v:g}% (적용 비중 {sh * 100:.0f}%)")
        for m in re.finditer(r"받는 (?:\S+ )?피해(?:가|를|이)?\s*" + NUM + r"\s*증가", s):
            v = _max_value(s, m)
            add(v / 100, f"적이 받는 피해 +{v:g}%")
        for m in re.finditer(r"저항(?:을|이|를)?\s*" + NUM + r"\s*감소", s):
            v = float(m.group(1))
            add(v / (100 - ENEMY_RES), f"저항 -{v:g}%")
        for m in re.finditer(r"방어력(?:을|의|를)?\s*" + NUM + r"\s*(?:을\s*)?무시", s):
            v = float(m.group(1))
            add(0.5 * v / (100 - 0.5 * v), f"방어력 {v:g}% 무시")
    return min(mult - 1, MAX_SINGLE_GAIN), parts


def estimate_chains(char: Character, totals: dict[str, float] | None = None) -> list[ChainEstimate]:
    if not any(c.description for c in char.chains):
        return []  # 효과 문구가 없는 데이터 (저장소 기본 데이터) → 업데이트 후 계산
    shares = skill_shares(char)
    ctx = StatContext.from_totals(totals or {})
    out = []
    for c in sorted(char.chains, key=lambda c: c.seq):
        gain, parts = estimate_one(c.description, shares, ctx)
        default = not parts
        if default:
            gain = DEFAULT_CORE_GAIN if c.core else DEFAULT_OTHER_GAIN
            parts = [f"효과 크기를 문구로 알 수 없어 {'핵심 돌파' if c.core else '일반 돌파'} 기본값 사용"]
        out.append(ChainEstimate(c.seq, gain, parts, default))
    return out


def cumulative_gain(estimates: list[ChainEstimate], upto: int) -> float:
    """0돌 대비 upto돌의 DPS 증가율."""
    return math.prod(1 + e.gain for e in estimates if e.seq <= upto) - 1
