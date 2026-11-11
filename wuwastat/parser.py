"""OCR 결과(단어 + 위치)를 캐릭터 총 스탯 / 에코 옵션으로 해석한다.

OCR은 전처리 방식에 따라 틀리는 글자가 달라서, 여러 방식으로 읽은 결과를 줄(y 위치) 단위로 합친다.
이름을 못 읽은 줄도 숫자가 있으면 stat="" 으로 남겨서 사용자가 화면에서 고를 수 있게 한다.
"""

from __future__ import annotations

import re
import statistics
import unicodedata
from collections import Counter
from dataclasses import dataclass
from difflib import SequenceMatcher
from pathlib import Path
from typing import Callable

from PIL import Image

from . import stats as S
from .models import Echo, StatLine
from .ocr import VARIANTS, Word, recognize, recognize_tiled

NUM_RE = re.compile(r"(\d[\d,]*(?:\.\d+)?)\s*(%?)")
MAX_LABEL_LEN = 14  # 스킬 설명처럼 긴 문장은 스탯 줄이 아니다
UNKNOWN = ""


@dataclass
class Row:
    words: list[Word]

    @property
    def text(self) -> str:
        return " ".join(w.text for w in self.words)

    @property
    def y(self) -> float:
        return statistics.mean(w.cy for w in self.words)


@dataclass
class ParsedLine:
    stat: str  # UNKNOWN 이면 이름을 못 읽은 줄
    value: float
    label: str
    y: float
    percent: bool = False
    fuzzy: bool = False


def group_rows(words: list[Word]) -> list[Row]:
    """같은 높이에 있는 단어를 한 줄로 묶는다 (이름과 값이 멀리 떨어져 있어도)."""
    if not words:
        return []
    tol = statistics.median(w.h for w in words) * 0.6
    rows: list[list[Word]] = []
    for w in sorted(words, key=lambda w: w.cy):
        if rows and abs(statistics.mean(x.cy for x in rows[-1]) - w.cy) <= tol:
            rows[-1].append(w)
        else:
            rows.append([w])
    return [Row(sorted(r, key=lambda w: w.x)) for r in rows]


def _fix_ocr_text(t: str) -> str:
    t = t.replace("％", "%").replace("，", ",").replace("。", ".").replace("•", "·").replace("十", "+")
    t = re.sub(r"(?<=\d)[oO](?=[\d.%])", "0", t)
    t = re.sub(r"(?<=\d)\s+(?=\.\d)", "", t)
    t = re.sub(r"(?<=\d\.)\s+(?=\d)", "", t)
    t = re.sub(r"(?<=\d)\s+%", "%", t)
    return t


# ---------------------------------------------------------------------------
# 퍼지 매칭 (자모 단위 유사도)
# ---------------------------------------------------------------------------
_FUZZY_LABELS = [
    ("크리티컬 피해", S.CRIT_DMG), ("크리티컬", S.CRIT_RATE), ("공격력", S.ATK_FLAT), ("방어력", S.DEF_FLAT),
    ("HP", S.HP_FLAT), ("공명 효율", S.ENERGY_REGEN), ("치료 효과 보너스", S.HEALING),
    ("일반 공격 피해 보너스", S.BASIC_DMG), ("강공격 피해 보너스", S.HEAVY_DMG),
    ("공명 스킬 피해 보너스", S.SKILL_DMG), ("공명 해방 피해 보너스", S.LIBERATION_DMG),
    *[(S.label(v), v) for v in S.ELEMENT_DMG.values()],
]


def _jamo(s: str) -> str:
    s = re.sub(r"[\s\d.,%+·:]", "", s)
    return unicodedata.normalize("NFD", s)


def classify_fuzzy(label: str, percent: bool) -> str | None:
    target = _jamo(label)
    if len(target) < 2:
        return None
    best, best_key = 0.0, None
    for name, key in _FUZZY_LABELS:
        r = SequenceMatcher(None, target, _jamo(name)).ratio()
        if r > best:
            best, best_key = r, key
    if best < 0.6 or best_key is None:
        return None
    if percent and best_key in S.FLAT_TO_PCT:
        return S.FLAT_TO_PCT[best_key]
    return best_key


