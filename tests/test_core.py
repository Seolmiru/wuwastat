from wuwastat import stats as S
from wuwastat.models import Build, Character, Echo, EchoSlot, StatLine, Target
from wuwastat.parser import ParsedLine, echo_from_lines, infer_cost, merge_passes, parse_line, totals_from_lines
from wuwastat.scoring import evaluate, evaluate_echo, grade_of, substat_weights
from wuwastat.scraper import parse_priority


def phoebe() -> Character:
    return Character(
        id="1506", name="페비", element="spectro",
        targets=[Target(S.CRIT_RATE, 70.0), Target(S.CRIT_DMG, 240.0)],
        echo_slots=[EchoSlot(4, S.CRIT_DMG), EchoSlot(3, S.SPECTRO_DMG), EchoSlot(3, S.SPECTRO_DMG),
                    EchoSlot(1, S.ATK_PCT), EchoSlot(1, S.ATK_PCT)],
        substat_priority=[[S.CRIT_RATE, S.CRIT_DMG], [S.ATK_PCT], [S.HEAVY_DMG], [S.ENERGY_REGEN]],
    )


def echo(cost, main, *subs):
    return Echo(cost=cost, main=StatLine(*main), substats=[StatLine(k, v) for k, v in subs])


# --- 우선순위 문구 해석 ------------------------------------------------------
def test_parse_priority_variants():
    assert parse_priority("크확/크피>공격력 백분율>강공격 피해 보너스>공명 효율") == [
        [S.CRIT_RATE, S.CRIT_DMG], [S.ATK_PCT], [S.HEAVY_DMG], [S.ENERGY_REGEN]]
    assert parse_priority("크리티컬 확률/피해 >HP 백분율> 일반 공격 피해 보너스") == [
        [S.CRIT_RATE, S.CRIT_DMG], [S.HP_PCT], [S.BASIC_DMG]]
    assert parse_priority("공명 효율>HP 백분율>나머지") == [[S.ENERGY_REGEN], [S.HP_PCT]]
    assert parse_priority("크확/크피>공명 효율=평타 피해 보너스=공격력 백분율") == [
        [S.CRIT_RATE, S.CRIT_DMG], [S.ENERGY_REGEN, S.BASIC_DMG, S.ATK_PCT]]
    assert parse_priority("크리티컬/크리티컬 피해>공격력%>공명 해방 피해 보서스")[2] == [S.LIBERATION_DMG]
    assert parse_priority("일반 공격/강공격 피해 보너스") == [[S.BASIC_DMG, S.HEAVY_DMG]]


# --- OCR 줄 해석 -------------------------------------------------------------
def test_parse_line_basic():
    p = parse_line("크리티컬 피해 16.2%")
    assert (p.stat, p.value, p.percent) == (S.CRIT_DMG, 16.2, True)
    assert parse_line("공격력 8.6%").stat == S.ATK_PCT
    assert parse_line("공격력 40").stat == S.ATK_FLAT
    assert parse_line("HP 13,245").value == 13245
    assert parse_line("공격력 1,000 + 1,200").value == 2200


def test_parse_line_rejects_noise():
    assert parse_line("회절 피해가 10% 증가된다") is None
    assert parse_line("악몽 · 애곡하는 아익스 +25") is None
    assert parse_line("COST 4") is None
    assert parse_line("영원의 광채") is None


def test_parse_line_fuzzy_and_unknown():
    p = parse_line("공영 효율 9.2%")
    assert p.stat == S.ENERGY_REGEN and p.fuzzy
    assert parse_line("9.2%").stat == ""


def test_merge_prefers_exact_reading():
    a = [ParsedLine("", 8.6, "고 74", 100)]
    b = [ParsedLine(S.ATK_PCT, 8.6, "공격력", 102)]
    merged = merge_passes([a, b], tol=10)
    assert len(merged) == 1 and merged[0].stat == S.ATK_PCT


