"""데이터 모델."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

from . import stats as S

DEFAULT_DATA = Path(__file__).resolve().parent.parent / "data" / "characters.json"


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
    guide: dict | None = None
    targets: list[Target] = field(default_factory=list)
    echo_slots: list[EchoSlot] = field(default_factory=list)
    main_echo: str = ""
    sonata: list[str] = field(default_factory=list)
    substat_priority: list[list[str]] = field(default_factory=list)
    substat_priority_text: str = ""
    weapons: list[Weapon] = field(default_factory=list)
    weapon_text: str = ""
    chains: list[Chain] = field(default_factory=list)
    chain_text: str = ""
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
            stale=bool(d.get("stale", False)),
        )

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


def load_characters(path: Path = DEFAULT_DATA) -> tuple[list[Character], dict]:
    raw = json.loads(Path(path).read_text(encoding="utf-8"))
    chars = [Character.from_dict(c) for c in raw.get("characters", [])]
    meta = {k: v for k, v in raw.items() if k != "characters"}
    return chars, meta


__all__ = ["Target", "EchoSlot", "Character", "StatLine", "Echo", "Build", "load_characters", "S"]