# ---------------------------------------------------------------------------
# 한 줄 해석
# ---------------------------------------------------------------------------
def parse_line(text: str, y: float = 0.0) -> ParsedLine | None:
    text = _fix_ocr_text(text)
    nums = list(NUM_RE.finditer(text))
    if not nums:
        return None
    first = nums[0]
    raw_label = text[: first.start()]
    label = raw_label.strip(" :：+·-")
    if len(re.sub(r"\s", "", label)) > MAX_LABEL_LEN:
        return None
    if re.search(r"COST|코스트|Lv", label, re.I):
        return None
    # 에코 강화 레벨 '+25' (숫자 하나 앞에 +)
    if len(nums) == 1 and raw_label.rstrip().endswith("+") and not first.group(2):
        return None
    rest = text[first.start():]
    # 숫자 뒤에 글자가 이어지면 문장이다 ('회절 피해가 10% 증가된다')
    if re.search(r"[가-힣A-Za-z]{2,}", NUM_RE.sub("", rest)):
        return None
    values = [float(m.group(1).replace(",", "")) for m in nums]
    is_pct = any(m.group(2) for m in nums)
    # '1,000 + 1,200' (기본 + 추가) 형식이면 더한다
    value = sum(values) if "+" in rest and len(values) > 1 else values[-1]
    stat = S.classify_stat_name(label, percent=is_pct) if label else None
    fuzzy = False
    if stat is None and label:
        stat = classify_fuzzy(label, is_pct)
        fuzzy = stat is not None
    return ParsedLine(stat or UNKNOWN, value, label, y, is_pct, fuzzy)


def parse_lines(words: list[Word]) -> list[ParsedLine]:
    return [p for row in group_rows(words) if (p := parse_line(row.text, row.y))]


def merge_passes(passes: list[list[ParsedLine]], tol: float) -> list[ParsedLine]:
    """여러 OCR 결과를 y 위치로 묶고, 줄마다 가장 믿을 만한 해석을 고른다."""
    clusters: list[list[ParsedLine]] = []
    for lines in passes:
        for p in lines:
            for c in clusters:
                if abs(statistics.mean(x.y for x in c) - p.y) <= tol:
                    c.append(p)
                    break
            else:
                clusters.append([p])
    merged: list[ParsedLine] = []
    for c in sorted(clusters, key=lambda c: statistics.mean(x.y for x in c)):
        exact = [p for p in c if p.stat and not p.fuzzy]
        pool = exact or [p for p in c if p.stat] or c
        votes = Counter((p.stat, p.value) for p in pool)
        (stat, value), _ = votes.most_common(1)[0]
        best = next(p for p in pool if (p.stat, p.value) == (stat, value))
        merged.append(best)
    return merged


ProgressFn = Callable[[str], None]


TILE_THRESHOLD = 1600
PERCENT_ONLY = S.PERCENT_STATS - {S.ATK_PCT, S.HP_PCT, S.DEF_PCT}
_HAS_DIGIT = re.compile(r"\d")


@dataclass
class LabelRow:
    """전체 화면 OCR에서 찾은 스탯 줄: 이름 위치 + (읽혔다면) 숫자 위치."""

    family: str  # 스탯 키 (공격력/HP/방어력은 고정 수치 키). UNKNOWN = 이름을 못 읽음
    label: str
    label_x1: float
    cy: float
    h: float
    fuzzy: bool
    num_right: float | None
    fallback: ParsedLine | None

    @property
    def rank(self) -> int:
        return 2 if self.family and not self.fuzzy else 1 if self.family else 0


def _label_rows(words: list[Word]) -> list[LabelRow]:
    out: list[LabelRow] = []
    for row in group_rows(words):
        idx = next((i for i, w in enumerate(row.words) if _HAS_DIGIT.search(_fix_ocr_text(w.text))), None)
        label_words = row.words if idx is None else row.words[:idx]
        num_words = [] if idx is None else row.words[idx:]
        label = _fix_ocr_text(" ".join(w.text for w in label_words)).strip(" :：+·-")
        compact = re.sub(r"\s", "", label)
        if len(compact) > MAX_LABEL_LEN or re.search(r"COST|코스트|Lv|UID", label, re.I):
            continue
        fallback = parse_line(row.text, row.y)
        if fallback is None and num_words:
            continue  # 문장이거나 레벨 표시(+25) 같은 줄
        fam = S.classify_stat_name(label, percent=False) if label else None
        fuzzy = False
        if fam is None and label:
            fam = classify_fuzzy(label, False)
            fuzzy = fam is not None
        if fam is None:
            # 이름을 못 읽었어도 숫자가 있고 앞부분이 짧은 잡음뿐이면 스탯 줄일 수 있다
            if not num_words or len(compact) > 3:
                continue
        h = statistics.median(w.h for w in (label_words or num_words))
        out.append(LabelRow(
            family=fam or UNKNOWN,
            label=label,
            label_x1=max(w.x + w.w for w in label_words) if label_words else num_words[0].x - h,
            cy=row.y,
            h=h,
            fuzzy=fuzzy,
            num_right=max((w.x + w.w for w in num_words), default=None),
            fallback=fallback,
        ))
    return out


