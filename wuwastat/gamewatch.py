"""세팅 자동 측정: 명조 창 화면을 읽기만 해서 캐릭터 스탯과 에코를 자동으로 인식한다.

게임에 마우스·키보드 입력을 보내지 않는다. 화면 픽셀을 읽는 방식은 화면 녹화 프로그램과 같고,
캡처한 화면은 메모리에서만 쓰고 파일로 저장하지 않는다.

사용 흐름
1. 앱에서 '세팅 자동 측정 시작'을 누른다.
2. 게임에서 캐릭터의 '속성 상세정보' 화면을 연다 → 캐릭터 이름과 총 스탯을 읽는다.
3. 에코 화면으로 넘어가 에코 5개를 차례로 클릭한다 → 새 에코가 보일 때마다 1~5번 칸에 채운다.
4. 에코 5개를 모두 읽거나 '자동 측정 중지'를 누르면 끝난다.

주의
- 게임 창이 맨 앞에 있을 때만 읽는다 (다른 창이 가리면 그 창이 찍히므로).
- 전체 화면(독점) 모드에서는 캡처가 검게 나올 수 있다. 창 모드나 테두리 없는 창 모드를 쓰자.
"""

from __future__ import annotations

import ctypes
import re
import time
from ctypes import wintypes
from dataclasses import dataclass, field
from difflib import SequenceMatcher
from typing import Callable

from PIL import Image, ImageGrab

from . import stats as S
from .models import Character, Echo
from .ocr import Word, recognize, recognize_tiled
from .parser import echo_from_lines, group_rows, read_lines, totals_from_lines

ATTR_SCREEN_WORDS = ("속성", "상세정보")
GAME_TITLES = ("Wuthering Waves", "鸣潮", "명조")
GAME_WINDOW_CLASS = "UnrealWindow"  # 명조(언리얼 엔진) 게임 창의 창 클래스
OWN_TITLE_MARKERS = ("등급 계산기", "wuwastat")
POLL_INTERVAL = 0.5
ECHO_COUNT = 5


class WatchError(RuntimeError):
    pass


# ---------------------------------------------------------------------------
# 게임 창 찾기 / 캡처
#
# 게임 프로세스에는 전혀 접근하지 않는다 (OpenProcess, 메모리 읽기, DLL 주입, 후킹 없음).
# 쓰는 것은 창 관리자 정보 조회(창 목록·제목·클래스·위치·맨 앞 창 여부)와
# 데스크톱 화면 픽셀 읽기(GDI BitBlt, 화면 녹화·캡처 프로그램과 같은 방식)뿐이다.
# ---------------------------------------------------------------------------
user32 = ctypes.WinDLL("user32", use_last_error=True)


@dataclass
class GameWindow:
    hwnd: int
    title: str
    left: int = 0
    top: int = 0
    width: int = 0
    height: int = 0

    def refresh(self) -> bool:
        if not user32.IsWindow(self.hwnd):
            return False
        rect = wintypes.RECT()
        user32.GetClientRect(self.hwnd, ctypes.byref(rect))
        pt = wintypes.POINT(0, 0)
        user32.ClientToScreen(self.hwnd, ctypes.byref(pt))
        self.left, self.top, self.width, self.height = pt.x, pt.y, rect.right, rect.bottom
        return True

    @property
    def is_foreground(self) -> bool:
        return user32.GetForegroundWindow() == self.hwnd and not user32.IsIconic(self.hwnd)


def _class_name(hwnd: int) -> str:
    buf = ctypes.create_unicode_buffer(256)
    user32.GetClassNameW(hwnd, buf, 256)
    return buf.value


def is_game_window(title: str, class_name: str) -> bool:
    """명조 게임 창인가. 브라우저 탭 제목('Wuthering Waves - 위키 - Chrome')은 제외한다."""
    t = title.strip()
    if any(m in t for m in OWN_TITLE_MARKERS):
        return False
    if class_name == GAME_WINDOW_CLASS and any(g.lower() in t.lower() for g in GAME_TITLES):
        return True
    return t in GAME_TITLES  # 창 클래스가 다르더라도 제목이 정확히 게임 이름이면


