"""명조 공식 공략 사이트(wuwaguide.kurogames.com)에서 캐릭터별 추천 스탯을 수집한다.

공식 사이트는 Vue SPA이며 아래 API에서 데이터를 받아온다.
  GET /role/avatar/list                      전체 캐릭터 목록
  GET /introduction/list?roleGbId=<id>       캐릭터별 공략 목록 (언어별로 따로 등록됨)
  GET /introduction/info?id=<공략 id>        공략 상세 (x-language 헤더가 공략 언어와 같아야 함)

공략 상세의 roleAttribute(추천 스탯), echo(에코 코스트별 추천 메인옵션, 세트),
echoTexts(서브옵션 우선순위 문구)를 정리해서 data/characters.json 에 저장한다.

실행: python -m wuwastat.scraper
"""

from __future__ import annotations

import argparse
import html
import json
import re
import sys
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from . import stats as S

API_BASES = ["https://guide-server.aki-game.net", "https://guide-server-1.aki-game.net"]
GUIDE_PAGE = "https://wuwaguide.kurogames.com/ko/?role_id={id}"
DATA_DIR = Path(__file__).resolve().parent.parent / "data"
DEFAULT_OUTPUT = DATA_DIR / "characters.local.json"  # 전체 데이터 (저장소 제외)
BUNDLED_OUTPUT = DATA_DIR / "characters.json"  # 저장소용: 숫자와 이름만
PREFERRED_LANG = "ko"

# roleAttribute.gbId 앞부분 → 스탯 키 (다른 언어 공략일 때 이름 대신 사용)
TARGET_GBID = {
    "2": S.HP, "7": S.ATK, "10": S.DEF, "8": S.CRIT_RATE, "9": S.CRIT_DMG,
    "11": S.ENERGY_REGEN, "35": S.HEALING,
    "22": S.GLACIO_DMG, "23": S.FUSION_DMG, "24": S.ELECTRO_DMG,
    "25": S.AERO_DMG, "26": S.SPECTRO_DMG, "27": S.HAVOC_DMG,
    "14": S.SKILL_DMG,
}
# echoAttributes.attribute.gbId → 에코 메인옵션 키
ECHO_MAIN_GBID = {
    "81": S.CRIT_RATE, "91": S.CRIT_DMG, "111": S.ENERGY_REGEN, "351": S.HEALING,
    "221": S.GLACIO_DMG, "231": S.FUSION_DMG, "241": S.ELECTRO_DMG,
    "251": S.AERO_DMG, "261": S.SPECTRO_DMG, "271": S.HAVOC_DMG,
    "100072": S.ATK_PCT, "100022": S.HP_PCT, "100102": S.DEF_PCT,
}
MAX_RESPONSE_BYTES = 10 * 1024 * 1024
ROLE_ID_RE = re.compile(r"\d{1,8}")  # 캐릭터 ID는 숫자만 (저장 파일 이름과 URL에 쓰인다)
OPERATIONS = {1: "=", 2: "!=", 3: "<", 4: ">", 5: "<=", 6: ">="}


class ApiError(RuntimeError):
    pass


def _get(path: str, lang: str = PREFERRED_LANG, timeout: float = 20) -> Any:
    last: Exception | None = None
    for base in API_BASES:
        req = urllib.request.Request(
            base + path,
            headers={
                "x-language": lang,
                "User-Agent": "wuwastat/0.1",
                "Origin": "https://wuwaguide.kurogames.com",
            },
        )
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                raw = resp.read(MAX_RESPONSE_BYTES + 1)
            if len(raw) > MAX_RESPONSE_BYTES:
                raise ApiError(f"{path}: 응답이 너무 큽니다")
            body = json.loads(raw.decode("utf-8"))
            if not isinstance(body, dict):
                raise ApiError(f"{path}: 예상과 다른 응답 형식")
        except Exception as e:  # 네트워크 오류면 다음 도메인으로
            last = e
            continue
        if body.get("code") != 200:
            raise ApiError(f"{path}: {body.get('message')}")
        return body.get("data")
    raise ApiError(f"{path}: {last}")


def _text(texts: list[dict] | None, field: str = "name", lang: str = PREFERRED_LANG) -> str:
    texts = texts or []
    for t in texts:
        if t.get("language") == lang and t.get(field):
            return t[field]
    for t in texts:
        if t.get("language") == "en" and t.get(field):
            return t[field]
    return next((t[field] for t in texts if t.get(field)), "")


