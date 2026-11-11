"""실제 게임 스크린샷 OCR 회귀 테스트 (Windows OCR이 있는 환경에서만 실행)."""

from pathlib import Path

import pytest

from wuwastat import stats as S

FIXTURES = Path(__file__).parent / "fixtures"

try:
    from wuwastat.ocr import available_languages
    HAS_OCR = "ko" in [l.split("-")[0] for l in available_languages()]
except Exception:
    HAS_OCR = False

pytestmark = pytest.mark.skipif(not HAS_OCR, reason="Windows 한국어 OCR 없음")


def test_totals_galbrena_busy_background():
    """숫자 뒤에 밝은 붓 자국이 겹친 속성 상세 화면 (갈브레나)."""
    from wuwastat.parser import parse_totals

    totals = parse_totals(FIXTURES / "totals_galbrena.png")
    assert totals == {
        S.HP: 15610, S.ATK: 2182, S.DEF: 1353,
        S.ENERGY_REGEN: 129.2, S.CRIT_RATE: 63.9, S.CRIT_DMG: 267.4,
    }


ECHOES = {
    # 파일: (이름, 코스트, 메인옵션, 서브옵션 5개)
    "echo_corosaurus.png": ("코로사우루스", 3, (S.FUSION_DMG, 30.0),
                            [(S.HEAVY_DMG, 7.9), (S.CRIT_RATE, 6.3), (S.CRIT_DMG, 12.6), (S.DEF_FLAT, 40), (S.HP_FLAT, 390)]),
    "echo_leviathan.png": ("공명의 메아리 · 명식 · 레비아탄", 4, (S.CRIT_RATE, 22.0),
                           [(S.ENERGY_REGEN, 8.4), (S.HEAVY_DMG, 7.1), (S.DEF_FLAT, 40), (S.CRIT_RATE, 7.5), (S.ATK_FLAT, 40)]),
    "echo_chameleon.png": ("악몽 · 그린멜팅카멜레온", 3, (S.FUSION_DMG, 30.0),
                           [(S.CRIT_DMG, 13.8), (S.SKILL_DMG, 10.9), (S.DEF_FLAT, 50), (S.CRIT_RATE, 7.5), (S.ENERGY_REGEN, 11.6)]),
    "echo_chameleon_fluid.png": ("악몽 · 그린멜팅카멜레온(유체)", 1, (S.ATK_PCT, 18.0),
                                 [(S.ATK_PCT, 10.9), (S.CRIT_RATE, 9.3), (S.CRIT_DMG, 12.6), (S.LIBERATION_DMG, 10.1), (S.HP_FLAT, 360)]),
    "echo_drake.png": ("드레이크 · 전도", 1, (S.ATK_PCT, 18.0),
                       [(S.CRIT_DMG, 13.8), (S.ENERGY_REGEN, 9.2), (S.DEF_PCT, 10.0), (S.CRIT_RATE, 6.3), (S.BASIC_DMG, 9.4)]),
}


@pytest.mark.parametrize("fname", sorted(ECHOES))
def test_echo_screens(fname):
    """에코 상세 화면: 메인옵션 1개 + 서브옵션 5개만 (고정 부옵션, 이름, 레벨 줄 제외)."""
    from wuwastat.parser import parse_echo

    name, cost, main, subs = ECHOES[fname]
    e = parse_echo(FIXTURES / fname)
    assert e.name == name
    assert e.cost == cost
    assert e.level == 25
    assert (e.main.stat, e.main.value) == main
    assert [(s.stat, s.value) for s in e.substats] == subs


def _game_frames():
    """속성 화면(갈브레나) → 에코 5개를 차례로 연 화면 (같은 에코를 다시 연 화면 포함)."""
    from PIL import Image

    base = Image.open(FIXTURES / "totals_galbrena.png").convert("RGB")
    screen = Image.new("RGB", (1920, 1080), (25, 30, 40))
    attr = screen.copy()
    attr.paste(base, (180, 330))
    from PIL import ImageDraw, ImageFont
    d = ImageDraw.Draw(attr)
    f = ImageFont.truetype("C:/Windows/Fonts/malgunbd.ttf", 26)
    d.text((105, 52), "속성 상세정보", font=f, fill=(235, 235, 235))
    d.text((212, 200), "갈브레나", font=f, fill=(235, 235, 235))
    frames = [attr]
    # 코로사우루스는 다른 에코를 본 뒤 한 번 더 연다 → 중복으로 세지 않아야 한다
    order = ["echo_leviathan", "echo_corosaurus", "echo_chameleon", "echo_corosaurus",
             "echo_chameleon_fluid", "echo_drake"]
    for name in order:
        fr = screen.copy()
        fr.paste(Image.open(FIXTURES / f"{name}.png").convert("RGB"), (1250, 300))
        frames += [fr, fr]  # 같은 화면이 두 번 연속 보여야 확정
    return frames