def _merge_label_rows(passes: list[list[LabelRow]]) -> list[LabelRow]:
    clusters: list[list[LabelRow]] = []
    for rows in passes:
        for r in rows:
            for c in clusters:
                if abs(statistics.mean(x.cy for x in c) - r.cy) <= 0.6 * r.h:
                    c.append(r)
                    break
            else:
                clusters.append([r])
    merged = []
    for c in clusters:
        best_rank = max(r.rank for r in c)
        pool = [r for r in c if r.rank == best_rank]
        fam = Counter(r.family for r in pool).most_common(1)[0][0]
        best = next(r for r in pool if r.family == fam)
        rights = [r.num_right for r in c if r.num_right is not None]
        best.num_right = max(rights) if rights else None
        fb = [r.fallback for r in c if r.fallback and r.fallback.stat and not r.fallback.fuzzy]
        best.fallback = fb[0] if fb else best.fallback
        merged.append(best)
    return sorted(merged, key=lambda r: r.cy)


def _final_stat(family: str, value_text: str) -> str:
    if family in S.FLAT_TO_PCT and "." in value_text:
        return S.FLAT_TO_PCT[family]
    return family


def read_lines(image: Image.Image | str | Path, mode: str = "echo", variants=VARIANTS,
               progress: ProgressFn | None = None) -> list[ParsedLine]:
    """mode='totals' 이면 공격력/HP/방어력을 고정 수치(정수)로만 읽는다."""
    from .valueocr import candidates, choose

    img = (Image.open(image) if isinstance(image, (str, Path)) else image).convert("RGB")
    passes = []
    for v in variants:
        if progress:
            progress(f"글자 위치 찾기 ({v})")
        # 게임 전체 화면처럼 큰 이미지는 조각으로 나눠 확대해서 읽는다 (통째로 읽으면 작은 글자를 놓친다)
        words = recognize_tiled(img, variant=v) if max(img.size) > TILE_THRESHOLD else recognize(img, variant=v)
        passes.append(_label_rows(words))
    rows = _merge_label_rows(passes)
    if not rows:
        return []
    # 이름을 못 읽은 줄이 이름을 읽은 줄 바로 옆이면 같은 줄을 반쯤 읽은 것이다 → 버린다
    known = [r for r in rows if r.family]
    rows = [r for r in rows if r.family or all(abs(r.cy - k.cy) > 0.8 * k.h for k in known)]

    # 값은 오른쪽 정렬이므로 숫자 오른쪽 끝의 중앙값을 기준선으로 쓴다
    rights = [r.num_right for r in rows if r.num_right is not None]
    h = statistics.median(r.h for r in rows)
    value_right = statistics.median(rights) if rights else None
    lines: list[ParsedLine] = []
    for i, r in enumerate(rows):
        if progress:
            progress(f"숫자 읽기 {i + 1}/{len(rows)}")
        right = value_right if value_right is not None else r.label_x1 + 15 * r.h
        if r.num_right is not None and abs(r.num_right - right) <= 2 * h:
            right = r.num_right
        elif r.family == UNKNOWN:
            continue  # 기준선에서 먼 숫자 (UID 등)
        x0 = max(r.label_x1 + 0.2 * h, right - 8 * h) if r.family else right - 8 * h
        box = (int(max(x0, 0)), int(max(r.cy - 0.85 * h, 0)),
               int(min(right + 0.8 * h, img.width)), int(min(r.cy + 0.85 * h, img.height)))
        if box[2] - box[0] < h or box[3] - box[1] < 4:
            continue
        if r.family in PERCENT_ONLY:
            expect = True
        elif r.family in S.FLAT_TO_PCT and mode == "totals":
            expect = False
        else:
            expect = None
        crop = img.crop(box)
        value_text = choose(candidates(crop), expect)
        if value_text is None:  # 색이 있는 숫자 (금색 등): 채도 제한 없이 밝기만으로 다시
            value_text = choose(candidates(crop, sat=256), expect)
        if value_text is None and r.fallback is not None:
            fb = r.fallback
            fam_fb = {S.ATK_PCT: S.ATK_FLAT, S.HP_PCT: S.HP_FLAT, S.DEF_PCT: S.DEF_FLAT}.get(fb.stat, fb.stat)
            if not r.family or fam_fb == r.family:
                lines.append(ParsedLine(fb.stat or r.family, fb.value, r.label, r.cy, fb.percent, r.fuzzy))
                continue
        if value_text is None:
            if r.family and mode == "echo":
                lines.append(ParsedLine(r.family, 0.0, r.label, r.cy, False, r.fuzzy))
            continue
        stat = _final_stat(r.family, value_text)
        lines.append(ParsedLine(stat, float(value_text), r.label, r.cy, "." in value_text, r.fuzzy))
    return lines