def find_game_window() -> GameWindow | None:
    found: list[GameWindow] = []

    @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    def cb(hwnd, _):
        if not user32.IsWindowVisible(hwnd):
            return True
        n = user32.GetWindowTextLengthW(hwnd)
        buf = ctypes.create_unicode_buffer(n + 1)
        user32.GetWindowTextW(hwnd, buf, n + 1)
        title = buf.value
        if is_game_window(title, _class_name(hwnd)):
            found.append(GameWindow(hwnd, title))
        return True

    user32.EnumWindows(cb, 0)
    if not found:
        return None
    found[0].refresh()
    return found[0]


def capture(win: GameWindow) -> Image.Image | None:
    """게임 창 클라이언트 영역을 메모리로 캡처한다 (파일로 저장하지 않음). 읽을 수 없으면 None."""
    if not win.refresh() or win.width < 200 or win.height < 200:
        return None
    img = ImageGrab.grab(bbox=(win.left, win.top, win.left + win.width, win.top + win.height), all_screens=True)
    if img.convert("L").getextrema()[1] < 16:
        raise WatchError("게임 화면이 검게 캡처됩니다. 게임을 창 모드나 테두리 없는 창 모드로 바꿔 주세요.")
    return img


# ---------------------------------------------------------------------------
# 화면 해석
# ---------------------------------------------------------------------------
def _norm(s: str) -> str:
    return re.sub(r"[\s·.\-:：]", "", s)


def is_attribute_screen(words: list[Word], height: int) -> bool:
    """화면 위쪽 15% 안에 '속성 상세정보' 제목이 있는가."""
    text = _norm("".join(w.text for w in sorted(words, key=lambda w: (w.y, w.x)) if w.cy < height * 0.15))
    return all(k in text for k in ATTR_SCREEN_WORDS)


def detect_character(words: list[Word], characters: list[Character], img_width: int) -> tuple[Character | None, str]:
    """화면 왼쪽의 캐릭터 이름을 캐릭터 목록과 비교한다. 방랑자는 이름이 닉네임이라 속성으로 고른다.

    반환: (캐릭터, 화면에서 읽은 이름)
    """
    rows = [r for r in group_rows(words) if r.words[0].x < img_width * 0.45]
    best, best_r, seen = None, 0.0, ""
    for r in rows:
        t = _norm(r.text)
        if not t or len(t) > 20:
            continue
        for c in characters:
            if c.name.startswith("방랑자"):
                continue
            for name in (c.name, c.name_en):
                n = _norm(name)
                if not n:
                    continue
                ratio = 1.0 if t == n else SequenceMatcher(None, t, n).ratio()
                if ratio > best_r:
                    best, best_r, seen = c, ratio, r.text
    if best and best_r >= 0.75:
        return best, seen
    for r in rows[:12]:
        t = _norm(r.text)
        for el, ko in S.ELEMENT_KO.items():
            if t == ko:
                rover = next((c for c in characters if c.name.startswith("방랑자") and c.element == el), None)
                if rover:
                    return rover, r.text
    return None, ""


def find_echo_panel(img: Image.Image, words: list[Word]) -> Image.Image | None:
    """'COST' 글자를 기준으로 에코 상세 패널(이름 ~ 서브옵션 5개)을 잘라낸다.

    1080p 에코 상세 패널에서 'COST' 글자 폭은 약 52px이고, 패널은 그 왼쪽 0.6배 ~ 오른쪽 7.4배,
    위쪽 1.3배(에코 이름 줄) ~ 아래쪽 6배(마지막 서브옵션 줄) 범위에 있다.
    """
    cost = [w for w in words if "COST" in w.text.upper()]
    if not cost:
        return None
    c = min(cost, key=lambda w: w.x)
    unit = c.w
    box = (c.x - 0.6 * unit, c.y - 1.3 * unit, c.x + 7.4 * unit, c.y + 6.0 * unit)
    box = tuple(int(v) for v in (max(box[0], 0), max(box[1], 0), min(box[2], img.width), min(box[3], img.height)))
    if box[2] - box[0] < unit * 3 or box[3] - box[1] < unit * 3:
        return None
    return img.crop(box)


def read_echo_panel(panel: Image.Image) -> Echo:
    lines = read_lines(panel, mode="echo")
    header = "\n".join(r.text for r in group_rows(recognize(panel, variant="clean"))[:3])
    return echo_from_lines(lines, header)