def _strip_html(s: str) -> str:
    s = re.sub(r"<br\s*/?>|</p>", "\n", s or "")
    s = re.sub(r"<[^>]+>", "", s)
    return html.unescape(s).replace("\xa0", " ").strip()


def _parse_number(s: str) -> float:
    return float(re.sub(r"[^\d.\-]", "", s) or 0)


# ---------------------------------------------------------------------------
# 서브옵션 우선순위 문구 해석
# ---------------------------------------------------------------------------
_IGNORE_WORDS = ("나머지", "기타", "그외", "그 외")


def parse_priority(text: str) -> list[list[str]]:
    """'크확/크피>공격력 백분율>강공격 피해 보너스>공명 효율' → [[crit_rate, crit_dmg], [atk_pct], ...]

    '>'로 등급을 나누고, '/', '=', ',' 로 묶인 항목은 같은 등급으로 본다.
    """
    text = text.replace("＞", ">").replace("》", ">").replace("→", ">")
    tiers: list[list[str]] = []
    seen: set[str] = set()
    for chunk in text.split(">"):
        if any(w in chunk for w in _IGNORE_WORDS):
            continue
        tier: list[str] = []
        prev: str | None = None
        parts = [p for p in re.split(r"[/=,、]", chunk) if p.strip()]
        # '일반 공격/강공격 피해 보너스' 처럼 앞부분에 '피해'가 생략된 경우도 classify가 처리한다.
        for part in parts:
            key = S.classify_stat_name(part, percent=True, prev=prev)
            if key and key not in seen:
                tier.append(key)
                seen.add(key)
            prev = key or prev
        if tier:
            tiers.append(tier)
    return tiers


# ---------------------------------------------------------------------------
# 공략 → 캐릭터 레코드
# ---------------------------------------------------------------------------
def _target_from_item(item: dict) -> dict | None:
    prefix = str(item.get("gbId", "")).split("-")[0]
    name = _text(item.get("texts"))
    key = TARGET_GBID.get(prefix)
    if key is None:
        key = S.classify_stat_name(name, percent=item.get("attachmentType") == "2")
        if key:
            key = S.to_total_key(key)
    if key is None:
        return None
    return {
        "stat": key,
        "value": _parse_number(str(item.get("recommendAmount", "0"))),
        "op": OPERATIONS.get(item.get("operation"), ">="),
        "label": name,
    }


def _echo_main_key(attr: dict | None) -> str | None:
    if not attr:
        return None
    key = ECHO_MAIN_GBID.get(str(attr.get("gbId")))
    if key is None:
        key = S.classify_stat_name(_text(attr.get("texts")), percent=attr.get("attachmentGameBusinessId") == "2")
    return key