def test_echo_from_lines_and_cost():
    lines = [ParsedLine(S.CRIT_DMG, 44.0, "", 0, True), ParsedLine(S.ATK_FLAT, 150, "", 1),
             ParsedLine(S.CRIT_RATE, 8.1, "", 2, True), ParsedLine("", 9.2, "", 3, True)]
    e = echo_from_lines(lines)
    assert e.cost == 4 and e.main.stat == S.CRIT_DMG
    assert [s.stat for s in e.substats] == [S.CRIT_RATE, ""]
    assert infer_cost(StatLine(S.ATK_PCT, 18.0), StatLine(S.HP_FLAT, 2280)) == 1
    assert infer_cost(StatLine(S.ATK_PCT, 30.0), StatLine(S.ATK_FLAT, 100)) == 3
    assert infer_cost(StatLine(S.ATK_PCT, 33.0), StatLine(S.ATK_FLAT, 150)) == 4


def test_totals_from_lines():
    lines = [ParsedLine(S.HP_FLAT, 13245, "", 0), ParsedLine(S.ATK_FLAT, 2105, "", 1),
             ParsedLine(S.CRIT_RATE, 63.2, "", 2, True)]
    assert totals_from_lines(lines) == {S.HP: 13245, S.ATK: 2105, S.CRIT_RATE: 63.2}


# --- 점수 --------------------------------------------------------------------
def test_weights_include_flat_at_half():
    w = substat_weights(phoebe())
    assert w[S.CRIT_RATE] == 1.0 and w[S.ATK_PCT] == 0.8
    assert w[S.ATK_FLAT] == 0.4
    assert S.HP_PCT not in w


def test_perfect_echo_scores_100():
    c = phoebe()
    e = echo(4, (S.CRIT_DMG, 44.0), (S.CRIT_RATE, 10.5), (S.CRIT_DMG, 21.0), (S.ATK_PCT, 11.6),
             (S.HEAVY_DMG, 11.6), (S.ENERGY_REGEN, 12.4))
    r = evaluate_echo(c, e, 0)
    assert round(r.score) == 100 and r.main_ok


def test_main_mismatch_penalized():
    c = phoebe()
    good = echo(3, (S.SPECTRO_DMG, 30.0), (S.CRIT_RATE, 8.1), (S.CRIT_DMG, 16.2))
    bad = echo(3, (S.ATK_PCT, 30.0), (S.CRIT_RATE, 8.1), (S.CRIT_DMG, 16.2))
    g, b = evaluate_echo(c, good, 0), evaluate_echo(c, bad, 1)
    assert not b.main_ok and b.score < g.score
    # 4코스트는 크리티컬/크리티컬 피해 서로 인정
    assert evaluate_echo(c, echo(4, (S.CRIT_RATE, 22.0)), 0).main_ok


def test_evaluate_orders_replacement_and_advice():
    c = phoebe()
    echoes = [
        echo(4, (S.CRIT_DMG, 44.0), (S.CRIT_RATE, 9.3), (S.CRIT_DMG, 18.6), (S.ATK_PCT, 9.4), (S.HEAVY_DMG, 8.6), (S.ATK_FLAT, 50)),
        echo(3, (S.SPECTRO_DMG, 30.0), (S.CRIT_RATE, 8.1), (S.CRIT_DMG, 16.2), (S.ATK_PCT, 7.9), (S.DEF_FLAT, 50), (S.HP_FLAT, 430)),
        echo(3, (S.ATK_PCT, 30.0), (S.DEF_PCT, 10.9), (S.HP_PCT, 8.6), (S.DEF_FLAT, 60), (S.HP_FLAT, 390), (S.ENERGY_REGEN, 6.8)),
        echo(1, (S.ATK_PCT, 18.0), (S.CRIT_RATE, 6.9), (S.ATK_FLAT, 40), (S.HP_PCT, 7.1), (S.DEF_PCT, 9.0), (S.HP_FLAT, 360)),
        echo(1, (S.ATK_PCT, 18.0), (S.CRIT_DMG, 13.8), (S.CRIT_RATE, 7.5), (S.ATK_PCT, 8.6), (S.ENERGY_REGEN, 8.4), (S.ATK_FLAT, 40)),
    ]
    ev = evaluate(c, Build({S.CRIT_RATE: 60.0, S.CRIT_DMG: 250.0}, echoes))
    assert ev.replace_order[0].index == 2  # 메인옵션 틀리고 점수도 최하
    assert ev.targets[0].deficit == 10.0 and ev.targets[1].achieved
    titles = [a.title for a in ev.advice]
    assert titles[0].startswith("1순위 교체: 3번")
    assert any("크리티컬 10.0% 부족" in t for t in titles)
    assert ev.grade == grade_of(ev.total_score)


