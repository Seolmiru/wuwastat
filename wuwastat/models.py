"""데이터 모델."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

from . import stats as S

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
# 저장소에 포함된 기본 데이터: 숫자(추천 스탯 등)와 이름만 담는다
BUNDLED_DATA = DATA_DIR / "characters.json"
# '캐릭터 데이터 업데이트'로 받은 전체 데이터 (공략 설명 글 포함). 저장소에 올리지 않는다 (.gitignore)
LOCAL_DATA = DATA_DIR / "characters.local.json"
# 직접 입력한 추천 파티 모드 (공식 공략에 없는 정보)
PARTY_MODES = DATA_DIR / "party_modes.json"


def data_path() -> Path:
    """업데이트로 받은 전체 데이터가 있으면 그것을, 없으면 저장소 기본 데이터를 쓴다."""
    return LOCAL_DATA if LOCAL_DATA.exists() else BUNDLED_DATA


DEFAULT_DATA = BUNDLED_DATA  # 예전 이름 호환


@dataclass
class Target:
    stat: str
    value: float
    op: str = ">="
    label: str = ""


@dataclass
class EchoSlot:
    cost: int
    main: str


@dataclass
class Weapon:
    name: str
    star: int | None = None
    recommended: bool = False
    signature: bool = False
    effect: str = ""


@dataclass
class Chain:
    seq: int
    name: str = ""
    description: str = ""
    core: bool = False


@dataclass
class Character:
    id: str
    name: str
    name_en: str = ""
    star: int | None = None
    element: str = ""
    source_url: str = ""
    portrait: str = ""  # 공식 서버의 캐릭터 초상화 주소
    guide: dict | None = None
    targets: list[Target] = field(default_factory=list)
    echo_slots: list[EchoSlot] = field(default_factory=list)
    main_echo: str = ""
    sonata: list[str] = field(default_factory=list)
    substat_priority: list[list[str]] = field(default_factory=list)
    substat_priority_text: str = ""
    extra_substats: list[str] = field(default_factory=list)  # 공략에 없지만 유저가 유효하다고 추가한 옵션
    weapons: list[Weapon] = field(default_factory=list)
    weapon_text: str = ""
    chains: list[Chain] = field(default_factory=list)
    chain_text: str = ""
    teams: list[list[str]] = field(default_factory=list)  # 추천 파티원 칸별 [주 추천 ID, 대체 ID...]
    stale: bool = False  # 공식 사이트에서 사라져 예전 데이터를 쓰는 중

    @property
    def signature_weapon(self) -> Weapon | None:
        return next((w for w in self.weapons if w.signature), None)

    @property
    def sonata_names(self) -> list[str]:
        """'영원의 광채 5세트' → '영원의 광채'"""
        import re
        return [re.sub(r"\s*\d세트$", "", s) for s in self.sonata]

    @classmethod
    def from_dict(cls, d: dict) -> "Character":
        return cls(
            id=str(d["id"]),
            name=d["name"],
            name_en=d.get("name_en", ""),
            star=d.get("star"),
            element=d.get("element", ""),
            source_url=d.get("source_url", ""),
            portrait=d.get("portrait") or d.get("icon") or "",
            guide=d.get("guide"),
            targets=[Target(**t) for t in d.get("targets", [])],
            echo_slots=[EchoSlot(**s) for s in d.get("echo_slots", [])],
            main_echo=d.get("main_echo", ""),
            sonata=list(d.get("sonata", [])),
            substat_priority=[list(t) for t in d.get("substat_priority", [])],
            substat_priority_text=d.get("substat_priority_text", ""),
            weapons=[Weapon(**w) for w in d.get("weapons", [])],
            weapon_text=d.get("weapon_text", ""),
            chains=[Chain(**c) for c in d.get("chains", [])],
            chain_text=d.get("chain_text", ""),
            teams=[[str(i) for i in slot] for slot in d.get("teams", []) if slot],
            stale=bool(d.get("stale", False)),
        )

    def recommended_parties(self) -> list[tuple[str, ...]]:
        """추천 파티 조합 (자기 자신 포함 ID 목록).

        첫 조합은 칸마다 주 추천 캐릭터. 이후 한 칸씩 대체 캐릭터로 바꾼 조합을 칸 순서대로 더한다.
        예) 에이메스 - 린네 - 모니에 / 에이메스 - 데니아 - 모니에 / 에이메스 - 루파 - 모니에 / 에이메스 - 린네 - 치사
        """
        if not self.teams:
            return []
        base = [slot[0] for slot in self.teams]
        parties = [tuple([self.id, *base])]
        for i, slot in enumerate(self.teams):
            for alt in slot[1:]:
                members = list(base)
                members[i] = alt
                if alt not in base:
                    parties.append(tuple([self.id, *members]))
        return parties

    def recommended_mains(self, cost: int) -> set[str]:
        return {s.main for s in self.echo_slots if s.cost == cost}

    @property
    def display_name(self) -> str:
        return f"{self.name} ({self.name_en})" if self.name_en and self.name_en != self.name else self.name


@dataclass
class StatLine:
    stat: str
    value: float


@dataclass
class Echo:
    cost: int
    main: StatLine | None = None
    substats: list[StatLine] = field(default_factory=list)
    name: str = ""
    level: int | None = None
    source: str = ""  # 스크린샷 파일 경로 등


@dataclass
class Build:
    """평가 대상: 캐릭터 총 스탯 + 장착 에코."""

    totals: dict[str, float] = field(default_factory=dict)
    echoes: list[Echo] = field(default_factory=list)
    weapon: str = ""  # 장착 무기 이름 ("" = 입력 안 함)
    sonata: list[str] = field(default_factory=list)  # 장착한 에코 세트 이름들 (3+2 조합이면 2개)
    chain: int | None = None  # 공명 체인(돌파) 단계 0~6


def load_party_modes(names: dict[str, str], path: Path | None = None) -> list[tuple[str, str, str]]:
    """party_modes.json → [(함께 쓰는 캐릭터 ID, 모드를 고르는 캐릭터 ID, 모드)]. names: ID → 이름."""
    try:
        raw = json.loads(Path(path or PARTY_MODES).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    by_name = {v: k for k, v in names.items()}
    out = []
    for r in raw.get("rules", []) if isinstance(raw, dict) else []:
        if not isinstance(r, dict):
            continue
        a, b, mode = by_name.get(str(r.get("with", ""))), by_name.get(str(r.get("member", ""))), str(r.get("mode", ""))
        if a and b and mode:
            out.append((a, b, mode))
    return out


def party_modes(party: tuple[str, ...] | list[str], rules: list[tuple[str, str, str]]) -> dict[str, str]:
    """파티 안에서 모드를 고르는 캐릭터 ID → 모드. 같은 캐릭터에 규칙이 여럿 맞으면 먼저 적힌 것."""
    members = set(party)
    out: dict[str, str] = {}
    for a, b, mode in rules:
        if a in members and b in members and b not in out:
            out[b] = mode
    return out


def load_characters(path: Path | None = None) -> tuple[list[Character], dict]:
    raw = json.loads(Path(path or data_path()).read_text(encoding="utf-8"))
    chars = [Character.from_dict(c) for c in raw.get("characters", [])]
    meta = {k: v for k, v in raw.items() if k != "characters"}
    return chars, meta


__all__ = ["Target", "EchoSlot", "Character", "StatLine", "Echo", "Build", "load_characters", "load_party_modes", "party_modes", "S"]