def build_character(avatar: dict, guide_meta: dict | None, info: dict | None) -> dict:
    rid = str(avatar["roleGbId"])
    element = S.ELEMENT_BY_GBID.get(str((avatar.get("element") or {}).get("gbId")), "")
    record: dict[str, Any] = {
        "id": rid,
        "name": _text(avatar.get("texts")),
        "name_en": _text(avatar.get("texts"), lang="en"),
        "star": avatar.get("star"),
        "element": element,
        "portrait": avatar.get("cardPictureUrl") or avatar.get("portrait"),
        "source_url": GUIDE_PAGE.format(id=rid),
        "guide": None,
        "targets": [],
        "echo_slots": [],
        "main_echo": "",
        "sonata": [],
        "substat_priority": [],
        "substat_priority_text": "",
        "weapons": [],
        "weapon_text": "",
        "chains": [],
        "chain_text": "",
        "teams": [],
    }
    if not info:
        return record

    lang = guide_meta["language"] if guide_meta else PREFERRED_LANG
    record["guide"] = {
        "id": info.get("id"),
        "language": lang,
        "title": _text(info.get("baseTexts"), "introductionName", lang)
        or (guide_meta or {}).get("title", ""),
        "author": _text(info.get("baseTexts"), "introductionSource", lang),
        "likes": info.get("likeCount"),
        "modified_at": (guide_meta or {}).get("modified_at"),
    }

    for item in (info.get("roleAttribute") or {}).get("items") or []:
        t = _target_from_item(item)
        if t:
            record["targets"].append(t)

    echo_main = (info.get("echo") or {}).get("main") or {}
    props = echo_main.get("echoProps") or {}
    record["main_echo"] = _text(props.get("texts"), lang=lang)
    sets: dict[str, int] = {}  # 세트 이름 → 최대 세트 수 (5세트와 2세트가 함께 오면 5세트만)
    for se in echo_main.get("echoSetEffects") or []:
        name = _text(se.get("texts"), lang=lang)
        if name:
            sets[name] = max(sets.get(name, 0), int(se.get("echoSet") or 0))
    record["sonata"] = [f"{n} {c}세트" for n, c in sets.items()]
    for slot in echo_main.get("echoAttributes") or []:
        key = _echo_main_key(slot.get("attribute"))
        if key:
            record["echo_slots"].append({"cost": int(slot.get("cost") or 0), "main": key})

    prio_text = _strip_html(_text(info.get("echoTexts"), "recommendDescription", lang))
    record["substat_priority_text"] = prio_text
    record["substat_priority"] = parse_priority(prio_text) if lang == PREFERRED_LANG else []

    # 추천 무기: status 1 = 추천, 2 = 대안. 첫 번째 추천 5성 무기를 전용(종결) 무기로 본다.
    weapons = []
    for i, w in enumerate((info.get("weapon") or {}).get("items") or []):
        weapons.append({
            "name": _text(w.get("texts"), lang=lang),
            "star": w.get("star"),
            "recommended": w.get("status") == 1,
            "effect": _text(w.get("texts"), "effectDescription", lang),
        })
    sig = next((w for w in weapons if w["recommended"] and w["star"] == 5), None)
    for w in weapons:
        w["signature"] = w is sig
    record["weapons"] = weapons
    record["weapon_text"] = _strip_html(_text(info.get("weaponTexts"), "recommendDescription", lang))

    # 공명 체인(돌파): status 1 = 공략이 꼽은 핵심 체인
    record["chains"] = [
        {
            "seq": int(c.get("resonanceSequence") or 0),
            "name": _text(c.get("texts"), lang=lang),
            "description": _text(c.get("texts"), "description", lang),
            "core": c.get("status") == 1,
        }
        for c in (info.get("roleResonance") or {}).get("items") or []
    ]
    record["chain_text"] = _strip_html(_text(info.get("roleResonanceTexts"), "recommendDescription", lang))
    record["teams"] = parse_teams(info.get("teammate"))
    record["team_members"] = collect_team_members(info.get("teammate"))
    record["echo_images"] = collect_echo_images(info)
    return record


def parse_teams(teammate: dict | None) -> list[list[str]]:
    """추천 파티원 칸 목록. 칸마다 [주 추천 캐릭터 ID, 대체 캐릭터 ID...].

    공략 사이트는 파티원 칸(보통 2칸)마다 주 추천 캐릭터와 대체 캐릭터를 보여준다.
    """
    teammate = teammate or {}
    items = teammate.get("items") or []
    if not items:  # 모드별 추천만 있는 공략이면 첫 번째 모드를 쓴다
        modes = teammate.get("modeTeammateRecommends") or []
        items = next((m.get("items") for m in modes if isinstance(m, dict) and m.get("items")), [])
    slots: list[list[str]] = []
    for it in items:
        if not isinstance(it, dict):
            continue
        members = [it.get("main")] + list(it.get("spares") or [])
        ids: list[str] = []
        for m in members:
            rid = str((m or {}).get("roleGbId", "")) if isinstance(m, dict) else ""
            if ROLE_ID_RE.fullmatch(rid) and rid not in ids:
                ids.append(rid)
        if ids:
            slots.append(ids)
    return slots


def collect_team_members(teammate: dict | None) -> dict[str, dict]:
    """추천 파티원 ID → 이름·초상화 주소. 캐릭터 목록에 없는 파티원(방랑자 등)을 표시할 때 쓴다."""
    out: dict[str, dict] = {}

    def walk(o: Any) -> None:
        if isinstance(o, dict):
            rid = str(o.get("roleGbId", ""))
            if ROLE_ID_RE.fullmatch(rid) and o.get("texts"):
                out.setdefault(rid, {"name": _text(o.get("texts")),
                                     "portrait": o.get("cardPictureUrl") or ""})
            for v in o.values():
                walk(v)
        elif isinstance(o, list):
            for v in o:
                walk(v)

    walk(teammate)
    return out