def test_missing_total_reported():
    ev = evaluate(phoebe(), Build({}, []))
    assert ev.target_score is None and any("값 없음" in a.title for a in ev.advice)


# --- 공식 확률 기반 에코 점수 --------------------------------------------------
def test_official_probability_tables():
    for k in S.SUBSTAT_TYPES:
        assert abs(sum(S.SUBSTAT_PROBS[k]) - 100) < 0.01
        assert len(S.SUBSTAT_PROBS[k]) == len(S.SUBSTAT_ROLLS[k])
    assert len(S.SUBSTAT_TYPES) == 13
    assert round(S.SUBSTAT_EXPECTED[S.CRIT_RATE], 2) == 7.53  # 낮은 수치가 더 잘 나온다


def test_exact_distribution_matches_monte_carlo():
    import numpy as np
    from wuwastat.scoring import _ideal, percentile, raw_at_percentile

    w = substat_weights(phoebe())
    rng = np.random.default_rng(1)
    n = 40000
    picked = np.argsort(rng.random((n, 13)), axis=1)[:, :5]
    total = np.zeros(n)
    for j, stat in enumerate(S.SUBSTAT_TYPES):
        if not w.get(stat):
            continue
        p = np.array(S.SUBSTAT_PROBS[stat]) / 100
        vals = rng.choice(S.SUBSTAT_ROLLS[stat], size=n, p=p / p.sum())
        total += np.where((picked == j).any(axis=1), w[stat] * vals / S.SUBSTAT_MAX[stat], 0)
    total = total / _ideal(w) * 100
    for q in (50, 80, 92):
        assert abs(np.percentile(total, q) - raw_at_percentile(q, w)) < 1.0
    assert percentile(raw_at_percentile(80, w), w) >= 79


def test_echo_grade_is_rarity_based():
    c = phoebe()
    double_crit = echo(4, (S.CRIT_DMG, 44.0), (S.CRIT_RATE, 8.1), (S.CRIT_DMG, 16.2),
                       (S.DEF_FLAT, 50), (S.HP_FLAT, 430), (S.HP_PCT, 8.6))
    r = evaluate_echo(c, double_crit, 0)
    assert 60 <= r.score < 92  # 크리티컬 2줄 + 쓸모없는 3줄 = B급 (상위 약 26%)
    junk = echo(4, (S.CRIT_DMG, 44.0), (S.DEF_FLAT, 50), (S.HP_FLAT, 430), (S.HP_PCT, 8.6), (S.DEF_PCT, 10.9), (S.ATK_FLAT, 40))
    assert evaluate_echo(c, junk, 0).score < 30


def test_unmet_target_caps_grade_at_a():
    c = phoebe()
    top = echo(4, (S.CRIT_DMG, 44.0), (S.CRIT_RATE, 10.5), (S.CRIT_DMG, 21.0), (S.ATK_PCT, 11.6),
               (S.HEAVY_DMG, 11.6), (S.ENERGY_REGEN, 12.4))
    ev = evaluate(c, Build({S.CRIT_RATE: 69.9, S.CRIT_DMG: 300.0}, [top] * 5))
    assert ev.total_score >= 80 and ev.grade == "A" and ev.grade_capped
    ev2 = evaluate(c, Build({S.CRIT_RATE: 70.0, S.CRIT_DMG: 300.0}, [top] * 5))
    assert ev2.grade in ("S", "SS", "SSS") and not ev2.grade_capped