# ---------------------------------------------------------------------------
# 캐릭터 총 스탯
# ---------------------------------------------------------------------------
def totals_from_lines(lines: list[ParsedLine]) -> dict[str, float]:
    totals: dict[str, float] = {}
    for p in lines:
        if not p.stat:
            continue
        key = S.to_total_key(p.stat)
        # 총 스탯 화면의 공격력/HP/방어력은 고정 수치다.
        if key in (S.ATK_PCT, S.HP_PCT, S.DEF_PCT):
            if p.percent:
                continue
            key = S.to_total_key({S.ATK_PCT: S.ATK_FLAT, S.HP_PCT: S.HP_FLAT, S.DEF_PCT: S.DEF_FLAT}[key])
        totals.setdefault(key, p.value)
    return totals


def parse_totals(image, progress: ProgressFn | None = None) -> dict[str, float]:
    return totals_from_lines(read_lines(image, mode="totals", progress=progress))


# ---------------------------------------------------------------------------
# 에코
# ---------------------------------------------------------------------------
COST_RE = re.compile(r"(?:COST|코스트)\s*(\d)", re.I)


def infer_cost(main: StatLine | None, secondary: StatLine | None) -> int:
    if main is not None:
        if main.stat in (S.CRIT_RATE, S.CRIT_DMG, S.HEALING):
            return 4
        if main.stat in S.ELEMENT_DMG.values() or main.stat == S.ENERGY_REGEN:
            return 3
    if secondary is not None:
        if secondary.stat == S.HP_FLAT:
            return 1
        if secondary.stat == S.ATK_FLAT:
            if main is not None and main.stat in S.MAIN_STAT_MAX[4] and main.stat in S.MAIN_STAT_MAX[3]:
                # 레벨이 낮아도 메인옵션과 고정 부옵션의 성장 비율은 같다
                d4 = abs(main.value / S.MAIN_STAT_MAX[4][main.stat] - secondary.value / 150)
                d3 = abs(main.value / S.MAIN_STAT_MAX[3][main.stat] - secondary.value / 100)
                return 4 if d4 <= d3 else 3
            return 4 if secondary.value > 100 else 3
    return 0


_ALL_MAIN_STATS = {k for d in S.MAIN_STAT_MAX.values() for k in d}


def _is_secondary(p: ParsedLine) -> bool:
    """메인옵션 바로 아래 고정 부옵션: 공격력(100/150) 또는 HP(2280) 고정 수치."""
    return not p.percent and p.value > 0 and p.stat in (S.ATK_FLAT, S.HP_FLAT, UNKNOWN)


def _find_main(lines: list[ParsedLine]) -> int | None:
    """'% 메인옵션 + 바로 다음 줄 고정 부옵션' 쌍의 위치. 그 위의 이름/레벨/COST 줄은 무시한다."""
    for i, p in enumerate(lines[:-1]):
        if p.percent and p.stat in _ALL_MAIN_STATS and _is_secondary(lines[i + 1]):
            return i
    for i, p in enumerate(lines):  # 부옵션 줄을 못 읽은 경우: 첫 % 메인옵션
        if p.percent and p.stat in _ALL_MAIN_STATS:
            return i
    return None