def collect_echo_images(info: dict) -> dict[str, str]:
    """공략에 등장하는 모든 에코(추천 메인·대체·파티원 에코)의 한국어 이름 → 공식 이미지 주소."""
    out: dict[str, str] = {}

    def walk(o: Any) -> None:
        if isinstance(o, dict):
            props = o.get("echoProps")
            if isinstance(props, dict):
                name = next((t.get("name") for t in props.get("texts") or [] if t.get("language") == "ko"), None)
                url = props.get("pictureUrl")
                if name and isinstance(url, str) and url.startswith("https://"):
                    out.setdefault(name, url)
            for v in o.values():
                walk(v)
        elif isinstance(o, list):
            for v in o:
                walk(v)

    walk(info)
    return out


def _pick_guide(guides: list[dict]) -> dict | None:
    """한국어 공략 중 추천 수가 가장 많은 것, 없으면 전체 언어 중 가장 많은 것."""
    cands = []
    for g in guides or []:
        for t in g.get("texts") or []:
            if t.get("introductionName"):
                cands.append({
                    "id": g["id"],
                    "language": t["language"],
                    "title": t["introductionName"],
                    "likes": g.get("likeCount", 0),
                    "modified_at": g.get("modifiedAt"),
                })
    if not cands:
        return None
    cands.sort(key=lambda c: (c["language"] == PREFERRED_LANG, c["likes"]), reverse=True)
    return cands[0]


def fetch_character(avatar: dict) -> dict:
    rid = avatar["roleGbId"]
    meta = _pick_guide(_get(f"/introduction/list?roleGbId={rid}"))
    info = _get(f"/introduction/info?id={meta['id']}", meta["language"]) if meta else None
    return build_character(avatar, meta, info)


def _avatar_from_record(rec: dict) -> dict:
    """이전에 저장한 캐릭터 레코드를 avatar 목록 형식으로 되돌린다."""
    gbid = next((k for k, v in S.ELEMENT_BY_GBID.items() if v == rec.get("element")), "")
    return {
        "roleGbId": rec["id"],
        "texts": [{"language": "ko", "name": rec.get("name", "")}, {"language": "en", "name": rec.get("name_en", "")}],
        "star": rec.get("star"),
        "element": {"gbId": gbid},
        "cardPictureUrl": rec.get("portrait") or rec.get("icon"),
    }


def load_previous(path: Path = DEFAULT_OUTPUT) -> dict | None:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def scrape_all(progress: Callable[[int, int, str], None] | None = None, workers: int = 4,
               previous: dict | None = None) -> dict:
    """previous: 이전 수집 결과. 공식 목록에서 빠진 캐릭터도 공략이 남아 있으면 계속 수집한다."""
    avatars = [a for a in (_get("/role/avatar/list") or [])
               if isinstance(a, dict) and ROLE_ID_RE.fullmatch(str(a.get("roleGbId", "")))]
    listed = {str(a["roleGbId"]) for a in avatars}
    for rec in (previous or {}).get("characters", []):
        if str(rec["id"]) not in listed and ROLE_ID_RE.fullmatch(str(rec["id"])):
            avatars.append(_avatar_from_record(rec))
    results: list[dict] = [None] * len(avatars)  # type: ignore[list-item]
    errors: list[str] = []

    def job(i: int) -> None:
        a = avatars[i]
        try:
            results[i] = fetch_character(a)
        except Exception as e:  # 한 캐릭터 실패가 전체를 막지 않게
            errors.append(f"{a.get('roleGbId')}: {e}")
            results[i] = build_character(a, None, None)

    done = 0
    with ThreadPoolExecutor(max_workers=workers) as ex:
        for i, _ in zip(range(len(avatars)), ex.map(job, range(len(avatars)))):
            done += 1
            if progress:
                progress(done, len(avatars), results[i]["name"] if results[i] else "")

    # 이번에 공략을 못 받았지만 예전 데이터에는 있던 캐릭터는 예전 데이터를 유지한다
    prev_by_id = {str(c["id"]): c for c in (previous or {}).get("characters", [])}
    for i, r in enumerate(results):
        old = prev_by_id.get(str(r["id"])) if r else None
        if r and not r["guide"] and old and old.get("guide"):
            results[i] = {**r, **old, "stale": True}

    chars = sorted((r for r in results if r), key=lambda c: c["name"])
    echo_images: dict[str, str] = dict((previous or {}).get("echo_images", {}))
    team_members: dict[str, dict] = dict((previous or {}).get("team_members", {}))
    for c in chars:
        echo_images.update(c.pop("echo_images", None) or {})
        team_members.update(c.pop("team_members", None) or {})
    known = {str(c["id"]) for c in chars}
    return {
        "source": "https://wuwaguide.kurogames.com/ko/",
        "fetched_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "count": len(chars),
        "errors": errors,
        "characters": chars,
        "echo_images": dict(sorted(echo_images.items())),
        # 캐릭터 목록에 없는 추천 파티원만 (방랑자 등)
        "team_members": {k: v for k, v in sorted(team_members.items()) if k not in known},
    }


