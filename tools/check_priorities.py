"""서브옵션 우선순위에서 빠졌을 수 있는 '피해 보너스' 옵션을 찾는다.

공략 우선순위에 피해 보너스(일반 공격/강공격/공명 스킬/공명 해방)가 하나도 없는 캐릭터를 출력하고,
공명 체인(돌파) 효과 문구에서 가장 많이 언급된 피해 종류를 참고용으로 함께 보여 준다.
돌파 문구는 여러 피해 종류를 두루 언급하므로 이것만으로 판단하지 말고 공략·실제 운용과 대조해야 한다.

사용: .venv/Scripts/python tools/check_priorities.py
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from wuwastat import stats as S  # noqa: E402
from wuwastat.models import load_characters  # noqa: E402

KEYWORDS = {
    S.BASIC_DMG: ["일반 공격"],
    S.HEAVY_DMG: ["강공격"],
    S.SKILL_DMG: ["공명 스킬"],
    S.LIBERATION_DMG: ["공명 해방"],
}


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    chars, _ = load_characters()
    found = 0
    for c in chars:
        have = {k for tier in c.substat_priority for k in tier}
        if have & set(KEYWORDS):
            continue
        text = (c.chain_text or "") + " ".join(ch.description or "" for ch in c.chains)
        hits = sorted(((sum(text.count(w) for w in ws), k) for k, ws in KEYWORDS.items()), reverse=True)
        hint = ", ".join(f"{S.label(k)} {n}회" for n, k in hits if n) or "-"
        print(f"{c.name}: 공략 {c.substat_priority_text or '(우선순위 없음)'}\n    돌파 문구 언급: {hint}")
        found += 1
    print(f"\n피해 보너스가 우선순위에 없는 캐릭터 {found}명 / 전체 {len(chars)}명")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