def test_energy_regen_advice_is_substat_only():
    c = phoebe()
    c.targets.append(Target(S.ENERGY_REGEN, 130.0))
    ev = evaluate(c, Build({S.CRIT_RATE: 70.0, S.CRIT_DMG: 240.0, S.ENERGY_REGEN: 129.2}, []))
    er = next(a for a in ev.advice if a.title.startswith("공명 효율"))
    assert "서브옵션 1줄" in er.detail
    assert "무기" not in er.detail and "세트" not in er.detail and "메인옵션" not in er.detail


def test_replacement_needs_ignore_recommended_main():
    from wuwastat.scoring import TargetEval, _replacement_needs

    e = echo(4, (S.CRIT_RATE, 22.0), (S.ENERGY_REGEN, 8.4), (S.CRIT_RATE, 7.5))
    t = [TargetEval(S.CRIT_RATE, "크리티컬", 60.0, 63.9, ">="), TargetEval(S.ENERGY_REGEN, "공명 효율", 130.0, 129.2, ">=")]
    needs = _replacement_needs(e, t, main_ok=True)
    assert "크리티컬 3.6%" in needs and "공명 효율 9.2%" in needs


def test_gear_advice_signature_and_offmeta():
    from wuwastat.models import Weapon

    c = phoebe()
    c.weapons = [Weapon("전용검", 5, True, True), Weapon("대안검", 4, False, False)]
    c.sonata = ["영원의 광채 5세트"]
    titles = [a.title for a in evaluate(c, Build({}, [], weapon="대안검", sonata=["영원의 광채"])).advice]
    assert any("전용 무기" in t for t in titles) and not any("세트" in t for t in titles)
    titles = [a.title for a in evaluate(c, Build({}, [], weapon="(기타)", sonata=["(기타)"])).advice]
    assert any("추천 무기가 아닙니다" in t for t in titles) and any("추천 세트와 다릅니다" in t for t in titles)


def test_scraper_weapons_and_chains():
    from wuwastat.scraper import build_character

    avatar = {"roleGbId": "1", "texts": [{"language": "ko", "name": "테스트"}], "star": 5, "element": {"gbId": "2"}}
    info = {
        "id": 1, "weapon": {"items": [
            {"star": 5, "status": 1, "texts": [{"language": "ko", "name": "전용총"}]},
            {"star": 5, "status": 2, "texts": [{"language": "ko", "name": "범용총"}]},
        ]},
        "roleResonance": {"items": [
            {"resonanceSequence": 1, "status": 1, "texts": [{"language": "ko", "name": "하나", "description": "d1"}]},
            {"resonanceSequence": 2, "status": 2, "texts": [{"language": "ko", "name": "둘", "description": "d2"}]},
        ]},
        "roleResonanceTexts": [{"language": "ko", "recommendDescription": "<p>●1돌: 요약<br></p>"}],
    }
    r = build_character(avatar, {"language": "ko", "id": 1}, info)
    assert [(w["name"], w["signature"]) for w in r["weapons"]] == [("전용총", True), ("범용총", False)]
    assert [(c["seq"], c["core"]) for c in r["chains"]] == [(1, True), (2, False)]
    assert "1돌: 요약" in r["chain_text"]


def test_two_set_combination():
    c = phoebe()
    c.sonata = ["불타는 깃털을 펼친 사냥꾼의 그림자 3세트", "울부짖는 늑대의 불꽃 2세트"]
    full = [a.title for a in evaluate(c, Build({}, [], sonata=["불타는 깃털을 펼친 사냥꾼의 그림자", "울부짖는 늑대의 불꽃"])).advice]
    assert not any("세트" in t for t in full)
    half = [a.title for a in evaluate(c, Build({}, [], sonata=["불타는 깃털을 펼친 사냥꾼의 그림자"])).advice]
    assert any("울부짖는 늑대의 불꽃" in t and "빠져" in t for t in half)