def save(data: dict, path: Path = DEFAULT_OUTPUT) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)
    return path


def slim(data: dict) -> dict:
    """저장소에 올릴 데이터: 계산에 쓰는 숫자와 이름만 남기고 공략 설명 글은 뺀다.

    빼는 것: 공략 제목·작성자, 서브옵션 우선순위 원문, 무기 효과·추천 설명, 공명 체인 효과·추천 설명.
    이미지는 파일이 아니라 공식 서버의 주소(링크)만 남긴다. 앱이 실행 중에 받아 PC 캐시에만 둔다.
    빠진 내용은 앱에서 '캐릭터 데이터 업데이트'를 누르면 공식 사이트에서 받아 로컬 파일에만 저장한다.
    """
    chars = []
    for c in data.get("characters", []):
        g = c.get("guide")
        chars.append({
            "id": c["id"],
            "name": c.get("name", ""),
            "name_en": c.get("name_en", ""),
            "star": c.get("star"),
            "element": c.get("element", ""),
            "source_url": c.get("source_url", ""),
            "portrait": _official_image(c.get("portrait") or c.get("icon")),
            "guide": {k: g.get(k) for k in ("id", "language", "likes", "modified_at")} if g else None,
            "targets": [{k: t[k] for k in ("stat", "value", "op")} for t in c.get("targets", [])],
            "echo_slots": c.get("echo_slots", []),
            "main_echo": c.get("main_echo", ""),
            "sonata": c.get("sonata", []),
            "substat_priority": c.get("substat_priority", []),
            "weapons": [{k: w.get(k) for k in ("name", "star", "recommended", "signature")}
                        for w in c.get("weapons", [])],
            "chains": [{k: ch.get(k) for k in ("seq", "name", "core")} for ch in c.get("chains", [])],
            "teams": c.get("teams", []),
            **({"stale": True} if c.get("stale") else {}),
        })
    return {
        "source": data.get("source"),
        "fetched_at": data.get("fetched_at"),
        "count": len(chars),
        "slim": True,
        "characters": chars,
        "echo_images": {k: v for k, v in (data.get("echo_images") or {}).items() if _official_image(v)},
        "team_members": {k: {"name": v.get("name", ""), "portrait": _official_image(v.get("portrait"))}
                         for k, v in (data.get("team_members") or {}).items()},
    }


def _official_image(url: str | None) -> str:
    """공식 CDN의 https 이미지 주소만 남긴다."""
    from .images import is_allowed
    return url if url and is_allowed(url) else ""


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="공식 공략 사이트에서 캐릭터 추천 스탯 수집")
    ap.add_argument("-o", "--output", type=Path, default=DEFAULT_OUTPUT,
                    help="전체 데이터 저장 위치 (기본: data/characters.local.json, 저장소 제외)")
    ap.add_argument("--export-slim", action="store_true",
                    help="저장소용 data/characters.json (숫자와 이름만)도 함께 갱신")
    ap.add_argument("--download-images", action="store_true",
                    help="캐릭터·에코 초상화를 공식 CDN에서 받아 assets 폴더에 저장 (개발자용)")
    args = ap.parse_args(argv)

    def progress(i: int, n: int, name: str) -> None:
        print(f"[{i}/{n}] {name}", flush=True)

    previous = load_previous(args.output) or load_previous(BUNDLED_OUTPUT)
    data = scrape_all(progress, previous=previous)
    path = save(data, args.output)
    if args.export_slim:
        print(f"저장소용 데이터 저장: {save(slim(data), BUNDLED_OUTPUT)}")
    if args.download_images:
        from .images import download_assets
        n_p, n_e = download_assets(data)
        print(f"초상화 {n_p}개, 에코 이미지 {n_e}개 저장: assets/")
    no_guide = [c["name"] for c in data["characters"] if not c["guide"]]
    print(f"저장 완료: {path} ({data['count']}명)")
    if no_guide:
        print("공략 없음:", ", ".join(no_guide))
    for e in data["errors"]:
        print("오류:", e, file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