def test_watch_loop_reads_character_and_five_echoes(monkeypatch):
    from wuwastat import gamewatch as G
    from wuwastat.models import load_characters

    frames = iter(_game_frames())
    win = G.GameWindow(1, "Wuthering Waves", 0, 0, 1920, 1080)
    monkeypatch.setattr(G, "find_game_window", lambda: win)
    monkeypatch.setattr(G.GameWindow, "refresh", lambda self: True)
    monkeypatch.setattr(G.GameWindow, "is_foreground", property(lambda self: True))
    monkeypatch.setattr(G, "capture", lambda w: next(frames))
    events = []
    chars, _ = load_characters()
    state = G.watch(chars, events.append, cancel=lambda: False, interval=0)

    char_ev = next(e for e in events if e[0] == "character")
    assert char_ev[1] == "1208"  # 갈브레나
    assert char_ev[3][S.CRIT_DMG] == 267.4 and char_ev[3][S.ATK] == 2182
    echo_names = [e[2].name for e in events if e[0] == "echo"]
    assert echo_names == ["공명의 메아리 · 명식 · 레비아탄", "코로사우루스", "악몽 · 그린멜팅카멜레온",
                          "악몽 · 그린멜팅카멜레온(유체)", "드레이크 · 전도"]
    assert any(e[0] == "status" and "이미 읽은 에코" in e[1] for e in events)
    assert len(state.echoes) == 5


# --- 실제 게임 전체 화면 (1900x1040 전후) ---------------------------------------
def test_game_attribute_screen_with_character_art_over_stats():
    """여우의 별자리: 캐릭터 그림(꼬리·장식)이 스탯 글자 위까지 겹친 속성 상세정보 화면."""
    from PIL import Image
    from wuwastat import gamewatch as G
    from wuwastat.models import load_characters
    from wuwastat.ocr import recognize_tiled
    from wuwastat.parser import read_lines, totals_from_lines

    img = Image.open(FIXTURES / "game_attr_fox.png").convert("RGB")
    words = recognize_tiled(img)
    assert G.is_attribute_screen(words, img.height)
    char, _ = G.detect_character(words, load_characters()[0], img.width)
    assert char is not None and char.name == "여우의 별자리"
    assert totals_from_lines(read_lines(img, mode="totals")) == {
        S.HP: 15899, S.ATK: 2090, S.DEF: 1553,
        S.ENERGY_REGEN: 110.0, S.CRIT_RATE: 75.4, S.CRIT_DMG: 264.8,
    }


def test_game_echo_screen_with_many_cost_labels():
    """에코 화면: 'COST' 글자가 필터 탭·'COST 12/12'·상세 패널 등 여러 곳에 있다. 이름은 두 줄."""
    from PIL import Image
    from wuwastat import gamewatch as G
    from wuwastat.ocr import recognize_tiled

    img = Image.open(FIXTURES / "game_echo_fox.png").convert("RGB")
    words = recognize_tiled(img)
    assert not G.is_attribute_screen(words, img.height)
    e = G.read_echo_panel(G.find_echo_panel(img, words))
    assert e.name == "공명의 메아리 · 태초의 여우의 별자리 · 천연"
    assert (e.cost, e.level) == (4, 25)
    assert (e.main.stat, e.main.value) == (S.CRIT_DMG, 44.0)
    assert [(s.stat, s.value) for s in e.substats] == [
        (S.CRIT_RATE, 8.1), (S.ATK_PCT, 9.4), (S.DEF_PCT, 10.9), (S.LIBERATION_DMG, 7.1), (S.CRIT_DMG, 15.0)]


def test_watch_loop_on_real_game_screens(monkeypatch):
    from PIL import Image
    from wuwastat import gamewatch as G
    from wuwastat.models import load_characters

    attr = Image.open(FIXTURES / "game_attr_fox.png").convert("RGB")
    echo = Image.open(FIXTURES / "game_echo_fox.png").convert("RGB")
    frames = iter([attr, echo, echo])
    win = G.GameWindow(1, "Wuthering Waves", 0, 0, *attr.size)
    monkeypatch.setattr(G, "find_game_window", lambda: win)
    monkeypatch.setattr(G.GameWindow, "refresh", lambda self: True)
    monkeypatch.setattr(G.GameWindow, "is_foreground", property(lambda self: True))
    stop = {"n": 0}

    def capture(w):
        try:
            return next(frames)
        except StopIteration:
            stop["n"] = 1
            return echo

    monkeypatch.setattr(G, "capture", capture)
    events = []
    G.watch(load_characters()[0], events.append, cancel=lambda: stop["n"] == 1, interval=0)
    char_ev = next(e for e in events if e[0] == "character")
    assert char_ev[1] == "1311" and char_ev[3][S.CRIT_RATE] == 75.4  # 여우의 별자리
    echoes = [e[2] for e in events if e[0] == "echo"]
    assert len(echoes) == 1 and echoes[0].main.value == 44.0
