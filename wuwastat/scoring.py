"""육성 등급 계산과 개선 가이드 생성.

점수 구성
- 목표 스탯 달성도 (0~100): 공식 공략의 추천 스탯 대비 현재 총 스탯 비율의 평균
- 에코 점수 (0~100): 에코별 서브옵션 점수. 서브옵션 값 / 최대 롤 값 × 우선순위 가중치를 더하고,
  이론상 최고치(우선순위 상위 5개 옵션이 모두 최대 롤)로 나눈다. 메인옵션이 추천과 다르면 감점.
- 종합 점수 = 목표 달성도 50% + 에코 점수(SS급 에코 기준으로 환산) 50%
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

from . import stats as S
from .models import Build, Character, Echo

TIER_WEIGHTS = [1.0, 0.8, 0.6, 0.45, 0.35, 0.3]
DEFAULT_PRIORITY = [[S.CRIT_RATE, S.CRIT_DMG], [S.ATK_PCT], [S.ENERGY_REGEN]]
MAIN_MISMATCH_PENALTY = 0.6
ECHO_BENCHMARK = 70.0  # 이 점수 이상인 에코만 있으면 에코 항목 만점
TARGET_WEIGHT = 0.5

GRADES = [(95, "SSS"), (88, "SS"), (80, "S"), (70, "A"), (60, "B"), (45, "C"), (0, "D")]
ECHO_GRADES = [(80, "SSS"), (70, "SS"), (60, "S"), (50, "A"), (40, "B"), (30, "C"), (0, "D")]
CRIT_PAIR = {S.CRIT_RATE, S.CRIT_DMG}


def grade_of(score: float, table=GRADES) -> str:
    for threshold, g in table:
        if score >= threshold:
            return g
    return table[-1][1]


def substat_weights(char: Character) -> dict[str, float]:
    priority = char.substat_priority or DEFAULT_PRIORITY
    w: dict[str, float] = {}
    for i, tier in enumerate(priority):
        tw = TIER_WEIGHTS[min(i, len(TIER_WEIGHTS) - 1)]
        for key in tier:
            w.setdefault(key, tw)
    # 총 스탯 목표에 있지만 우선순위 문구에 없는 스탯 (예: HP 목표)
    for t in char.targets:
        key = {S.ATK: S.ATK_PCT, S.HP: S.HP_PCT, S.DEF: S.DEF_PCT}.get(t.stat, t.stat)
        if key in S.SUBSTAT_MAX:
            w.setdefault(key, TIER_WEIGHTS[min(len(priority), len(TIER_WEIGHTS) - 1)])
    for flat, pct in S.FLAT_TO_PCT.items():
        if pct in w:
            w[flat] = w[pct] * S.FLAT_VALUE_RATIO
    return w


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
    score: float  # 감점 반영 최종 점수 (0~100)
    raw_score: float
    grade: str
    main_ok: bool
    recommended_mains: list[str]
    substats: list[SubstatEval]
    notes: list[str] = field(default_factory=list)


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
    replace_order: list[EchoEval]
    advice: list[Advice]


# ---------------------------------------------------------------------------
# 계산
# ---------------------------------------------------------------------------
def _main_ok(char: Character, echo: Echo) -> tuple[bool, list[str]]:
    rec = char.recommended_mains(echo.cost)
    if echo.main is None:
        return True, sorted(rec)
    if not rec:
        return True, []
    if echo.main.stat in rec:
        return True, sorted(rec)
    # 4코스트 크리티컬/크리티컬 피해는 서로 대체 가능
    if echo.cost == 4 and echo.main.stat in CRIT_PAIR and rec & CRIT_PAIR:
        return True, sorted(rec)
    return False, sorted(rec)


def evaluate_echo(char: Character, echo: Echo, index: int, weights: dict[str, float] | None = None) -> EchoEval:
    weights = weights or substat_weights(char)
    subs: list[SubstatEval] = []
    total = 0.0
    for line in echo.substats:
        mx = S.SUBSTAT_MAX.get(line.stat)
        w = weights.get(line.stat, 0.0)
        ratio = min(line.value / mx, 1.2) if mx else 0.0
        subs.append(SubstatEval(line.stat, line.value, w, ratio))
        total += w * ratio

    ideal = sum(sorted((v for k, v in weights.items() if k in S.SUBSTAT_MAX), reverse=True)[:5]) or 1.0
    raw = total / ideal * 100
    main_ok, rec = _main_ok(char, echo)
    score = raw * (1.0 if main_ok else MAIN_MISMATCH_PENALTY)

    notes: list[str] = []
    if not main_ok and echo.main:
        notes.append(
            f"메인옵션이 {S.label(echo.main.stat)}입니다. 추천: {', '.join(S.label(r) for r in rec)}"
        )
    useless = [s for s in subs if not s.useful]
    useful = [s for s in subs if s.useful]
    if len(echo.substats) < 5:
        notes.append(f"서브옵션이 {len(echo.substats)}개만 열려 있습니다. 강화를 마치면 점수가 바뀔 수 있습니다")
    if useless:
        notes.append("쓸모없는 서브옵션: " + ", ".join(f"{S.label(s.stat)} {S.fmt(s.stat, s.value)}" for s in useless))
    crit_lines = [s for s in subs if s.stat in CRIT_PAIR]
    if weights.get(S.CRIT_RATE, 0) >= 0.8 and not crit_lines:
        notes.append("크리티컬 계열 서브옵션이 하나도 없습니다")
    elif len(useful) >= 4:
        notes.append(f"유효 서브옵션 {len(useful)}개로 좋은 에코입니다")

    return EchoEval(index, echo, score, raw, grade_of(score, ECHO_GRADES), main_ok, rec, subs, notes)


def _lines_needed(stat: str, deficit: float) -> str | None:
    avg = S.SUBSTAT_AVG.get(stat)
    if not avg or deficit <= 0:
        return None
    n = math.ceil(deficit / avg)
    return f"{S.label(stat)} 서브옵션 약 {n}줄 (평균 {S.fmt(stat, avg)} 기준)"


def _target_advice(t: TargetEval, char: Character) -> Advice | None:
    if t.current is None:
        return Advice(5, f"{t.label}: 값 없음", "총 스탯 스크린샷에서 이 값을 읽지 못했습니다. 표에 직접 입력해 주세요.")
    if t.achieved:
        return None
    d = t.deficit
    ways: list[str] = []
    if t.stat in (S.ATK, S.HP, S.DEF):
        pct = {S.ATK: S.ATK_PCT, S.HP: S.HP_PCT, S.DEF: S.DEF_PCT}[t.stat]
        # 기본 수치를 알 수 없어 '% 서브옵션 1줄 ≈ 현재 수치의 약 5%'로 어림잡는다.
        per_line = max((t.current or 0) * S.SUBSTAT_AVG[pct] / 100 * 0.55, 1)
        ways.append(f"{S.label(pct)} 서브옵션 약 {math.ceil(d / per_line)}줄 (1줄당 약 +{per_line:,.0f}로 어림)")
        ways.append(f"1코스트 메인옵션을 {S.label(pct)}로 맞추기")
    elif t.stat in S.SUBSTAT_AVG:
        ways.append(_lines_needed(t.stat, d) or "")
        if t.stat in CRIT_PAIR:
            mx = S.MAIN_STAT_MAX[4][t.stat]
            ways.append(f"4코스트 메인옵션 {S.label(t.stat)} ({mx:.0f}%)")
        if t.stat == S.ENERGY_REGEN:
            ways.append(f"3코스트 메인옵션 공명 효율 ({S.MAIN_STAT_MAX[3][S.ENERGY_REGEN]:.0f}%) 또는 공명 효율 무기/세트")
    elif t.stat in S.ELEMENT_DMG.values():
        n = math.ceil(d / 30.0)
        ways.append(f"3코스트 메인옵션 {S.label(t.stat)} (30%) {n}개, 또는 해당 속성 세트 효과")
    elif t.stat == S.HEALING:
        ways.append("4코스트 메인옵션 치료 효과 보너스 (26.4%)")
    ways = [w for w in ways if w]
    return Advice(
        1 if t.ratio < 0.85 else 2,
        f"{t.label} {S.fmt(t.stat, d)} 부족 (현재 {S.fmt(t.stat, t.current or 0)} / 목표 {S.fmt(t.stat, t.target)})",
        "올리는 방법: " + " / ".join(ways) if ways else "",
    )


def combine(target_score: float | None, echo_score: float | None) -> float:
    parts: list[tuple[float, float]] = []
    if target_score is not None:
        parts.append((target_score, TARGET_WEIGHT))
    if echo_score is not None:
        parts.append((min(echo_score / ECHO_BENCHMARK * 100, 100.0), 1 - TARGET_WEIGHT))
    return sum(s * w for s, w in parts) / sum(w for _, w in parts) if parts else 0.0


def evaluate(char: Character, build: Build) -> Evaluation:
    weights = substat_weights(char)

    targets = [TargetEval(t.stat, t.label or S.label(t.stat), t.value, build.totals.get(t.stat), t.op)
               for t in char.targets]
    known = [t for t in targets if t.current is not None]
    target_score = sum(t.ratio for t in known) / len(known) * 100 if known else None

    echoes = [evaluate_echo(char, e, i, weights) for i, e in enumerate(build.echoes)]
    echo_score = sum(e.score for e in echoes) / len(echoes) if echoes else None

    total = combine(target_score, echo_score)

    # 교체 우선순위: 메인옵션 불일치 → 점수 낮은 순
    replace_order = sorted(echoes, key=lambda e: (e.main_ok, e.score))

    advice: list[Advice] = []
    for t in targets:
        a = _target_advice(t, char)
        if a:
            advice.append(a)

    # 공명 효율이 목표를 크게 넘으면 낭비
    for t in targets:
        if t.stat == S.ENERGY_REGEN and t.current is not None and t.current - t.target >= 15:
            advice.append(Advice(
                3, f"공명 효율이 목표보다 {t.current - t.target:.1f}% 높습니다",
                "공명 효율 서브옵션이나 메인옵션 일부를 크리티컬/공격력 옵션으로 바꾸면 딜이 더 오릅니다.",
            ))

    n = len(echoes)
    for rank, e in enumerate(replace_order[:3]):
        if e.score >= ECHO_BENCHMARK and e.main_ok:
            break
        gain = max(ECHO_BENCHMARK - e.score, 0) / n if n else 0
        new_total = combine(target_score, (echo_score or 0) + gain)
        cost_label = f"{e.echo.cost}코스트" if e.echo.cost else "코스트 미상"
        want_main = ", ".join(S.label(m) for m in e.recommended_mains) or "-"
        missing = [t.label for t in targets if not t.achieved and t.current is not None]
        want_subs = "크리티컬/크리티컬 피해" if weights.get(S.CRIT_RATE, 0) >= 0.8 else \
            ", ".join(S.label(k) for k, v in sorted(weights.items(), key=lambda kv: -kv[1])[:2])
        detail = (f"{e.score:.0f}점 ({e.grade}) 에코입니다. "
                  f"메인옵션 {want_main}, 서브옵션 {want_subs} 위주의 에코로 바꾸세요."
                  + (f" 부족한 {', '.join(missing)}을(를) 함께 채우면 더 좋습니다." if missing else "")
                  + f" 70점 에코로 바꾸면 종합 점수가 약 {total:.0f} → {new_total:.0f}점이 됩니다.")
        advice.append(Advice(0 if rank == 0 else 2 + rank,
                             f"{rank + 1}순위 교체: {e.index + 1}번 에코 ({cost_label}, {e.echo.name or '이름 없음'})",
                             detail))

    advice.sort(key=lambda a: a.priority)
    return Evaluation(char, targets, echoes, target_score, echo_score, total, grade_of(total), replace_order, advice)