def echo_signature(e: Echo) -> tuple:
    """같은 에코를 두 번 세지 않기 위한 식별값."""
    main = (e.main.stat, e.main.value) if e.main else None
    return (_norm(e.name), e.cost, main, tuple((s.stat, s.value) for s in e.substats))


def is_complete_echo(e: Echo) -> bool:
    return bool(e.main and e.main.stat and e.substats)


# ---------------------------------------------------------------------------
# 감시 루프
# ---------------------------------------------------------------------------
@dataclass
class WatchState:
    character_id: str | None = None
    totals: dict[str, float] = field(default_factory=dict)
    echoes: list[Echo] = field(default_factory=list)
    signatures: list[tuple] = field(default_factory=list)


EventFn = Callable[[tuple], None]


def watch(characters: list[Character], emit: EventFn, cancel: Callable[[], bool],
          interval: float = POLL_INTERVAL) -> WatchState:
    """감시를 시작한다. 이벤트:
    ("status", 메시지)
    ("character", 캐릭터 ID 또는 None, 화면에서 읽은 이름, 총 스탯)
    ("echo", 번호(0~4), Echo)
    """
    state = WatchState()
    win = find_game_window()
    if win is None:
        raise WatchError("명조 게임 창을 찾지 못했습니다. 게임을 실행한 뒤 다시 시도하세요.")
    emit(("status", "자동 측정 중: 게임에서 캐릭터의 속성 상세정보 화면을 여세요."))

    last_panel_text = None
    last_attr_text = None
    pending: tuple | None = None  # 에코 화면 전환 중 덜 그려진 화면을 피하려고 두 번 연속 같으면 확정

    while not cancel():
        time.sleep(interval)
        if not win.refresh():
            raise WatchError("게임 창이 닫혔습니다.")
        if not win.is_foreground:
            continue
        img = capture(win)
        if img is None:
            continue
        words = recognize_tiled(img)

        if is_attribute_screen(words, img.height):
            pending = None
            key = _norm("".join(w.text for w in words if w.cx < img.width * 0.45))
            if key == last_attr_text:
                continue
            last_attr_text = key
            char, seen = detect_character(words, characters, img.width)
            emit(("status", f"캐릭터 화면 인식: {seen or '이름 확인 불가'} · 총 스탯 읽는 중"))
            totals = totals_from_lines(read_lines(img, mode="totals"))
            cid = char.id if char else None
            if cid != state.character_id:  # 다른 캐릭터로 바뀌면 에코를 새로 모은다
                state.echoes.clear()
                state.signatures.clear()
            state.character_id, state.totals = cid, totals
            emit(("character", cid, seen, totals))
            emit(("status", "총 스탯을 읽었습니다. 에코 화면으로 넘어가 에코를 하나씩 클릭하세요."))
            continue

        panel = find_echo_panel(img, words)
        if panel is None:
            pending = None
            continue
        panel_text = _norm("".join(w.text for w in recognize(panel, variant="clean")))
        if panel_text == last_panel_text:
            continue  # 화면이 그대로면 다시 읽지 않는다
        if pending != panel_text:
            pending = panel_text  # 한 번 더 같은 화면이 보이면 읽는다
            continue
        last_panel_text = panel_text
        emit(("status", "에코 읽는 중"))
        echo = read_echo_panel(panel)
        if not is_complete_echo(echo):
            emit(("status", "에코를 읽지 못했습니다. 에코 상세 정보가 잘 보이게 해 주세요."))
            continue
        sig = echo_signature(echo)
        if sig in state.signatures:
            emit(("status", f"이미 읽은 에코입니다 ({echo.name or '이름 없음'}). 다음 에코를 클릭하세요."))
            continue
        idx = len(state.echoes)
        state.echoes.append(echo)
        state.signatures.append(sig)
        emit(("echo", idx, echo))
        if len(state.echoes) >= ECHO_COUNT:
            emit(("status", "에코 5개를 모두 읽었습니다."))
            break
        emit(("status", f"{idx + 1}번 에코를 읽었습니다 ({echo.name or '이름 없음'}). 다음 에코를 클릭하세요."))
    return state
