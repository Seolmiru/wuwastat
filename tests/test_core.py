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
