"""육성 등급 계산과 개선 가이드 생성.

에코 점수 (확률 기반)
- 서브옵션 가치 = Σ (값 / 최대 롤 값 × 우선순위 가중치). 메인옵션이 추천과 다르면 ×0.6.
- 이 가치를 '새 에코를 끝까지 강화했을 때 나올 수 있는 모든 에코' 분포와 비교해 백분위로 바꾼다.
  분포는 쿠로게임즈 공식 확률(서브옵션 13종 균등·중복 없음, 수치별 확률)로 계산한다.
- 에코 점수 = 백분위 (예: 상위 10% 에코 = 90점). 종합 점수와 교체 우선순위에 쓴다.

에코 등급 (순수 수치)
- 유효 옵션과 무관하게 % 서브옵션 각 줄이 공식 수치표 8단계(최하/중옵/상옵/최상) 중 어디인지만 본다.
- 줄 점수 = (단계+1)/8×100, 평균 → S ≥ 90, A ≥ 80, B ≥ 70, 그 아래 C (종합 등급과 동일). 고정 수치 옵션은 제외.

종합 점수
- 목표 달성도 = 100 - (부족한 수치를 서브옵션 줄 수로 환산한 값 × 12)
- 에코 항목 = 에코 평균 백분위를 50점(아무 에코 수준)→0, 95점(모두 상위 5%)→100 으로 환산
- 종합 = 목표 달성도 50% + 에코 항목 50%
- 종합 등급: S ≥ 90, A ≥ 80, B ≥ 70, 그 아래 C
- 추천 스탯을 모두 채우면 최소 90점(S), 90~100점은 에코 항목으로 나눈다
- 추천 스탯을 하나라도 못 채우면 등급은 최대 A
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from functools import lru_cache

import numpy as np

from . import stats as S
from .models import Build, Character, Echo

TIER_WEIGHTS = [1.0, 0.8, 0.6, 0.45, 0.35, 0.3]
DEFAULT_PRIORITY = [[S.CRIT_RATE, S.CRIT_DMG], [S.ATK_PCT], [S.ENERGY_REGEN]]
MAIN_MISMATCH_PENALTY = 0.6
TARGET_WEIGHT = 0.5
LINE_PENALTY = 12.0  # 목표까지 부족한 서브옵션 1줄당 감점
ECHO_ZERO = 50.0  # 에코 평균 백분위가 이 이하면 에코 항목 0점 (아무 에코나 낀 수준)
ECHO_FULL_SCORE = 95.0  # 에코 평균 백분위가 이 이상이면 에코 항목 만점
GOOD_ECHO_PERCENTILE = 92.0  # 교체 목표로 삼는 에코 수준 (상위 8%, S급)
UNMET_GRADE_CAP = "A"

GRADES = [(90, "S"), (80, "A"), (70, "B"), (0, "C")]
# 에코 등급: 유효 옵션과 무관한 '순수 수치' 점수 기준 (roll_score, 0~100)
ECHO_GRADES = GRADES  # 종합 등급과 같은 기준 (S ≥ 90, A ≥ 80, B ≥ 70, 그 아래 C)
GRADE_ORDER = ["C", "B", "A", "S"]
# 수치 단계 (공식 수치표의 8단계를 2개씩 묶음)
ROLL_TIERS = ["최하", "중옵", "상옵", "최상"]
CRIT_PAIR = {S.CRIT_RATE, S.CRIT_DMG}


def grade_of(score: float, table=GRADES) -> str:
    for threshold, g in table:
        if score >= threshold:
            return g
    return table[-1][1]


def cap_grade(grade: str, cap: str) -> str:
    return grade if GRADE_ORDER.index(grade) <= GRADE_ORDER.index(cap) else cap


def substat_weights(char: Character) -> dict[str, float]:
    priority = char.substat_priority or DEFAULT_PRIORITY
    w: dict[str, float] = {}
    for i, tier in enumerate(priority):
        tw = TIER_WEIGHTS[min(i, len(TIER_WEIGHTS) - 1)]
        for key in tier:
            w.setdefault(key, tw)
    # 유저가 직접 추가한 유효 옵션: 공략 우선순위 바로 다음 등급으로 본다
    extra_w = TIER_WEIGHTS[min(len(priority), len(TIER_WEIGHTS) - 1)]
    for key in char.extra_substats:
        if key in S.SUBSTAT_MAX:
            w.setdefault(key, extra_w)
    # 총 스탯 목표에 있지만 우선순위 문구에 없는 스탯 (예: HP 목표)
    for t in char.targets:
        key = {S.ATK: S.ATK_PCT, S.HP: S.HP_PCT, S.DEF: S.DEF_PCT}.get(t.stat, t.stat)
        if key in S.SUBSTAT_MAX:
            w.setdefault(key, TIER_WEIGHTS[min(len(priority), len(TIER_WEIGHTS) - 1)])
    for flat, pct in S.FLAT_TO_PCT.items():
        if pct in w:
            w[flat] = w[pct] * S.FLAT_VALUE_RATIO
    return w


def _ideal(weights: dict[str, float]) -> float:
    return sum(sorted((v for k, v in weights.items() if k in S.SUBSTAT_MAX), reverse=True)[:5]) or 1.0


def substat_value(stat: str, value: float, weights: dict[str, float]) -> float:
    mx = S.SUBSTAT_MAX.get(stat)
    return weights.get(stat, 0.0) * min(value / mx, 1.2) if mx else 0.0


def roll_index(stat: str, value: float) -> int | None:
    """서브옵션 수치가 공식 수치표 8단계 중 몇 번째(0~7)인지. 가장 가까운 값으로 맞춘다."""
    rolls = S.SUBSTAT_ROLLS.get(stat)
    if not rolls or stat in S.FLAT_TO_PCT:  # 고정 수치 옵션은 % 수치표에 없으므로 제외
        return None
    return min(range(len(rolls)), key=lambda i: abs(rolls[i] - value))


def roll_tier(stat: str, value: float) -> str | None:
    i = roll_index(stat, value)
    return None if i is None else ROLL_TIERS[i // 2]


def roll_score(echo: Echo) -> float | None:
    """유효 옵션과 무관하게 % 서브옵션 수치만 보고 매기는 점수 (0~100).

    각 줄 = (단계 + 1) / 8 × 100 → 최하 12.5/25, 중옵 37.5/50, 상옵 62.5/75, 최상 87.5/100. 평균을 낸다.
    """
    pts = [(i + 1) / 8 * 100 for l in echo.substats if (i := roll_index(l.stat, l.value)) is not None]
    return sum(pts) / len(pts) if pts else None


# ---------------------------------------------------------------------------
# 공식 확률로 만든 '무작위 에코' 분포
# ---------------------------------------------------------------------------
BIN = 0.05  # 분포 계산 격자 (서브옵션 가치 0~100 기준)


@lru_cache(maxsize=64)
def _distribution(weights_key: tuple[tuple[str, float], ...]) -> np.ndarray:
    """무작위 +25 에코(서브옵션 5개)의 서브옵션 가치 누적분포 (격자 BIN 단위, 0~100 정규화).

    공식 규칙: 13종 중 5종이 중복 없이 균등하게 뽑히므로 모든 5종 조합의 확률이 같다.
    종류마다 '안 뽑힘 / 뽑힘(수치 분포)'을 동적 계획법으로 합쳐 정확한 분포를 구한다.
    """
    weights = dict(weights_key)
    ideal = _ideal(weights)
    size = int(130 / BIN) + 2  # 최대치(120% 롤 여유) 넘게
    k = S.SUBSTATS_PER_ECHO
    dp = np.zeros((k + 1, size))
    dp[0, 0] = 1.0
    for stat in S.SUBSTAT_TYPES:
        w = weights.get(stat, 0.0)
        probs = np.array(S.SUBSTAT_PROBS[stat]) / sum(S.SUBSTAT_PROBS[stat])
        pmf = np.zeros(size)
        for v, p in zip(S.SUBSTAT_ROLLS[stat], probs):
            pmf[int(round(w * v / S.SUBSTAT_MAX[stat] / ideal * 100 / BIN))] += p
        new = dp.copy()  # 안 뽑힘
        for c in range(1, k + 1):
            new[c] += np.convolve(dp[c - 1], pmf)[:size]  # 뽑힘
        dp = new
    pmf_total = dp[k] / dp[k].sum()
    return np.cumsum(pmf_total)


def _dist_for(weights: dict[str, float]) -> np.ndarray:
    return _distribution(tuple(sorted(weights.items())))


def percentile(raw: float, weights: dict[str, float]) -> float:
    """raw 가치 이하인 무작위 에코의 비율 (0~100)."""
    cdf = _dist_for(weights)
    i = min(max(int(round(raw / BIN)), 0), len(cdf) - 1)
    return float(cdf[i]) * 100


def raw_at_percentile(pct: float, weights: dict[str, float]) -> float:
    cdf = _dist_for(weights)
    return float(np.searchsorted(cdf, pct / 100)) * BIN


# ---------------------------------------------------------------------------
# 결과 구조
# ---------------------------------------------------------------------------
@dataclass
class SubstatEval:
    stat: str
    value: float
    weight: float
    ratio: float  # 최대 롤 대비

    @property
    def useful(self) -> bool:
        return self.weight > 0


@dataclass
class EchoEval:
    index: int
    echo: Echo
    score: float  # 백분위 점수 (0~100, 메인옵션 감점 반영)
    raw_score: float  # 서브옵션 가치 (이론상 최고 = 100)
    grade: str
    main_ok: bool
    recommended_mains: list[str]
    substats: list[SubstatEval]
    notes: list[str] = field(default_factory=list)
    roll_score: float | None = None  # 유효 옵션과 무관한 순수 수치 점수 (등급 기준)

    @property
    def top_percent(self) -> float:
        return max(100 - self.score, 0.1)


@dataclass
class TargetEval:
    stat: str
    label: str
    target: float
    current: float | None
    op: str

    @property
    def achieved(self) -> bool:
        return self.current is not None and self.current >= self.target

    @property
    def ratio(self) -> float:
        if self.current is None or self.target <= 0:
            return 0.0
        return min(self.current / self.target, 1.0)

    @property
    def deficit(self) -> float:
        return 0.0 if self.current is None else max(self.target - self.current, 0.0)


@dataclass
class Advice:
    priority: int  # 작을수록 먼저
    title: str
    detail: str


@dataclass
class Evaluation:
    character: Character
    targets: list[TargetEval]
    echoes: list[EchoEval]
    target_score: float | None
    echo_score: float | None
    total_score: float
    grade: str
    grade_capped: bool
    missing_lines: float
    replace_order: list[EchoEval]
    advice: list[Advice]
    chain: int | None = None
    chain_estimates: list = field(default_factory=list)  # chain.ChainEstimate
    chain_gain: float | None = None  # 0돌 대비 현재 돌파의 DPS 증가율 (추정)
    chain_lines: float | None = None  # 돌파 효율을 서브옵션 줄 수로 환산한 값
    chain_score: float | None = None  # 돌파 반영 점수
    chain_grade: str | None = None


# ---------------------------------------------------------------------------
# 에코
# ---------------------------------------------------------------------------
def _main_ok(char: Character, echo: Echo) -> tuple[bool, list[str]]:
    rec = char.recommended_mains(echo.cost)
    if echo.main is None or not echo.main.stat or not rec:
        return True, sorted(rec)
    if echo.main.stat in rec:
        return True, sorted(rec)
    # 4코스트 크리티컬/크리티컬 피해는 서로 대체 가능
    if echo.cost == 4 and echo.main.stat in CRIT_PAIR and rec & CRIT_PAIR:
        return True, sorted(rec)
    return False, sorted(rec)


def evaluate_echo(char: Character, echo: Echo, index: int, weights: dict[str, float] | None = None) -> EchoEval:
    weights = weights or substat_weights(char)
    subs = [SubstatEval(l.stat, l.value, weights.get(l.stat, 0.0),
                        min(l.value / S.SUBSTAT_MAX[l.stat], 1.2) if l.stat in S.SUBSTAT_MAX else 0.0)
            for l in echo.substats]
    raw = sum(s.weight * s.ratio for s in subs) / _ideal(weights) * 100
    main_ok, rec = _main_ok(char, echo)
    effective = raw * (1.0 if main_ok else MAIN_MISMATCH_PENALTY)
    score = percentile(effective, weights)

    notes: list[str] = []
    if not main_ok and echo.main:
        notes.append(f"메인옵션이 {S.label(echo.main.stat)}입니다. 추천: {', '.join(S.label(r) for r in rec)}")
    if len(echo.substats) < 5:
        notes.append(f"서브옵션이 {len(echo.substats)}개만 열려 있습니다. 강화를 마치면 점수가 바뀔 수 있습니다")
    useless = [s for s in subs if not s.useful]
    if useless:
        notes.append("쓸모없는 서브옵션: " + ", ".join(f"{S.label(s.stat)} {S.fmt(s.stat, s.value)}" for s in useless))
    if weights.get(S.CRIT_RATE, 0) >= 0.8 and not any(s.stat in CRIT_PAIR for s in subs):
        notes.append("크리티컬 계열 서브옵션이 없습니다")
    rs = roll_score(echo)
    grade = grade_of(rs, ECHO_GRADES) if rs is not None else "-"
    return EchoEval(index, echo, score, raw, grade, main_ok, rec, subs, notes, rs)


# ---------------------------------------------------------------------------
# 목표 스탯
# ---------------------------------------------------------------------------
def _per_line(stat: str, current: float | None) -> tuple[str, float] | None:
    """목표 스탯 1줄을 올리는 서브옵션과 그 기대값."""
    if stat in S.SUBSTAT_EXPECTED:
        return stat, S.SUBSTAT_EXPECTED[stat]
    if stat in (S.ATK, S.HP, S.DEF):
        pct = {S.ATK: S.ATK_PCT, S.HP: S.HP_PCT, S.DEF: S.DEF_PCT}[stat]
        # 기본 수치를 모르므로 총 수치에서 어림잡는다: 총합 ≈ 기본 × 1.8 + 고정치 합 약 350
        cur = current or 0
        base = max((cur - 350) / 1.8, cur * 0.3, 1)
        return pct, base * S.SUBSTAT_EXPECTED[pct] / 100
    return None


def missing_lines(t: TargetEval) -> float:
    if t.current is None or t.achieved:
        return 0.0
    pl = _per_line(t.stat, t.current)
    if pl:
        return t.deficit / pl[1]
    # 속성 피해/치료 보너스 등 서브옵션에 없는 스탯은 피해 보너스 1줄(기대값)로 환산
    return t.deficit / S.SUBSTAT_EXPECTED[S.ATK_PCT]


def _target_advice(t: TargetEval, char: Character, build: Build) -> Advice | None:
    if t.current is None:
        return Advice(5, f"{t.label}: 값 없음", "총 스탯 스크린샷에서 이 값을 읽지 못했습니다. 표에 직접 입력해 주세요.")
    if t.achieved:
        return None
    ways: list[str] = []
    pl = _per_line(t.stat, t.current)
    if pl:
        sub, per = pl
        n = max(math.ceil(t.deficit / per - 1e-9), 1)
        unit = f"약 +{per:,.0f}" if t.stat in (S.ATK, S.HP, S.DEF) else f"기대값 {S.fmt(sub, per)}"
        ways.append(f"{S.label(sub)} 서브옵션 {n}줄 (1줄 {unit})")
    # 메인옵션은 공략이 그 코스트에 추천하는 옵션일 때만, 그리고 아직 그 옵션이 아닌 에코가 있을 때만 제안
    main_key = {S.ATK: S.ATK_PCT, S.HP: S.HP_PCT, S.DEF: S.DEF_PCT}.get(t.stat, t.stat)
    for cost in (4, 3, 1):
        rec = char.recommended_mains(cost)
        if main_key in rec:
            wrong = [e for e in build.echoes if e.cost == cost and e.main and e.main.stat
                     and not _main_ok(char, e)[0]]
            if wrong:
                ways.append(f"메인옵션이 추천과 다른 {cost}코스트 에코를 {S.label(main_key)} 메인옵션으로 교체")
    if t.stat in S.ELEMENT_DMG.values() and not ways:
        ways.append("추천 세트와 3코스트 속성 피해 메인옵션을 갖췄는지 확인")
    return Advice(
        1 if t.ratio < 0.9 else 2,
        f"{t.label} {S.fmt(t.stat, t.deficit)} 부족 (현재 {S.fmt(t.stat, t.current)} / 목표 {S.fmt(t.stat, t.target)})",
        "올리는 방법: " + " / ".join(ways) if ways else "",
    )


def _gear_advice(char: Character, build: Build) -> list[Advice]:
    out: list[Advice] = []
    names = [w.name for w in char.weapons]
    sig = char.signature_weapon
    rec_list = " > ".join(f"{w.name}{' (전용)' if w.signature else ''}" for w in char.weapons)
    if build.weapon and char.weapons:
        if build.weapon not in names:  # '(기타)' 포함
            out.append(Advice(2, "장착 무기가 공식 추천 무기가 아닙니다",
                              f"추천 무기: {rec_list}. 추천 스탯은 추천 무기를 기준으로 잡혀 있습니다."))
        elif sig and build.weapon != sig.name:
            out.append(Advice(6, f"전용 무기 「{sig.name}」을(를) 얻으면 더 강해집니다",
                              f"지금 무기 「{build.weapon}」도 공략 추천 목록에 있습니다."))
    chosen = [x for x in build.sonata if x]
    if chosen and char.sonata:
        rec = char.sonata_names
        wrong = [x for x in chosen if x not in rec]
        missing = [x for x in rec if x not in chosen]
        if wrong:
            out.append(Advice(2, "에코 세트가 공식 추천 세트와 다릅니다", f"추천 세트: {' + '.join(char.sonata)}"))
        elif missing:
            out.append(Advice(2, f"추천 세트 조합 중 {', '.join(missing)}이(가) 빠져 있습니다",
                              f"추천 세트: {' + '.join(char.sonata)}"))
    return out


def _chain_advice(char: Character, build: Build) -> list[Advice]:
    if build.chain is None or not char.chains:
        return []
    nxt = next((c for c in sorted(char.chains, key=lambda c: c.seq) if c.seq > build.chain and c.core), None)
    if not nxt:
        return []
    return [Advice(7, f"다음 핵심 돌파: {nxt.seq}돌 「{nxt.name}」", chain_summary(char, nxt.seq))]


def chain_summary(char: Character, seq: int, limit: int = 120) -> str:
    """공략의 돌파 추천 문구에서 'N돌: ...' 요약을 찾고, 없으면 효과 설명을 줄여서 쓴다."""
    import re
    m = re.search(rf"{seq}\s*돌\s*[:：]\s*(.+)", char.chain_text)
    if m:
        return m.group(1).strip()
    desc = next((c.description for c in char.chains if c.seq == seq), "").replace("\n", " ")
    if not desc:
        return "효과 설명은 '캐릭터 데이터 업데이트' 후에 볼 수 있습니다."
    return desc if len(desc) <= limit else desc[:limit].rstrip() + "…"


# ---------------------------------------------------------------------------
# 종합
# ---------------------------------------------------------------------------
def _echo_contribution(echo: Echo, stat: str, current_total: float, include_main: bool = True) -> float:
    """에코 하나가 총 스탯 하나에 더해 주는 양 (메인옵션 + 서브옵션)."""
    lines = ([echo.main] if echo.main and include_main else []) + list(echo.substats)
    if stat in (S.ATK, S.HP, S.DEF):
        flat = {S.ATK: S.ATK_FLAT, S.HP: S.HP_FLAT, S.DEF: S.DEF_FLAT}[stat]
        pct = S.FLAT_TO_PCT[flat]
        base = max((current_total - 350) / 1.8, current_total * 0.3, 1)
        return sum(l.value for l in lines if l.stat == flat) + sum(l.value for l in lines if l.stat == pct) * base / 100
    return sum(l.value for l in lines if l.stat == stat)


def _replacement_needs(echo: Echo, targets: list[TargetEval], main_ok: bool = True) -> str:
    """이 에코를 뺐을 때 목표를 맞추려면 새 에코가 채워야 할 스탯.

    메인옵션이 이미 추천 옵션이면 새 에코도 같은 메인옵션을 달 것이므로 서브옵션만 잃는다고 본다.
    """
    out = []
    for t in targets:
        if t.current is None:
            continue
        lost = _echo_contribution(echo, t.stat, t.current, include_main=not main_ok)
        need = t.target - (t.current - lost)
        if need > 0 and (lost > 0 or not t.achieved):
            out.append(f"{t.label} {S.fmt(t.stat, need)}")
    return ", ".join(out)


def echo_component(avg_percentile: float) -> float:
    """에코 평균 백분위 → 0~100. 아무 에코나 낀 수준(50)은 0점, 모두 상위 5%(95) 이상이면 만점."""
    return min(max((avg_percentile - ECHO_ZERO) / (ECHO_FULL_SCORE - ECHO_ZERO) * 100, 0.0), 100.0)


def combine(target_score: float | None, echo_score: float | None) -> float:
    parts: list[tuple[float, float]] = []
    if target_score is not None:
        parts.append((target_score, TARGET_WEIGHT))
    if echo_score is not None:
        parts.append((echo_component(echo_score), 1 - TARGET_WEIGHT))
    return sum(s * w for s, w in parts) / sum(w for _, w in parts) if parts else 0.0


MET_FLOOR = 90.0  # 추천 스탯을 모두 채우면 받는 최소 점수 (S)


def total_score(target_score: float | None, echo_score: float | None, all_met: bool) -> float:
    """추천 스탯을 모두 채우면 90점(S) 보장, 그 위 10점은 에코 품질로 나눈다. 아니면 combine 그대로."""
    if all_met:
        echo = echo_component(echo_score) if echo_score is not None else 100.0
        return MET_FLOOR + (100 - MET_FLOOR) * echo / 100
    return combine(target_score, echo_score)


def evaluate(char: Character, build: Build) -> Evaluation:
    weights = substat_weights(char)

    targets = [TargetEval(t.stat, t.label or S.label(t.stat), t.value, build.totals.get(t.stat), t.op)
               for t in char.targets]
    known = [t for t in targets if t.current is not None]
    lines = sum(missing_lines(t) for t in known)
    target_score = max(0.0, 100 - LINE_PENALTY * lines) if known else None

    echoes = [evaluate_echo(char, e, i, weights) for i, e in enumerate(build.echoes)]
    echo_score = sum(e.score for e in echoes) / len(echoes) if echoes else None

    unmet = [t for t in known if not t.achieved]
    all_met = bool(known) and not unmet
    total = total_score(target_score, echo_score, all_met)
    grade = grade_of(total)
    capped = bool(unmet) and cap_grade(grade, UNMET_GRADE_CAP) != grade
    if unmet:
        grade = cap_grade(grade, UNMET_GRADE_CAP)

    replace_order = sorted(echoes, key=lambda e: (e.main_ok, e.score))
    advice: list[Advice] = []
    for t in targets:
        a = _target_advice(t, char, build)
        if a:
            advice.append(a)
    for t in targets:
        if t.stat == S.ENERGY_REGEN and t.current is not None and t.current - t.target >= 15:
            advice.append(Advice(3, f"공명 효율이 목표보다 {t.current - t.target:.1f}% 높습니다",
                                 "공명 효율 서브옵션이 붙은 에코를 크리티컬/공격력 서브옵션 에코로 바꾸면 딜이 더 오릅니다."))

    good_raw = raw_at_percentile(GOOD_ECHO_PERCENTILE, weights)
    n = len(echoes)
    want_subs = "크리티컬/크리티컬 피해" if weights.get(S.CRIT_RATE, 0) >= 0.8 else \
        ", ".join(S.label(k) for k, v in sorted(weights.items(), key=lambda kv: -kv[1])[:2])
    missing_names = [t.label for t in unmet]
    good_label = f"상위 {100 - GOOD_ECHO_PERCENTILE:.0f}%"
    for rank, e in enumerate(replace_order[:3]):
        if e.score >= GOOD_ECHO_PERCENTILE and e.main_ok:
            break
        new_total = total_score(target_score, all_met=all_met, echo_score=(echo_score or 0) + max(GOOD_ECHO_PERCENTILE - e.score, 0) / n)
        beat = 100 - e.score  # 새 에코가 이보다 좋을 확률
        want_main = ", ".join(S.label(m) for m in e.recommended_mains) or "-"
        needs = _replacement_needs(e.echo, targets, e.main_ok)
        detail = (f"상위 {e.top_percent:.0f}% 수준 에코입니다. 새 에코를 끝까지 강화하면 "
                  f"약 {beat:.0f}% 확률로 이보다 좋은 에코가 나옵니다. "
                  f"메인옵션 {want_main}에 서브옵션 {want_subs}가 붙은 에코를 노리세요."
                  + (f" 이 에코의 서브옵션이 빠지므로, 목표를 채우려면 새 에코 서브옵션에 {needs}이(가) 필요합니다." if needs else "")
                  + f" {good_label} 에코(서브옵션 가치 {good_raw:.0f} 이상, 새 에코가 이 수준일 확률 "
                    f"{100 - GOOD_ECHO_PERCENTILE:.0f}%)로 바꾸면 종합 점수가 약 {total:.0f} → {new_total:.0f}점이 됩니다.")
        advice.append(Advice(0 if rank == 0 else 2 + rank,
                             f"{rank + 1}순위 교체: {e.index + 1}번 에코 "
                             f"({e.echo.cost or '?'}코스트, {e.echo.name or '이름 없음'})", detail))
    advice += _gear_advice(char, build)
    advice += _chain_advice(char, build)
    if capped:
        advice.append(Advice(1, f"추천 스탯을 모두 채우면 {grade_of(total)}등급 이상을 받을 수 있습니다",
                             f"아직 못 채운 스탯: {', '.join(missing_names)}. 추천 스탯을 하나라도 못 채우면 등급은 최대 A입니다."))
    advice.sort(key=lambda a: a.priority)
    ev = Evaluation(char, targets, echoes, target_score, echo_score, total, grade, capped, lines,
                    replace_order, advice, build.chain)
    _apply_chain(ev, char, build, weights, unmet)
    return ev


def _line_dps_gain(weights: dict[str, float], totals: dict[str, float]) -> float:
    """우선순위 1순위 서브옵션 1줄(기대값)을 더 맞췄을 때의 DPS 증가율."""
    from .chain import StatContext

    ctx = StatContext.from_totals(totals)
    top = max((k for k in weights if k in S.SUBSTAT_EXPECTED and k not in S.FLAT_TO_PCT),
              key=lambda k: weights[k], default=S.ATK_PCT)
    ev = S.SUBSTAT_EXPECTED[top] / 100
    if top == S.CRIT_DMG:
        return ctx.crit_mult(cd=ctx.crit_dmg + ev) / ctx.crit_mult() - 1
    if top == S.CRIT_RATE:
        return ctx.crit_mult(cr=ctx.crit_rate + ev) / ctx.crit_mult() - 1
    if top in (S.ATK_PCT, S.HP_PCT, S.DEF_PCT):
        return ev * ctx.atk_base_ratio
    return ev / (1 + EXISTING_DMG_BONUS_RATIO)


EXISTING_DMG_BONUS_RATIO = 0.8


def _apply_chain(ev: Evaluation, char: Character, build: Build, weights: dict[str, float], unmet: list) -> None:
    """돌파 효율: 스탯에 더하지 않고 'DPS가 몇 % 늘었나'를 추정해, 서브옵션 줄 수로 환산해 점수에 반영한다."""
    from .chain import cumulative_gain, estimate_chains

    if not char.chains:
        return
    ev.chain_estimates = estimate_chains(char, build.totals)
    if build.chain is None or not ev.chain_estimates:
        return
    gain = cumulative_gain(ev.chain_estimates, build.chain)
    per_line = _line_dps_gain(weights, build.totals)
    lines = math.log1p(gain) / math.log1p(per_line) if per_line > 0 else 0.0
    ev.chain_gain = gain
    ev.chain_lines = lines
    # 목표 달성도 1줄 = LINE_PENALTY점, 종합 점수에서는 목표 달성도 비중만큼
    ev.chain_score = min(ev.total_score + lines * LINE_PENALTY * TARGET_WEIGHT, 100.0)
    g = grade_of(ev.chain_score)
    ev.chain_grade = cap_grade(g, UNMET_GRADE_CAP) if unmet else g