# --- 돌파 효율 추정 -----------------------------------------------------------
def test_chain_estimate_patterns():
    from wuwastat.chain import StatContext, estimate_one, skill_shares

    c = phoebe()
    sh = skill_shares(c)
    ctx = StatContext(crit_rate=0.6, crit_dmg=2.5, atk_base_ratio=0.5)
    # 조건(변주 스킬 발동 시)은 적용 범위가 아니다 → 자기 버프 전체 적용
    g, parts = estimate_one("변주 스킬 발동 시, 크리티컬 피해가 28% 증가되며, 18초간 지속된다", sh, ctx)
    assert abs(g - (ctx.crit_mult(cd=2.78) / ctx.crit_mult() - 1)) < 1e-9 and len(parts) == 1
    # '최대 N%'는 최대치, 크리티컬 피해를 일반 피해 증가로 중복 계산하지 않음
    g, parts = estimate_one("1pt 당 강공격의 크리티컬 피해를 추가로 2% 증가시키고 최대 80%까지 증가시킬 수 있다", sh, ctx)
    assert len(parts) == 1 and "+80%" in parts[0]
    # 소수점은 문장 끝이 아니다
    g, parts = estimate_one("강공격의 용융 피해가 추가로 0.875% 부스트되고, 최대 35%까지 부스트된다", sh, ctx)
    assert "35%" in parts[0] and "100%" not in parts[0]
    # 배율 상승은 스킬 비중만큼, 같은 돌파에서 여러 번 나오면 비중을 나눈다
    one, _ = estimate_one("강공격의 피해 배율이 100% 상승된다", sh, ctx)
    two, _ = estimate_one("A 상태에서 강공격의 피해 배율이 100% 상승된다. B 상태에서 강공격의 피해 배율이 100% 상승된다", sh, ctx)
    assert abs(one - sh["heavy"]) < 1e-9 and abs(two - ((1 + sh["heavy"] / 2) ** 2 - 1)) < 1e-9
    assert estimate_one("적의 공격에 의해 중단되지 않는다", sh, ctx) == (0.0, [])


def test_chain_score_not_added_to_stats():
    from wuwastat.models import Chain

    c = phoebe()
    c.chains = [Chain(1, "a", "공격력이 20% 증가된다", True), Chain(2, "b", "알 수 없는 효과", True)]
    totals = {S.CRIT_RATE: 62.0, S.CRIT_DMG: 220.0}  # 기본 점수가 100점 미만이 되도록 목표 미달
    ev0 = evaluate(c, Build(dict(totals), [], chain=0))
    ev2 = evaluate(c, Build(dict(totals), [], chain=2))
    assert ev0.chain_gain == 0 and ev2.chain_gain > 0.08
    assert ev2.total_score == ev0.total_score  # 기본 점수/스탯은 그대로
    assert ev2.chain_score > ev0.chain_score
    assert ev2.chain_estimates[1].estimated_default


# --- 보안: 외부 데이터 방어 ----------------------------------------------------
def test_build_file_rejects_path_characters():
    from wuwastat.ui import build_file

    assert build_file("1208").name == "1208.json"
    for bad in ("../evil", "..\evil", "a/b", "C:x", "", "1" * 40):
        assert build_file(bad) is None


def test_open_link_only_official_https(monkeypatch):
    from PySide6.QtCore import QUrl
    from wuwastat import ui

    opened = []
    monkeypatch.setattr(ui.QDesktopServices, "openUrl", lambda u: opened.append(u.toString()))
    ui.open_link(QUrl("https://wuwaguide.kurogames.com/ko/?role_id=1"))
    ui.open_link(QUrl("http://wuwaguide.kurogames.com/"))
    ui.open_link(QUrl("https://evil.example.com/"))
    ui.open_link(QUrl("file:///C:/Windows/System32/cmd.exe"))
    assert opened == ["https://wuwaguide.kurogames.com/ko/?role_id=1"]


def test_scraper_drops_unsafe_role_ids(monkeypatch):
    from wuwastat import scraper

    avatars = [{"roleGbId": "1208", "texts": [{"language": "ko", "name": "정상"}]},
               {"roleGbId": "../../x", "texts": [{"language": "ko", "name": "이상"}]}]
    monkeypatch.setattr(scraper, "_get", lambda path, lang="ko", timeout=20: avatars if "avatar" in path else [])
    data = scraper.scrape_all(workers=1)
    assert [c["id"] for c in data["characters"]] == ["1208"]