def echo_from_lines(lines: list[ParsedLine], header_text: str = "", source: str = "") -> Echo:
    """에코 상세 화면: 메인옵션 1개 + (고정 부옵션은 건너뜀) + 서브옵션 5개."""
    idx = _find_main(lines)
    main = StatLine(lines[idx].stat, lines[idx].value) if idx is not None else None
    rest = lines[idx + 1:] if idx is not None else lines
    secondary = None  # 코스트 추정에만 쓴다
    if rest and _is_secondary(rest[0]):
        stat = rest[0].stat or (S.HP_FLAT if rest[0].value >= 300 else S.ATK_FLAT)  # 1코스트 HP 고정치는 수백~2280
        secondary = StatLine(stat, rest[0].value)
        rest = rest[1:]
    subs = [StatLine(p.stat, p.value) for p in rest
            if p.value > 0 and (p.stat in S.SUBSTAT_MAX or p.stat == UNKNOWN)][:5]

    name, level, header_cost = parse_echo_header(header_text)
    cost = header_cost or infer_cost(main, secondary)
    return Echo(cost=cost, main=main, substats=subs, name=name, level=level, source=source)


def parse_echo_header(header_text: str) -> tuple[str, int | None, int]:
    """에코 상단 '이름 +25' / 'COST 3' → (이름, 레벨, 코스트)

    긴 이름은 두 줄로 나뉘므로('공명의 메아리 · 태초의 여우의 별자리 ·' / '천연 +25') COST 줄 위의 줄을 모두 잇는다.
    '+25' 뒤에 붙는 글자는 잠금·즐겨찾기 아이콘을 잘못 읽은 것이라 버린다.
    """
    lines = [_fix_ocr_text(l).strip() for l in header_text.splitlines() if l.strip()]
    cost_idx = next((i for i, l in enumerate(lines) if COST_RE.search(l)), None)
    m = COST_RE.search(lines[cost_idx]) if cost_idx is not None else None
    cost = int(m.group(1)) if m and m.group(1) in "134" else 0
    name_lines = lines[:cost_idx] if cost_idx is not None else lines[:2]
    parts, level = [], None
    for line in name_lines:
        lv = re.search(r"\+\s?(\d{1,2})\b", line)
        if lv and int(lv.group(1)) <= 25:
            level = int(lv.group(1))
            line = line[: lv.start()]
        elif level is None and not re.search(r"[가-힣A-Za-z]{2,}", line):
            continue  # 이름 줄이 아닌 잡음
        parts.append(line.strip(" -"))
    name = re.sub(r"\s*·\s*", " · ", " ".join(p for p in parts if p)).strip(" ·")
    return name, level, cost


def _header_text(img: Image.Image, variant: str) -> str:
    return "\n".join(r.text for r in group_rows(recognize(img, variant=variant))[:4])


def snap_echo_name(candidates: list[str], known: list[str] | None = None) -> str:
    """OCR로 읽은 에코 이름 후보들을 공식 에코 이름 목록에서 가장 가까운 이름으로 보정한다.

    에코 이름은 금색 글자라 전처리마다 다르게 읽힌다 ('천연' → '전연', 둘째 줄 누락 등).
    충분히 비슷한 공식 이름이 없으면 가장 긴 후보를 그대로 쓴다.
    """
    if known is None:
        from .images import load_echo_index
        known = list(load_echo_index())
    cands = [c for c in candidates if c]
    if not cands:
        return ""
    norm = lambda s: re.sub(r"[\s·.\-:：()（）]", "", s)  # noqa: E731
    best, best_r = None, 0.0
    for c in cands:
        for k in known:
            r = SequenceMatcher(None, norm(c), norm(k)).ratio()
            if r > best_r:
                best, best_r = k, r
    return best if best and best_r >= 0.8 else max(cands, key=len)


def read_echo_header(img: Image.Image) -> tuple[str, int | None, int]:
    """에코 이름 / +레벨 / COST: 두 가지 전처리로 읽어 합친다."""
    names, level, cost = [], None, 0
    for v in ("clean", "raw"):
        n, lv, c = parse_echo_header(_header_text(img, v))
        names.append(n)
        level = level if level is not None else lv
        cost = cost or c
    return snap_echo_name(names), level, cost


def parse_echo(image, progress: ProgressFn | None = None) -> Echo:
    img = Image.open(image) if isinstance(image, (str, Path)) else image
    lines = read_lines(img, mode="echo", progress=progress)
    # 이름 / +레벨 / COST 는 스탯 줄이 아니므로 위쪽 줄 원문에서 따로 찾는다
    name, level, cost = read_echo_header(img)
    header = f"{name} +{level}\nCOST {cost}" if level is not None else f"{name}\nCOST {cost}"
    return echo_from_lines(lines, header, str(image) if isinstance(image, (str, Path)) else "")
