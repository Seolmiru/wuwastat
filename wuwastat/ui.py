"""PySide6 데스크톱 UI."""

from __future__ import annotations

import html
import io
import json
import re
import sys
import traceback
from dataclasses import asdict
from pathlib import Path
from typing import Callable

from PIL import Image
from PySide6.QtCore import QBuffer, QIODevice, QObject, QRunnable, QSettings, QSize, QStandardPaths, Qt, QThreadPool, QTimer, QUrl, Signal
from PySide6.QtGui import QDesktopServices, QFont, QIcon, QImage, QKeySequence, QPixmap, QShortcut
from PySide6.QtWidgets import (
    QAbstractItemView, QApplication, QCheckBox, QComboBox, QDialog, QDialogButtonBox, QCompleter, QDoubleSpinBox, QFileDialog, QFrame, QGridLayout,
    QGroupBox, QHBoxLayout, QHeaderView, QLabel, QLineEdit, QMainWindow, QMessageBox, QProgressBar, QProgressDialog,
    QPushButton, QScrollArea, QSplitter, QTableWidget, QTableWidgetItem, QTabWidget, QTextBrowser, QVBoxLayout,
    QSizePolicy, QToolButton, QWidget,
)

from . import __version__
from . import stats as S
from .models import LOCAL_DATA, Build, Character, Echo, StatLine, data_path, load_characters, load_party_modes, party_modes
from . import scoring as sc
from . import updater
from .scoring import Evaluation, evaluate

IMAGE_FILTER = "이미지 (*.png *.jpg *.jpeg *.bmp *.webp)"
GRADE_COLORS = {"S": "#c0392b", "A": "#8e44ad", "B": "#2980b9", "C": "#27ae60", "-": "#7f8c8d"}
# 화면 색 (어두운 테마 하나로 고정해 어떤 Windows 설정에서도 같은 대비로 보이게 한다)
BG = "#15171c"          # 창 바탕
PANEL = "#1f2229"       # 카드(구역) 바탕
PANEL_ALT = "#262a32"   # 표 줄무늬, 탭 등
INPUT_BG = "#181a20"
BORDER = "#363b46"
TEXT = "#e9ecf1"
ACCENT = "#4da3ff"      # 구역 제목, 주요 버튼, 선택 표시
MUTED_COLOR = "#a6b0bd"  # 보조 문구 (어두운 바탕에서도 읽히도록 밝은 회색)
# 캐릭터 속성별 강조색
ELEMENT_COLORS = {"glacio": "#5cc8f0", "fusion": "#f07848", "electro": "#b77cf5",
                  "aero": "#4fd9a8", "spectro": "#ecc94b", "havoc": "#e0609a"}
WARN_COLOR = "#e67e22"
APP_FONT_PT = 11  # 기본 글자 크기
FONT_PT_RANGE = (8, 20)
_font_listeners: list[Callable[[], None]] = []  # 글자 크기가 바뀌면 다시 그릴 위젯들


def base_pt() -> int:
    app = QApplication.instance()
    return app.font().pointSize() if app and app.font().pointSize() > 0 else APP_FONT_PT


def fs(delta: int) -> str:
    """기본 글자 크기 기준 상대 크기 (HTML용)."""
    return f"{max(base_pt() + delta, 6)}pt"


def saved_font_pt() -> int:
    v = QSettings().value("ui/font_pt", APP_FONT_PT)
    try:
        return min(max(int(v), FONT_PT_RANGE[0]), FONT_PT_RANGE[1])
    except (TypeError, ValueError):
        return APP_FONT_PT


def apply_font_pt(pt: int) -> None:
    app = QApplication.instance()
    font = app.font()
    font.setPointSize(pt)
    app.setFont(font)
    app.setStyleSheet(app_style(pt))  # 스타일 다시 적용해 모든 위젯 글꼴 갱신
    QSettings().setValue("ui/font_pt", pt)
    for f in list(_font_listeners):
        f()
def app_style(pt: int | None = None) -> str:
    """전체 화면 스타일. 구역은 카드로 나누고, 제목·주요 버튼·선택 상태에 강조색을 쓴다."""
    pt = pt or base_pt()
    return f"""
QToolTip {{ background: {PANEL_ALT}; color: {TEXT}; border: 1px solid {ACCENT}; padding: 4px; }}
QGroupBox {{ background: {PANEL}; border: 1px solid {BORDER}; border-top: 3px solid {ACCENT}; border-radius: 8px;
            margin-top: 6px; padding: {pt * 2 + 14}px 8px 8px 8px; font-weight: bold; font-size: {pt + 2}pt; }}
QGroupBox[untitled="true"] {{ padding-top: 8px; }}
QGroupBox::title {{ subcontrol-origin: padding; subcontrol-position: top left; left: 10px; top: 8px;
                   color: {ACCENT}; }}
QLabel {{ background: transparent; }}
QHeaderView::section {{ background: #2d323c; color: {TEXT}; font-weight: bold; padding: 6px 8px;
                       border: none; border-right: 1px solid {BORDER}; border-bottom: 2px solid {ACCENT}; }}
QTableWidget {{ background: {INPUT_BG}; alternate-background-color: {PANEL_ALT}; gridline-color: {BORDER};
               border: 1px solid {BORDER}; border-radius: 6px; }}
QTabWidget::pane {{ border: 1px solid {BORDER}; border-radius: 6px; background: {PANEL}; top: -1px; }}
QTabBar::tab {{ background: {PANEL_ALT}; color: {MUTED_COLOR}; padding: 7px 16px; margin-right: 2px;
               border: 1px solid {BORDER}; border-bottom: none; border-top-left-radius: 6px; border-top-right-radius: 6px; }}
QTabBar::tab:selected {{ background: {PANEL}; color: {TEXT}; font-weight: bold; border-top: 3px solid {ACCENT}; }}
QTabBar::tab:hover:!selected {{ color: {TEXT}; }}
QPushButton {{ background: #2d323c; color: {TEXT}; border: 1px solid #454c5a; border-radius: 6px; padding: 6px 14px; }}
QPushButton:hover {{ background: #384050; border-color: {ACCENT}; }}
QPushButton:pressed {{ background: #232831; }}
QPushButton[primary="true"] {{ background: {ACCENT}; color: white; border-color: {ACCENT}; font-weight: bold; }}
QPushButton[primary="true"]:hover {{ background: #6cb4ff; }}
QPushButton[primary="true"]:checked {{ background: #d9534f; border-color: #d9534f; }}
QComboBox, QLineEdit {{ background: {INPUT_BG}; color: {TEXT}; border: 1px solid #454c5a;
                        border-radius: 5px; min-height: 26px; padding: 1px 6px; }}
QComboBox:focus, QLineEdit:focus {{ border-color: {ACCENT}; }}
QAbstractSpinBox {{ min-height: 26px; }}
QComboBox QAbstractItemView {{ background: {PANEL}; color: {TEXT}; selection-background-color: {ACCENT};
                               border: 1px solid {BORDER}; }}
QProgressBar {{ background: {INPUT_BG}; border: 1px solid {BORDER}; border-radius: 5px; text-align: center;
               color: white; font-weight: bold; margin: 3px 4px; }}
QTextBrowser {{ background: {PANEL}; border: 1px solid {BORDER}; border-radius: 8px; padding: 6px; }}
QScrollArea {{ border: none; background: transparent; }}
QScrollArea > QWidget > QWidget {{ background: transparent; }}
QSplitter::handle {{ background: {BG}; width: 6px; }}
QStatusBar {{ background: #101216; color: {MUTED_COLOR}; }}
"""


def apply_theme(app: QApplication) -> None:
    """Fusion 스타일 + 어두운 팔레트 (Windows 밝은/어두운 모드와 관계없이 같은 모습)."""
    from PySide6.QtGui import QColor, QPalette
    app.setStyle("Fusion")
    pal = QPalette()
    roles = {QPalette.Window: BG, QPalette.WindowText: TEXT, QPalette.Base: INPUT_BG,
             QPalette.AlternateBase: PANEL_ALT, QPalette.Text: TEXT, QPalette.Button: "#2d323c",
             QPalette.ButtonText: TEXT, QPalette.Highlight: ACCENT, QPalette.HighlightedText: "#ffffff",
             QPalette.ToolTipBase: PANEL_ALT, QPalette.ToolTipText: TEXT, QPalette.PlaceholderText: "#7d8794",
             QPalette.Link: ACCENT, QPalette.Mid: BORDER, QPalette.Midlight: BORDER}
    for role, color in roles.items():
        pal.setColor(role, QColor(color))
    pal.setColor(QPalette.Disabled, QPalette.Text, QColor("#6b7380"))
    pal.setColor(QPalette.Disabled, QPalette.ButtonText, QColor("#6b7380"))
    app.setPalette(pal)
    app.setStyleSheet(app_style())

# 서브옵션 우선순위 1순위부터 (밝은/어두운 배경 모두에서 잘 보이는 진한 색)
PRIORITY_COLORS = ["#e74c3c", "#e67e22", "#d4a017", "#27ae60", "#2e86de", "#8e44ad"]
SUBSTAT_KEYS = [S.CRIT_RATE, S.CRIT_DMG, S.ATK_PCT, S.ATK_FLAT, S.HP_PCT, S.HP_FLAT, S.DEF_PCT, S.DEF_FLAT,
                S.ENERGY_REGEN, S.BASIC_DMG, S.HEAVY_DMG, S.SKILL_DMG, S.LIBERATION_DMG]
ECHO_COUNT = 5


def extra_substats_path() -> Path:
    return builds_dir().parent / "extra_substats.json"


def load_extra_substats() -> dict[str, list[str]]:
    """캐릭터 id → 유저가 추가한 유효 서브옵션. 캐릭터 데이터를 업데이트해도 유지된다."""
    try:
        d = json.loads(extra_substats_path().read_text(encoding="utf-8"))
        return {str(k): [x for x in v if x in S.SUBSTAT_MAX] for k, v in d.items() if isinstance(v, list)}
    except (OSError, ValueError, AttributeError):
        return {}


def save_extra_substats(data: dict[str, list[str]]) -> None:
    extra_substats_path().write_text(json.dumps({k: v for k, v in data.items() if v}, ensure_ascii=False, indent=2),
                                     encoding="utf-8")


def builds_dir() -> Path:
    base = Path(QStandardPaths.writableLocation(QStandardPaths.AppDataLocation) or Path.home() / ".wuwastat")
    p = base / "builds"
    p.mkdir(parents=True, exist_ok=True)
    return p


SAFE_ID = re.compile(r"[0-9A-Za-z_-]{1,32}")
ALLOWED_LINK_HOSTS = {"wuwaguide.kurogames.com"}


def build_file(char_id: str) -> Path | None:
    """캐릭터별 입력 저장 파일. ID는 원격 데이터에서 오므로 경로 문자를 허용하지 않는다."""
    if not SAFE_ID.fullmatch(char_id or ""):
        return None
    return builds_dir() / f"{char_id}.json"


def open_link(url: QUrl) -> None:
    """캐릭터 정보의 링크는 공식 공략 사이트(https)만 연다."""
    if url.scheme() == "https" and url.host() in ALLOWED_LINK_HOSTS:
        QDesktopServices.openUrl(url)


def plain_label(text: str = "") -> QLabel:
    """OCR 결과나 오류 문구를 보여주는 라벨: HTML로 해석하지 않는다."""
    lb = QLabel(text)
    lb.setTextFormat(Qt.PlainText)
    lb.setWordWrap(True)
    return lb


def qimage_to_pil(img: QImage) -> Image.Image:
    buf = QBuffer()
    buf.open(QIODevice.WriteOnly)
    img.save(buf, "PNG")
    return Image.open(io.BytesIO(bytes(buf.data()))).copy()


# ---------------------------------------------------------------------------
# 백그라운드 작업
# ---------------------------------------------------------------------------
class WorkerSignals(QObject):
    done = Signal(object)
    error = Signal(str)
    progress = Signal(object)


class Worker(QRunnable):
    def __init__(self, fn: Callable, *args, **kwargs):
        super().__init__()
        self.fn, self.args, self.kwargs = fn, args, kwargs
        self.signals = WorkerSignals()

    def run(self) -> None:
        try:
            result = self.fn(*self.args, progress=self.signals.progress.emit, **self.kwargs)
        except Exception as e:  # UI에 오류를 보여준다
            traceback.print_exc()
            self.signals.error.emit(f"{type(e).__name__}: {e}")
        else:
            self.signals.done.emit(result)


def _check_release(progress):
    return updater.check_latest()


def _ocr_totals(image, progress):
    from .parser import parse_totals
    return parse_totals(image, progress=progress)


def _ocr_echo(image, progress):
    from .parser import parse_echo
    return parse_echo(image, progress=progress)


PORTRAIT_SIZE = 120
ECHO_ICON_SIZE = 72


def portrait_label(size: int) -> QLabel:
    lb = QLabel()
    lb.setFixedSize(size, size)
    lb.setAlignment(Qt.AlignCenter)
    lb.setStyleSheet(f"border: 1px solid {BORDER}; border-radius: 6px; color: {MUTED_COLOR}; background: {INPUT_BG};")
    return lb


def _watch(characters, cancel, progress):
    from .gamewatch import watch
    return watch(characters, progress, cancel)


def _scrape(progress):
    from .scraper import load_previous, save, scrape_all
    data = scrape_all(lambda i, n, name: progress((i, n, name)), previous=load_previous(data_path()))
    save(data, LOCAL_DATA)
    return data


# ---------------------------------------------------------------------------
# 입력 위젯
# ---------------------------------------------------------------------------
class NoWheelCombo(QComboBox):
    """마우스 휠로 선택이 바뀌지 않는 콤보박스 (휠은 화면 스크롤에 넘긴다)."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.setFocusPolicy(Qt.StrongFocus)

    def wheelEvent(self, event) -> None:
        event.ignore()


class StatCombo(NoWheelCombo):
    def set_keys(self, keys: list[str], blank: bool = True) -> None:
        current = self.key()
        self.blockSignals(True)
        self.clear()
        if blank:
            self.addItem("(선택)", "")
        for k in keys:
            self.addItem(S.label(k), k)
        self.blockSignals(False)
        self.set_key(current)

    def key(self) -> str:
        return self.currentData() or ""

    def set_key(self, key: str) -> None:
        i = self.findData(key)
        if i < 0 and key:
            self.addItem(S.label(key), key)
            i = self.count() - 1
        self.setCurrentIndex(max(i, 0))


class ValueSpin(QDoubleSpinBox):
    def __init__(self):
        super().__init__()
        self.setRange(0, 999999)
        self.setDecimals(1)
        self.setButtonSymbols(QDoubleSpinBox.UpDownArrows)  # 옆의 상하 버튼으로 조절
        self.setFocusPolicy(Qt.StrongFocus)
        self.setMinimumWidth(95)

    def wheelEvent(self, event) -> None:  # 마우스 휠로 값이 바뀌지 않게 하고, 휠은 화면 스크롤에 넘긴다
        event.ignore()

    def set_stat(self, key: str) -> None:
        pct = key in S.PERCENT_STATS
        self.setSuffix(" %" if pct else "")
        self.setSingleStep(0.1 if pct else 1)


class DropMixin:
    """이미지 파일 드래그 앤 드롭."""

    image_dropped: Signal

    def dragEnterEvent(self, e):  # noqa: N802
        if e.mimeData().hasUrls() or e.mimeData().hasImage():
            e.acceptProposedAction()

    def dropEvent(self, e):  # noqa: N802
        md = e.mimeData()
        if md.hasImage():
            self.image_dropped.emit(qimage_to_pil(QImage(md.imageData())))
            return
        for url in md.urls():
            if url.isLocalFile():
                self.image_dropped.emit(url.toLocalFile())
                break


class EchoEditor(DropMixin, QFrame):
    changed = Signal()
    image_dropped = Signal(object)
    ocr_requested = Signal(object)

    def __init__(self, index: int):
        super().__init__()
        self.index = index
        self.setAcceptDrops(True)
        lay = QVBoxLayout(self)

        btns = QHBoxLayout()
        b_open = QPushButton("스크린샷 열기")
        b_paste = QPushButton("붙여넣기")
        b_clear = QPushButton("비우기")
        for b in (b_open, b_paste, b_clear):
            btns.addWidget(b)
        btns.addStretch()
        self.icon = portrait_label(ECHO_ICON_SIZE)
        btns.addWidget(self.icon)
        lay.addLayout(btns)
        b_open.clicked.connect(self._open)
        b_paste.clicked.connect(self.paste)
        b_clear.clicked.connect(self.clear)
        self.image_dropped.connect(self.ocr_requested.emit)

        grid = QGridLayout()
        grid.addWidget(QLabel("이름"), 0, 0)
        self.name = QLineEdit()
        self.name.setPlaceholderText("(선택 사항)")
        self.name.editingFinished.connect(self.changed.emit)
        grid.addWidget(self.name, 0, 1, 1, 2)
        grid.addWidget(QLabel("코스트"), 1, 0)
        self.cost = NoWheelCombo()
        for c in ("", "4", "3", "1"):
            self.cost.addItem(c or "(선택)", int(c) if c else 0)
        grid.addWidget(self.cost, 1, 1)
        grid.addWidget(QLabel("메인옵션"), 2, 0)
        self.main = StatCombo()
        self.main_val = ValueSpin()
        grid.addWidget(self.main, 2, 1)
        grid.addWidget(self.main_val, 2, 2)
        self.subs: list[tuple[StatCombo, ValueSpin]] = []
        for i in range(5):
            grid.addWidget(QLabel(f"서브옵션 {i + 1}"), 3 + i, 0)
            c, v = StatCombo(), ValueSpin()
            c.set_keys(SUBSTAT_KEYS)
            grid.addWidget(c, 3 + i, 1)
            grid.addWidget(v, 3 + i, 2)
            c.currentIndexChanged.connect(lambda _=0, c=c, v=v: (v.set_stat(c.key()), self.changed.emit()))
            v.valueChanged.connect(lambda *_: self.changed.emit())
            self.subs.append((c, v))
        grid.setColumnStretch(1, 1)
        lay.addLayout(grid)
        self.status = plain_label("에코 상세 화면 스크린샷을 열거나, 이 영역에 끌어다 놓거나, Ctrl+V로 붙여넣으세요.")
        self.status.setWordWrap(True)
        self.status.setStyleSheet(f"color: {MUTED_COLOR};")
        lay.addWidget(self.status)
        lay.addStretch()

        self.cost.currentIndexChanged.connect(self._cost_changed)
        self.main.currentIndexChanged.connect(lambda: (self.main_val.set_stat(self.main.key()), self.changed.emit()))
        self.main_val.valueChanged.connect(lambda *_: self.changed.emit())
        self._cost_changed()

    def set_icon(self, pix: "QPixmap | None", cost: int = 0) -> None:
        """에코 초상화. 공식 이미지가 없는 에코는 코스트만 표시한다."""
        if pix is not None and not pix.isNull():
            self.icon.setPixmap(pix.scaled(ECHO_ICON_SIZE, ECHO_ICON_SIZE, Qt.KeepAspectRatio, Qt.SmoothTransformation))
        else:
            self.icon.setPixmap(QPixmap())
            self.icon.setText(f"COST\n{cost}" if cost else "")

    def _cost_changed(self) -> None:
        cost = self.cost.currentData() or 0
        keys = list(S.MAIN_STAT_MAX.get(cost, {})) or sorted({k for d in S.MAIN_STAT_MAX.values() for k in d})
        self.main.set_keys(keys)
        self.changed.emit()

    def _open(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, f"에코 {self.index + 1} 스크린샷", "", IMAGE_FILTER)
        if path:
            self.ocr_requested.emit(path)

    def paste(self) -> None:
        img = QApplication.clipboard().image()
        if img.isNull():
            self.status.setText("클립보드에 이미지가 없습니다.")
            return
        self.ocr_requested.emit(qimage_to_pil(img))

    def clear(self) -> None:
        self.set_echo(Echo(cost=0))
        self.status.setText("")

    def set_echo(self, e: Echo) -> None:
        widgets = [self.name, self.cost, self.main, self.main_val] + [w for pair in self.subs for w in pair]
        for w in widgets:
            w.blockSignals(True)
        self.name.setText(e.name or "")
        self.cost.setCurrentIndex(max(self.cost.findData(e.cost or 0), 0))
        self._cost_changed_silent()
        self.main.set_key(e.main.stat if e.main else "")
        self.main_val.set_stat(self.main.key())
        self.main_val.setValue(e.main.value if e.main else 0)
        for i, (c, v) in enumerate(self.subs):
            line = e.substats[i] if i < len(e.substats) else None
            c.set_key(line.stat if line else "")
            v.set_stat(c.key())
            v.setValue(line.value if line else 0)
        for w in widgets:
            w.blockSignals(False)
        self.changed.emit()

    def _cost_changed_silent(self) -> None:
        cost = self.cost.currentData() or 0
        keys = list(S.MAIN_STAT_MAX.get(cost, {})) or sorted({k for d in S.MAIN_STAT_MAX.values() for k in d})
        self.main.set_keys(keys)

    def get_echo(self) -> Echo | None:
        main = StatLine(self.main.key(), self.main_val.value()) if self.main.key() else None
        subs = [StatLine(c.key(), v.value()) for c, v in self.subs if c.key() or v.value()]
        cost = self.cost.currentData() or 0
        if main is None and not subs:
            return None
        return Echo(cost=cost, main=main, substats=subs, name=self.name.text().strip())


class TotalsEditor(DropMixin, QGroupBox):
    changed = Signal()
    image_dropped = Signal(object)
    ocr_requested = Signal(object)

    def __init__(self):
        super().__init__("캐릭터 총 스탯")
        self.setAcceptDrops(True)
        lay = QVBoxLayout(self)
        btns = QHBoxLayout()
        b_open, b_paste, b_clear = QPushButton("스크린샷 열기"), QPushButton("붙여넣기"), QPushButton("비우기")
        for b in (b_open, b_paste, b_clear):
            btns.addWidget(b)
        btns.addStretch()
        lay.addLayout(btns)
        b_open.clicked.connect(self._open)
        b_paste.clicked.connect(self.paste)
        b_clear.clicked.connect(lambda: self.set_totals({}))
        self.image_dropped.connect(self.ocr_requested.emit)

        self.table = QTableWidget(len(S.TOTAL_STAT_KEYS), 3)
        self.table.setHorizontalHeaderLabels(["스탯", "현재 값", "목표"])
        self.table.verticalHeader().setVisible(False)
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        self.table.setSelectionMode(QAbstractItemView.NoSelection)
        self.table.verticalHeader().setDefaultSectionSize(36)
        self.table.setAlternatingRowColors(True)
        self.spins: dict[str, ValueSpin] = {}
        self._targets: dict = {}
        self.changed.connect(self._mark_targets)
        for r, key in enumerate(S.TOTAL_STAT_KEYS):
            item = QTableWidgetItem(S.label(key))
            item.setFlags(Qt.ItemIsEnabled)
            self.table.setItem(r, 0, item)
            sp = ValueSpin()
            sp.set_stat(key)
            sp.setSpecialValueText("-")  # 0 = 미입력
            sp.valueChanged.connect(lambda *_: self.changed.emit())
            self.table.setCellWidget(r, 1, sp)
            self.spins[key] = sp
            t = QTableWidgetItem("")
            t.setFlags(Qt.ItemIsEnabled)
            self.table.setItem(r, 2, t)
        self.table.setMinimumHeight(260)
        lay.addWidget(self.table)
        self.status = plain_label("캐릭터 속성 상세 화면 스크린샷을 열거나 끌어다 놓으세요. 0은 미입력으로 처리됩니다.")
        self.status.setWordWrap(True)
        self.status.setStyleSheet(f"color: {MUTED_COLOR};")
        lay.addWidget(self.status)

    def _open(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "캐릭터 스탯 스크린샷", "", IMAGE_FILTER)
        if path:
            self.ocr_requested.emit(path)

    def paste(self) -> None:
        img = QApplication.clipboard().image()
        if img.isNull():
            self.status.setText("클립보드에 이미지가 없습니다.")
            return
        self.ocr_requested.emit(qimage_to_pil(img))

    def _mark_targets(self) -> None:
        """목표 칸 색: 달성하면 초록, 모자라면 주황, 입력 전이면 강조색."""
        from PySide6.QtGui import QColor
        for r, key in enumerate(S.TOTAL_STAT_KEYS):
            t = self._targets.get(key)
            item = self.table.item(r, 2)
            if not t:
                continue
            cur = self.spins[key].value()
            ok = cur > 0 and (cur > t.value if t.op == ">" else cur >= t.value)
            item.setForeground(QColor("#2ecc71" if ok else "#f39c12" if cur > 0 else ACCENT))

    def set_targets(self, char: Character | None) -> None:
        targets = {t.stat: t for t in (char.targets if char else [])}
        self._targets = targets
        bold = QFont()
        bold.setBold(True)
        for r, key in enumerate(S.TOTAL_STAT_KEYS):
            t = targets.get(key)
            self.table.item(r, 2).setText(f"{'≥' if t and t.op == '>=' else '>' if t else ''} "
                                          f"{S.fmt(key, t.value)}" if t else "")
            self.table.item(r, 0).setFont(bold if t else QFont())
            self.table.item(r, 2).setFont(bold)
        self._mark_targets()

    def set_totals(self, totals: dict[str, float]) -> None:
        for key, sp in self.spins.items():
            sp.blockSignals(True)
            sp.setValue(totals.get(key, 0))
            sp.blockSignals(False)
        self.changed.emit()

    def totals(self) -> dict[str, float]:
        return {k: sp.value() for k, sp in self.spins.items() if sp.value() > 0}


class GearEditor(QGroupBox):
    """장착 무기 / 에코 세트(최대 2개 조합) / 공명 체인(돌파). 스크린샷에서 읽지 않으므로 직접 고른다."""

    changed = Signal()
    OTHER = "__other__"

    def __init__(self):
        super().__init__("무기 · 에코 세트 · 돌파")
        grid = QGridLayout(self)
        self.weapon = NoWheelCombo()
        self.sonata1 = NoWheelCombo()
        self.sonata2 = NoWheelCombo()
        self.chain = NoWheelCombo()
        self.chain.addItem("(선택 안 함)", None)
        for i in range(7):
            self.chain.addItem(f"{i}돌", i)
        rows = (("무기", self.weapon), ("에코 세트 1", self.sonata1),
                ("에코 세트 2", self.sonata2), ("돌파", self.chain))
        for r, (label, w) in enumerate(rows):
            grid.addWidget(QLabel(label), r, 0)
            grid.addWidget(w, r, 1)
            w.currentIndexChanged.connect(lambda *_: self.changed.emit())
        self.sonata2.setToolTip("3세트 + 2세트처럼 두 세트를 섞어 쓸 때만 고르세요.")
        grid.setColumnStretch(1, 1)

    def set_character(self, c: Character | None, all_sets: list[str] | None = None) -> None:
        weapons = [(f"{w.name}{' (전용)' if w.signature else ''}", w.name) for w in (c.weapons if c else [])]
        rec_sets = list(zip(c.sonata, c.sonata_names)) if c else []
        others = [n for n in (all_sets or []) if n not in {n for _, n in rec_sets}]
        sets = [(f"{text} (추천)", name) for text, name in rec_sets] + [(n, n) for n in others]
        for combo, items, other in ((self.weapon, weapons, "그 외 무기"),
                                    (self.sonata1, sets, "그 외 세트"), (self.sonata2, sets, "그 외 세트")):
            combo.blockSignals(True)
            combo.clear()
            combo.addItem("(선택 안 함)", "")
            for text, data in items:
                combo.addItem(text, data)
            combo.addItem(other, self.OTHER)
            combo.blockSignals(False)
        self.chain.blockSignals(True)
        self.chain.setCurrentIndex(0)
        self.chain.blockSignals(False)
        core = {ch.seq for ch in c.chains if ch.core} if c else set()
        for i in range(7):
            self.chain.setItemText(i + 1, f"{i}돌" + (" ★핵심" if i in core else ""))

    def _val(self, combo: QComboBox) -> str:
        d = combo.currentData() or ""
        return "(기타)" if d == self.OTHER else d

    def values(self) -> tuple[str, list[str], int | None]:
        sets = [v for v in (self._val(self.sonata1), self._val(self.sonata2)) if v]
        return self._val(self.weapon), sets, self.chain.currentData()

    def set_values(self, weapon: str, sonata, chain: int | None) -> None:
        if isinstance(sonata, str):  # 예전 저장 형식
            sonata = [sonata] if sonata else []
        sonata = list(sonata) + ["", ""]
        for combo, v in ((self.weapon, weapon), (self.sonata1, sonata[0]), (self.sonata2, sonata[1])):
            combo.blockSignals(True)
            i = combo.findData(self.OTHER if v == "(기타)" else v)
            combo.setCurrentIndex(max(i, 0))
            combo.blockSignals(False)
        self.chain.blockSignals(True)
        self.chain.setCurrentIndex(max(self.chain.findData(chain), 0) if chain is not None else 0)
        self.chain.blockSignals(False)


# ---------------------------------------------------------------------------
# 캐릭터 정보 / 결과
# ---------------------------------------------------------------------------
class CharacterInfo(QTextBrowser):
    def __init__(self):
        super().__init__()
        self.setOpenLinks(False)
        self.anchorClicked.connect(open_link)
        # 내용 높이에 맞춘다 (왼쪽 칸 전체가 스크롤되므로 이 칸 안에 스크롤바를 두지 않는다)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)

    def _fit_height(self) -> None:
        doc = self.document()
        doc.setTextWidth(self.viewport().width())
        m = self.contentsMargins()
        self.setFixedHeight(int(doc.size().height()) + m.top() + m.bottom() + 4)

    def resizeEvent(self, e):  # noqa: N802
        super().resizeEvent(e)
        self._fit_height()

    def setHtml(self, text: str) -> None:  # noqa: N802
        super().setHtml(text)
        self._fit_height()

    def show_character(self, c: Character | None) -> None:
        if c is None:
            self.setHtml("<p>캐릭터를 선택하세요.</p>")
            return
        e = html.escape
        ec = ELEMENT_COLORS.get(c.element, ACCENT)
        star_color = "#f5c542" if (c.star or 0) >= 5 else "#b98cf0"
        parts = [f"<p style='margin:0'><span style='font-size:{fs(8)}; font-weight:bold; color:{ec}'>{e(c.name)}</span></p>"
                 f"<p style='margin:2px 0 8px 0'><span style='font-size:{fs(1)}; color:{star_color}'>{'★' * (c.star or 0)}</span>"
                 f"&nbsp;&nbsp;<span style='font-size:{fs(0)}; font-weight:bold; color:#15171c; background-color:{ec}'>"
                 f"&nbsp;{e(S.ELEMENT_KO.get(c.element, ''))}&nbsp;</span></p>"]
        if not c.guide:
            parts.append(f"<p style='color:{WARN_COLOR}'>공식 공략이 아직 없어 기본 딜러 기준으로 계산합니다.</p>")
        if c.substat_priority:
            parts.append(f"<p style='margin:10px 0 2px 0; font-size:{fs(2)}; font-weight:bold; color:{ACCENT}'>서브옵션 우선순위</p>" "<table cellspacing='0' cellpadding='5'>")
            for i, tier in enumerate(c.substat_priority):
                color = PRIORITY_COLORS[min(i, len(PRIORITY_COLORS) - 1)]
                names = " / ".join(e(S.label(k)) for k in tier)
                parts.append(f"<tr><td style='font-size:{fs(3)}; font-weight:bold; color:{color}'>{i + 1}순위</td>"
                             f"<td style='font-size:{fs(3)}; font-weight:bold; color:{color}'>{names}</td></tr>")
            parts.append("</table>")
        if c.extra_substats:
            names = ", ".join(e(S.label(k)) for k in c.extra_substats)
            parts.append(f"<p style='font-size:{fs(1)}'><b>직접 추가한 유효 옵션</b><br>"
                         f"<span style='font-weight:bold; color:{PRIORITY_COLORS[min(len(c.substat_priority), len(PRIORITY_COLORS) - 1)]}'>"
                         f"{names}</span></p>")
        if not c.substat_priority and c.substat_priority_text:
            parts.append(f"<p style='margin:10px 0 2px 0; font-size:{fs(2)}; font-weight:bold; color:{ACCENT}'>서브옵션 우선순위</p>" f"<p style='font-size:{fs(3)}; font-weight:bold; color:{PRIORITY_COLORS[0]}'>"
                         f"{e(c.substat_priority_text)}</p>")
        if c.stale:
            parts.append(f"<p style='color:{WARN_COLOR}'>공식 사이트에서 이 캐릭터 공략을 지금은 받을 수 없어 예전에 받은 데이터를 씁니다.</p>")
        self.setHtml("".join(parts))


PARTY_ICON_SIZE = 52


class PartyPanel(QGroupBox):
    """공략 사이트처럼 추천 파티를 초상화로 보여준다. 예) 에이메스 - 린네 - 모니에"""

    def __init__(self, pixmap_for: Callable[[str], "QPixmap | None"]):
        super().__init__("추천 파티")
        self._pixmap_for = pixmap_for
        self._lay = QVBoxLayout(self)
        self._lay.setSpacing(6)

    def _clear(self) -> None:
        while self._lay.count():
            item = self._lay.takeAt(0)
            w = item.widget()
            if w is not None:
                w.hide()
                w.setParent(None)
                w.deleteLater()

    def _member(self, cid: str, name: str, mode: str = "") -> QWidget:
        box = QWidget()
        box.setFixedWidth(PARTY_ICON_SIZE + 14)  # 이름이 길어도 줄 폭이 늘지 않게 (긴 이름은 두 줄)
        v = QVBoxLayout(box)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(1)
        icon = portrait_label(PARTY_ICON_SIZE)
        pix = self._pixmap_for(cid)
        if pix is None:
            icon.setText(name[:3])
        else:
            icon.setPixmap(pix.scaled(PARTY_ICON_SIZE, PARTY_ICON_SIZE, Qt.KeepAspectRatio, Qt.SmoothTransformation))
        icon.setToolTip(name)
        v.addWidget(icon, 0, Qt.AlignHCenter)
        lb = QLabel(name)
        lb.setAlignment(Qt.AlignCenter)
        lb.setWordWrap(True)
        lb.setStyleSheet(f"font-size: {fs(-2)};")
        v.addWidget(lb)
        if mode:
            ml = QLabel(mode)
            ml.setAlignment(Qt.AlignCenter)
            # 이름보다 크게, 배경 칸을 둘러 한눈에 보이게 한다
            ml.setStyleSheet(f"font-size: {fs(1)}; font-weight: bold; color: white; background: {WARN_COLOR};"
                             " border-radius: 4px; padding: 1px 6px;")
            ml.setToolTip(f"{name}: {mode} 모드 선택")
            v.addWidget(ml, 0, Qt.AlignHCenter)
        return box

    def show_character(self, c: Character | None, names: dict[str, str],
                       rules: list[tuple[str, str, str]] | None = None) -> None:
        self._clear()
        parties = c.recommended_parties() if c else []
        self.setVisible(bool(parties))
        for party in parties:
            modes = party_modes(party, rules or [])
            row = QWidget()
            h = QHBoxLayout(row)
            h.setContentsMargins(0, 0, 0, 0)
            h.setSpacing(4)
            for i, cid in enumerate(party):
                if i:
                    dash = QLabel("-")
                    dash.setStyleSheet(f"color: {MUTED_COLOR}; font-weight: bold;")
                    dash.setFixedSize(dash.sizeHint().width(), PARTY_ICON_SIZE)  # 줄이 좁아도 사라지지 않게  # 초상화 높이 가운데에 맞춘다
                    h.addWidget(dash, 0, Qt.AlignTop)
                h.addWidget(self._member(cid, names.get(cid, cid), modes.get(cid, "")), 0, Qt.AlignTop)
            h.addStretch(1)
            row.setToolTip(" - ".join(names.get(cid, cid) + (f" ({modes[cid]})" if cid in modes else "")
                                      for cid in party))
            self._lay.addWidget(row)


class RainbowLabel(QWidget):
    """이스터 에그: 10번 클릭하면 글자가 무지개색으로 출렁이고, 그 상태에서 한 번 더 누르면 꺼진다."""

    CLICKS = 10

    def __init__(self, text: str):
        super().__init__()
        self.text = text
        self.clicks = 0
        self.phase = 0.0
        self.timer = QTimer(self, interval=33)  # 약 30fps
        self.timer.timeout.connect(self._tick)
        self.setCursor(Qt.PointingHandCursor)
        self._update_size()

    @property
    def active(self) -> bool:
        return self.timer.isActive()

    def _font(self) -> QFont:
        f = QFont(self.font())
        f.setPointSize(13 if self.active else 8)
        f.setBold(self.active)
        return f

    def _update_size(self) -> None:
        from PySide6.QtGui import QFontMetrics
        fm = QFontMetrics(self._font())
        wave = 10 if self.active else 0
        self.setFixedHeight(fm.height() + wave * 2 + 4)
        self.setMinimumWidth(fm.horizontalAdvance(self.text) + 8)
        self.update()

    def mousePressEvent(self, e):  # noqa: N802
        if e.button() != Qt.LeftButton:
            return
        if self.active:
            self.timer.stop()
            self.clicks = 0
        else:
            self.clicks += 1
            if self.clicks >= self.CLICKS:
                self.phase = 0.0
                self.timer.start()
        self._update_size()

    def _tick(self) -> None:
        self.phase += 0.12
        self.update()

    def paintEvent(self, _e):  # noqa: N802
        import math
        from PySide6.QtGui import QColor, QFontMetrics, QPainter
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        font = self._font()
        p.setFont(font)
        fm = QFontMetrics(font)
        if not self.active:
            p.setPen(QColor(MUTED_COLOR))
            p.drawText(2, fm.ascent() + 2, self.text)
            return
        x, base = 4, fm.ascent() + 12
        for i, ch in enumerate(self.text):
            hue = (i * 0.09 + self.phase * 0.15) % 1.0
            dy = math.sin(self.phase + i * 0.7) * 8  # 출렁출렁
            p.setPen(QColor.fromHslF(hue, 0.9, 0.62))
            p.drawText(int(x), int(base + dy), ch)
            x += fm.horizontalAdvance(ch)


class ResultsPanel(QWidget):
    def __init__(self):
        super().__init__()
        lay = QVBoxLayout(self)
        top = QHBoxLayout()
        self.grade = QLabel("-")
        def grade_font() -> None:
            f = QFont()
            f.setPointSize(round(base_pt() * 3.6))
            f.setBold(True)
            self.grade.setFont(f)
        grade_font()
        _font_listeners.append(grade_font)
        self.grade.setFixedSize(132, 132)
        self.grade.setAlignment(Qt.AlignCenter)
        self._grade_style("-")
        top.addWidget(self.grade)
        self.summary = QLabel("")
        self.summary.setWordWrap(True)
        top.addWidget(self.summary, 1)
        lay.addLayout(top)

        lay.addWidget(section_label("목표 스탯 달성도"))
        self.targets = QTableWidget(0, 4)
        self.targets.setHorizontalHeaderLabels(["스탯", "현재", "목표", "달성"])
        self._table_style(self.targets)
        self.targets.horizontalHeader().setSectionResizeMode(3, QHeaderView.Stretch)
        lay.addWidget(self.targets)

        lay.addWidget(section_label("에코 점수", "교체 우선순위 순"))
        self.echoes = QTableWidget(0, 5)
        self.echoes.setHorizontalHeaderLabels(["에코", "코스트", "메인옵션", "등급 (희귀도)", "메모"])
        self._table_style(self.echoes)
        self.echoes.horizontalHeader().setSectionResizeMode(4, QHeaderView.Stretch)
        self.echoes.setIconSize(QSize(36, 36))
        self.echoes.setWordWrap(True)
        lay.addWidget(self.echoes)

        # 장착 에코처럼 접고 펼 수 있는 제목
        advice_toggle = QToolButton()
        advice_toggle.setText("육성 가이드")
        advice_toggle.setCheckable(True)
        advice_toggle.setChecked(True)
        advice_toggle.setToolButtonStyle(Qt.ToolButtonTextBesideIcon)
        advice_toggle.setArrowType(Qt.DownArrow)
        advice_toggle.setAutoRaise(True)

        def toggle_style() -> None:
            advice_toggle.setStyleSheet(
                f"QToolButton {{ font-weight: bold; font-size: {base_pt() + 3}pt; color: {TEXT}; border: none;"
                f" border-left: 4px solid {ACCENT}; padding: 2px 0 2px 6px; margin-top: 10px; }}")
        toggle_style()
        _font_listeners.append(toggle_style)
        lay.addWidget(advice_toggle, 0, Qt.AlignLeft)
        self.advice = QTextBrowser()
        lay.addWidget(self.advice, 1)
        self._advice_spacer = QWidget()  # 접었을 때 남는 공간을 채운다
        lay.addWidget(self._advice_spacer, 0)

        def toggle_advice(on: bool) -> None:
            self.advice.setVisible(on)
            advice_toggle.setArrowType(Qt.DownArrow if on else Qt.RightArrow)
            lay.setStretchFactor(self._advice_spacer, 0 if on else 1)
        advice_toggle.toggled.connect(toggle_advice)

    def _grade_style(self, grade: str) -> None:
        color = GRADE_COLORS.get(grade, "#3a404b")
        self.grade.setStyleSheet(f"color: white; background: {color}; border-radius: 16px;"
                                 " border: 3px solid rgba(255,255,255,40);")

    def set_echo_icon(self, index: int, pix: "QPixmap | None") -> None:
        row = getattr(self, "_echo_rows", {}).get(index)
        item = self.echoes.item(row, 0) if row is not None else None
        if item is not None:
            item.setIcon(QIcon(pix) if pix is not None else QIcon())

    @staticmethod
    def _table_style(t: QTableWidget) -> None:
        t.verticalHeader().setVisible(False)
        t.setEditTriggers(QAbstractItemView.NoEditTriggers)
        t.setSelectionMode(QAbstractItemView.NoSelection)
        t.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeToContents)
        t.setAlternatingRowColors(True)
        t.verticalHeader().setDefaultSectionSize(32)

    def show_evaluation(self, ev: Evaluation | None) -> None:
        if ev is None:
            self.grade.setText("-")
            self._grade_style("-")
            self.summary.setText(f"<span style='font-size:{fs(3)}; color:{MUTED_COLOR}'>스탯이나 에코를 입력하면 등급을 계산합니다.</span>")
            self.targets.setRowCount(0)
            self.echoes.setRowCount(0)
            self.advice.clear()
            return
        color = GRADE_COLORS.get(ev.grade, "#333")
        self.grade.setText(ev.grade)
        self._grade_style(ev.grade)
        ts = (f"{ev.target_score:.0f}점" + (f" (서브옵션 약 {ev.missing_lines:.1f}줄 부족)" if ev.missing_lines else "")
              if ev.target_score is not None else "입력 없음")
        es = (f"{ev.echo_score:.0f}점 (평균 상위 {100 - ev.echo_score:.0f}%, 에코 {len(ev.echoes)}개)"
              if ev.echo_score is not None else "입력 없음")
        cap = ("<br><span style='color:#e67e22'>추천 스탯을 모두 채우지 못해 등급이 A로 제한됐습니다</span>"
               if ev.grade_capped else "")
        chain = ""
        if ev.character.chains and not ev.chain_estimates:
            chain = (f"<br><span style='color:{MUTED_COLOR}'>돌파 효율은 '캐릭터 데이터 업데이트'로 공명 체인 효과 문구를 "
                     "받은 뒤에 계산합니다.</span>")
        if ev.chain_estimates:
            per = " · ".join(f"{e.seq}돌 +{e.gain * 100:.0f}%{'*' if e.estimated_default else ''}"
                             for e in ev.chain_estimates)
            if ev.chain_gain is not None:
                cg = GRADE_COLORS.get(ev.chain_grade or "", "#333")
                chain = (f"<br><b>돌파 반영: <span style='color:{cg}'>{ev.chain_grade}</span> "
                         f"{ev.chain_score:.1f}점</b> — {ev.chain}돌은 0돌보다 DPS 약 +{ev.chain_gain * 100:.0f}% "
                         f"(서브옵션 약 {ev.chain_lines:.1f}줄 분량, 추정)")
            else:
                chain = f"<br><span style='color:{MUTED_COLOR}'>돌파 단계를 고르면 돌파 효율을 반영한 등급도 계산합니다.</span>"
            chain += f"<br><span style='color:{MUTED_COLOR}'>돌파별 DPS 상승(추정): {per}"
            chain += " (*효과 크기를 문구로 알 수 없어 기본값)</span>" if any(
                e.estimated_default for e in ev.chain_estimates) else "</span>"
        def row(label: str, value: str) -> str:
            return (f"<span style='font-size:{fs(1)}; color:{MUTED_COLOR}'>{label}</span>&nbsp; "
                    f"<span style='font-size:{fs(2)}; font-weight:bold'>{value}</span>")
        self.summary.setText(f"<span style='font-size:{fs(4)}; color:{MUTED_COLOR}'>종합</span>&nbsp;"
                             f"<span style='font-size:{fs(12)}; font-weight:bold; color:{color}'>{ev.total_score:.1f}</span>"
                             f"<span style='font-size:{fs(4)}; color:{MUTED_COLOR}'> 점</span><br>"
                             f"{row('목표 스탯 달성도', ts)}<br>{row('에코 평균 점수', es)}{cap}{chain}")

        self.targets.setRowCount(len(ev.targets))
        for r, t in enumerate(ev.targets):
            self.targets.setItem(r, 0, QTableWidgetItem(t.label))
            self.targets.setItem(r, 1, QTableWidgetItem("-" if t.current is None else S.fmt(t.stat, t.current)))
            self.targets.setItem(r, 2, QTableWidgetItem(S.fmt(t.stat, t.target)))
            bar = QProgressBar()
            bar.setRange(0, 100)
            bar.setValue(round(t.ratio * 100))
            bar.setFormat("달성" if t.achieved else f"{t.ratio * 100:.0f}%  (-{S.fmt(t.stat, t.deficit)})")
            bar.setStyleSheet(f"QProgressBar::chunk {{ background: {'#2ecc71' if t.achieved else '#e67e22'}; border-radius: 4px; }}")
            self.targets.setCellWidget(r, 3, bar)

        self.echoes.setRowCount(len(ev.replace_order))
        self._echo_rows: dict[int, int] = {}
        for r, e in enumerate(ev.replace_order):
            main = f"{S.label(e.echo.main.stat)} {S.fmt(e.echo.main.stat, e.echo.main.value)}" if e.echo.main and e.echo.main.stat else "-"
            if not e.main_ok:
                main += " ⚠"
            score_item = QTableWidgetItem(f"{e.grade} · 상위 {e.top_percent:.0f}%")
            score_item.setToolTip(
                (f"순수 수치 점수 {e.roll_score:.1f} / 100 (등급 기준)\n" if e.roll_score is not None else "")
                + "줄별 수치: " + ", ".join(f"{S.label(s.stat)} {sc.roll_tier(s.stat, s.value)}"
                                         for s in e.substats if sc.roll_tier(s.stat, s.value))
                + f"\n유효 옵션 가치 {e.raw_score:.1f} / 100 (종합 점수·교체 순위 기준)")
            score_item.setForeground(Qt.GlobalColor.white)
            score_item.setBackground(_qcolor(GRADE_COLORS.get(e.grade, "#555")))
            self.echoes.setItem(r, 0, QTableWidgetItem(f"{e.index + 1}번"))
            self._echo_rows[e.index] = r
            self.echoes.setItem(r, 1, QTableWidgetItem(str(e.echo.cost or "?")))
            self.echoes.setItem(r, 2, QTableWidgetItem(main))
            self.echoes.setItem(r, 3, score_item)
            self.echoes.setItem(r, 4, QTableWidgetItem(" / ".join(e.notes)))
        self.echoes.resizeRowsToContents()

        esc = html.escape
        items = "".join(f"<li style='margin-bottom:10px'><b style='font-size:{fs(2)}'>{esc(a.title)}</b>"
                        f"<br><span style='line-height:140%'>{esc(a.detail)}</span></li>" for a in ev.advice)
        self.advice.setHtml(f"<ol>{items}</ol>" if items else "<p>모든 목표를 달성했고 교체할 에코가 없습니다.</p>")


def section_label(title: str, sub: str = "") -> QLabel:
    lb = QLabel()
    lb.setStyleSheet(f"border-left: 4px solid {ACCENT}; padding: 2px 0 2px 8px; margin-top: 10px;")

    def render() -> None:
        ex = f"&nbsp; <span style='font-size:{fs(0)}; font-weight:normal; color:{MUTED_COLOR}'>{sub}</span>" if sub else ""
        lb.setText(f"<span style='font-size:{fs(3)}; font-weight:bold; color:{TEXT}'>{title}</span>{ex}")
    render()
    _font_listeners.append(render)
    return lb


def _qcolor(hex_: str):
    from PySide6.QtGui import QColor
    return QColor(hex_)


# ---------------------------------------------------------------------------
# 메인 창
# ---------------------------------------------------------------------------
class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle(f"명조 육성 등급 계산기 v{__version__}")
        self.resize(1500, 900)
        self.pool = QThreadPool.globalInstance()
        self.characters: list[Character] = []
        self.meta: dict = {}
        self.current: Character | None = None
        self.all_sets: list[str] = []
        self._loading = False
        self._workers: set[Worker] = set()  # 완료 신호가 전달될 때까지 작업 객체를 붙잡아 둔다
        self._pixmaps: dict[str, QPixmap | None] = {}  # 이미지 파일 경로 → 그림 (None = 읽기 실패)
        from .images import load_echo_index
        self.echo_index: dict[str, str] = load_echo_index()  # 에코 이름 → assets/echoes 파일 이름

        central = QWidget()
        root = QVBoxLayout(central)
        bar = QHBoxLayout()
        char_label = QLabel("캐릭터")
        char_label.setStyleSheet(f"font-weight: bold; color: {ACCENT};")
        bar.addWidget(char_label)
        self.char_combo = QComboBox()
        self.char_combo.setEditable(True)
        self.char_combo.setInsertPolicy(QComboBox.NoInsert)
        self.char_combo.setMinimumWidth(260)
        self.char_combo.setStyleSheet(f"QComboBox {{ font-weight: bold; border: 2px solid {ACCENT}; }}")
        self.char_combo.completer().setFilterMode(Qt.MatchContains)
        self.char_combo.completer().setCompletionMode(QCompleter.PopupCompletion)
        bar.addWidget(self.char_combo)
        self.data_label = QLabel("")
        self.data_label.setStyleSheet(f"color: {MUTED_COLOR};")
        bar.addWidget(self.data_label)
        bar.addStretch()
        bar.addWidget(QLabel("글자 크기"))
        b_small = QPushButton("가-")
        b_big = QPushButton("가+")
        self.font_label = QLabel()
        self.font_label.setMinimumWidth(36)
        self.font_label.setAlignment(Qt.AlignCenter)
        for b, d in ((b_small, -1), (b_big, 1)):
            b.setFixedWidth(48)
            b.setToolTip("글자를 작게" if d < 0 else "글자를 크게")
            b.clicked.connect(lambda _=False, d=d: self.change_font(d))
        bar.addWidget(b_small)
        bar.addWidget(self.font_label)
        bar.addWidget(b_big)
        bar.addSpacing(12)
        self.b_multi = QPushButton("에코 스크린샷 여러 장 열기")
        self.b_update = QPushButton("캐릭터 데이터 업데이트")
        self.b_app_update = QPushButton("프로그램 업데이트 확인")
        self.b_app_update.setToolTip("GitHub에서 새 버전을 확인합니다. 서명이 확인된 공식 배포 파일만 설치합니다.")
        bar.addWidget(self.b_multi)
        bar.addWidget(self.b_update)
        bar.addWidget(self.b_app_update)
        root.addLayout(bar)

        watch_bar = QHBoxLayout()
        self.b_watch = QPushButton("세팅 자동 측정 시작")
        self.b_watch.setCheckable(True)
        self.b_watch.setProperty("primary", True)
        self.b_watch.setToolTip(
            "명조 창 화면을 읽기만 해서 캐릭터 화면의 스탯과 에코 화면의 에코를 자동으로 채웁니다.\n"
            "게임에 클릭이나 키 입력을 보내지 않고, 캡처한 화면은 저장하지 않습니다.")
        watch_bar.addWidget(self.b_watch)
        self.watch_label = plain_label(
            "자동 측정을 시작한 뒤 게임에서 캐릭터 속성 화면을 열고, 에코 화면에서 에코 5개를 차례로 클릭하세요.")
        self.watch_label.setStyleSheet(f"color: {MUTED_COLOR};")
        watch_bar.addWidget(self.watch_label, 1)
        root.addLayout(watch_bar)
        self._watch_stop = None
        self._watch_cleared = False

        split = QSplitter(Qt.Horizontal)
        left = QWidget()
        left_lay = QVBoxLayout(left)
        left_lay.setContentsMargins(0, 0, 0, 0)
        self.portrait = portrait_label(PORTRAIT_SIZE)
        left_lay.addWidget(self.portrait, 0, Qt.AlignHCenter)
        self.info = CharacterInfo()
        left_lay.addWidget(self.info)
        self.party = PartyPanel(self.portrait_pixmap)
        self.party.setVisible(False)
        left_lay.addWidget(self.party)
        self.b_extra = QPushButton("유효 옵션 직접 추가")
        self.b_extra.setToolTip("공략 우선순위에 없지만 이 캐릭터에게 도움이 되는 서브옵션을 유효 옵션으로 추가합니다.\n"
                                "추가한 옵션은 공략 우선순위 바로 다음 순위로 계산합니다.")
        self.b_extra.clicked.connect(self.edit_extra_substats)
        left_lay.addWidget(self.b_extra)
        test_label = RainbowLabel("퇴사하고 싶다")  # 업데이트 테스트용 문구 (10번 클릭하면 이스터 에그)
        left_lay.addWidget(test_label)
        left_lay.addStretch(1)
        left_scroll = QScrollArea()
        left_scroll.setWidgetResizable(True)
        left_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        left_scroll.setWidget(left)
        split.addWidget(left_scroll)

        mid = QWidget()
        mid_lay = QVBoxLayout(mid)
        self.totals = TotalsEditor()
        mid_lay.addWidget(self.totals)
        self.gear = GearEditor()
        self.gear.changed.connect(self.schedule_eval)
        mid_lay.addWidget(self.gear)
        echo_box = QGroupBox()
        echo_box.setProperty("untitled", True)  # 제목 대신 접기 버튼을 쓰므로 제목 자리를 비우지 않는다
        eb = QVBoxLayout(echo_box)
        echo_toggle = QToolButton()
        echo_toggle.setText("장착 에코")
        echo_toggle.setCheckable(True)
        echo_toggle.setChecked(True)
        echo_toggle.setToolButtonStyle(Qt.ToolButtonTextBesideIcon)
        echo_toggle.setArrowType(Qt.DownArrow)
        echo_toggle.setAutoRaise(True)
        echo_toggle.setStyleSheet(f"QToolButton {{ font-weight: bold; color: {ACCENT}; border: none; }}")
        eb.addWidget(echo_toggle)
        self.echo_tabs = QTabWidget()
        self.echo_editors = [EchoEditor(i) for i in range(ECHO_COUNT)]
        for i, ed in enumerate(self.echo_editors):
            self.echo_tabs.addTab(ed, f"에코 {i + 1}")
            ed.changed.connect(self.schedule_eval)
            ed.ocr_requested.connect(lambda img, i=i: self.ocr_echo(i, img))
        eb.addWidget(self.echo_tabs)
        mid_lay.addWidget(echo_box, 1)
        mid_lay.addStretch(0)

        def toggle_echoes(on: bool) -> None:
            self.echo_tabs.setVisible(on)
            echo_toggle.setArrowType(Qt.DownArrow if on else Qt.RightArrow)
            mid_lay.setStretchFactor(echo_box, 1 if on else 0)
            mid_lay.setStretch(mid_lay.count() - 1, 0 if on else 1)
        echo_toggle.toggled.connect(toggle_echoes)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(mid)
        split.addWidget(scroll)

        self.results = ResultsPanel()
        split.addWidget(self.results)
        split.setSizes([300, 560, 700])
        root.addWidget(split, 1)
        self.setCentralWidget(central)
        self.statusBar().showMessage("준비")

        self.eval_timer = QTimer(self, singleShot=True, interval=250)
        self.eval_timer.timeout.connect(self.run_eval)
        self.char_combo.currentIndexChanged.connect(self.on_character)
        self.totals.changed.connect(self.schedule_eval)
        self.totals.ocr_requested.connect(self.ocr_totals)
        self.b_update.clicked.connect(self.update_data)
        self.font_label.setText(f"{base_pt()}pt")
        _font_listeners.append(self._rerender)
        self.b_multi.clicked.connect(self.open_many_echoes)
        self.b_watch.toggled.connect(self.toggle_watch)
        QShortcut(QKeySequence.Paste, self, activated=self.paste_current)

        self._release: updater.Release | None = None  # 설치할 수 있는 새 버전
        self.b_app_update.clicked.connect(self.on_app_update)
        updater.cleanup_leftovers(updater.portable_root())
        QTimer.singleShot(2000, lambda: self.check_app_update(silent=True))

        self.load_data()

    # ----- 프로그램 업데이트 -----
    def check_app_update(self, silent: bool) -> None:
        """새 버전 확인. silent=True(시작 시)면 새 버전이 있을 때만 버튼으로 알린다."""
        w = Worker(_check_release)

        def done(rel: updater.Release) -> None:
            if not updater.is_newer(rel.version):
                if not silent:
                    QMessageBox.information(self, "프로그램 업데이트", f"최신 버전(v{__version__})을 쓰고 있습니다.")
                return
            if not rel.signed:
                if not silent:
                    QMessageBox.information(self, "프로그램 업데이트",
                                            f"새 버전 v{rel.version}이 올라왔지만 아직 서명 파일이 준비되지 않았습니다.\n"
                                            "잠시 후 다시 확인해 주세요.")
                return
            self._release = rel
            self.b_app_update.setText(f"새 버전 v{rel.version} 설치")
            self.b_app_update.setStyleSheet(f"QPushButton {{ background: {WARN_COLOR}; color: white; font-weight: bold; }}")
            self.statusBar().showMessage(f"새 버전 v{rel.version}이 있습니다. 상단의 '새 버전 설치' 버튼으로 업데이트하세요.")
            if not silent:
                self.install_app_update()

        def err(m: str) -> None:
            if not silent:
                QMessageBox.warning(self, "프로그램 업데이트", f"새 버전을 확인하지 못했습니다.\n{m}")

        w.signals.done.connect(done)
        w.signals.error.connect(err)
        self.start_worker(w)

    def on_app_update(self) -> None:
        if self._release is not None:
            self.install_app_update()
        else:
            self.check_app_update(silent=False)

    def install_app_update(self) -> None:
        rel = self._release
        if rel is None:
            return
        root = updater.portable_root()
        if root is None:
            if QMessageBox.question(self, "프로그램 업데이트",
                                    f"새 버전 v{rel.version}이 있습니다.\n소스 코드로 실행 중이라 자동 설치는 할 수 없습니다. "
                                    "릴리즈 페이지를 열까요?") == QMessageBox.Yes:
                QDesktopServices.openUrl(QUrl(rel.page_url))
            return
        box = QMessageBox(self)
        box.setWindowTitle("프로그램 업데이트")
        box.setTextFormat(Qt.PlainText)
        box.setText(f"새 버전 v{rel.version}을 설치할까요? (지금 v{__version__})")
        box.setInformativeText("내려받은 파일의 서명을 확인한 뒤 설치하고 프로그램을 다시 시작합니다.\n"
                               "입력 기록과 설정은 그대로 남습니다.")
        if rel.notes.strip():
            box.setDetailedText(rel.notes.strip())
        box.setStandardButtons(QMessageBox.Yes | QMessageBox.No)
        if box.exec() != QMessageBox.Yes:
            return

        dlg = QProgressDialog("새 버전을 내려받는 중...", None, 0, 100, self)
        dlg.setWindowTitle("프로그램 업데이트")
        dlg.setWindowModality(Qt.WindowModal)
        dlg.setMinimumDuration(0)
        dlg.show()
        w = Worker(updater.prepare_update, rel, root)
        labels = {"sig": "서명 파일을 받는 중...", "verify": "서명을 확인하는 중...", "extract": "압축을 푸는 중..."}

        def prog(p) -> None:
            stage, done_, total = p
            if stage == "download":
                if total:
                    dlg.setMaximum(100)
                    dlg.setValue(min(int(done_ * 100 / total), 99))
                dlg.setLabelText(f"새 버전을 내려받는 중... {done_ / 1048576:.1f}"
                                 + (f" / {total / 1048576:.1f} MB" if total else " MB"))
            else:
                dlg.setMaximum(0)
                dlg.setLabelText(labels.get(stage, ""))

        def done(plan: updater.Plan) -> None:
            dlg.close()
            # 안내 창을 먼저 띄운다: 적용기는 시작하자마자 이 프로그램이 끝나기를 기다리기 때문
            QMessageBox.information(self, "프로그램 업데이트",
                                    f"v{plan.version} 설치를 위해 프로그램을 닫습니다. 잠시 후 새 버전이 자동으로 실행됩니다.")
            try:
                updater.launch_apply(plan)
            except OSError as e:
                QMessageBox.warning(self, "업데이트 실패", f"업데이트를 시작하지 못했습니다: {e}")
                return
            self.close()
            QApplication.quit()

        def err(m: str) -> None:
            dlg.close()
            QMessageBox.warning(self, "업데이트 실패",
                                f"{m}\n\n직접 받으려면 릴리즈 페이지에서 새 ZIP을 받으세요:\n{rel.page_url}")

        w.signals.progress.connect(prog)
        w.signals.done.connect(done)
        w.signals.error.connect(err)
        self.start_worker(w)

    # ----- 데이터 -----
    def load_data(self) -> None:
        if not data_path().exists():
            self.info.setHtml("<p>캐릭터 데이터가 없습니다. <b>캐릭터 데이터 업데이트</b>를 눌러 공식 공략 사이트에서 받아오세요.</p>")
            QTimer.singleShot(300, self._ask_initial_update)
            return
        self.characters, self.meta = load_characters(data_path())
        extras = load_extra_substats()
        for c in self.characters:
            c.extra_substats = extras.get(c.id, [])
        # 모든 캐릭터 공략에 나온 세트 이름 (그 외 세트를 고를 수 있게)
        self.all_sets = sorted({n for c in self.characters for n in c.sonata_names})
        prev = self.current.id if self.current else None
        self.char_combo.blockSignals(True)
        self.char_combo.clear()
        for c in self.characters:
            self.char_combo.addItem(c.display_name, c.id)
        self.char_combo.blockSignals(False)
        self.data_label.setText(f"{len(self.characters)}명 · 데이터 기준 {self.meta.get('fetched_at', '?')[:10]}")
        idx = self.char_combo.findData(prev) if prev else 0
        self.char_combo.setCurrentIndex(max(idx, 0))
        self.on_character()

    def _ask_initial_update(self) -> None:
        if QMessageBox.question(self, "데이터 없음", "공식 공략 사이트에서 캐릭터 데이터를 지금 받아올까요?") == QMessageBox.Yes:
            self.update_data()

    def edit_extra_substats(self) -> None:
        c = getattr(self, "current", None)
        if c is None:
            QMessageBox.information(self, "유효 옵션 추가", "먼저 캐릭터를 선택하세요.")
            return
        in_guide = {k for tier in c.substat_priority for k in tier}
        dlg = QDialog(self)
        dlg.setWindowTitle(f"{c.name} 유효 옵션 추가")
        lay = QVBoxLayout(dlg)
        lay.addWidget(QLabel("공략에 없지만 유효한 서브옵션을 체크하세요.\n공략 우선순위 바로 다음 순위로 계산합니다."))
        boxes: dict[str, QCheckBox] = {}
        for key in SUBSTAT_KEYS:
            if key in S.FLAT_TO_PCT:
                continue  # 고정 수치는 같은 계열 %옵션을 따라간다
            cb = QCheckBox(S.label(key))
            if key in in_guide:
                cb.setChecked(True)
                cb.setEnabled(False)
                cb.setText(f"{S.label(key)} (공략에 있음)")
            else:
                cb.setChecked(key in c.extra_substats)
            boxes[key] = cb
            lay.addWidget(cb)
        btns = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        btns.accepted.connect(dlg.accept)
        btns.rejected.connect(dlg.reject)
        lay.addWidget(btns)
        if dlg.exec() != QDialog.Accepted:
            return
        c.extra_substats = [k for k, cb in boxes.items() if cb.isEnabled() and cb.isChecked()]
        data = load_extra_substats()
        data[c.id] = c.extra_substats
        try:
            save_extra_substats(data)
        except OSError as e:
            QMessageBox.warning(self, "저장 실패", f"유효 옵션을 저장하지 못했습니다: {e}")
        self.info.show_character(c)
        self.schedule_eval()

    def change_font(self, delta: int) -> None:
        pt = min(max(base_pt() + delta, FONT_PT_RANGE[0]), FONT_PT_RANGE[1])
        if pt != base_pt():
            apply_font_pt(pt)

    def _rerender(self) -> None:
        self.font_label.setText(f"{base_pt()}pt")
        self.info.show_character(getattr(self, "current", None))
        self.show_party()
        self.run_eval()

    def update_data(self) -> None:
        dlg = QProgressDialog("공식 공략 사이트에서 캐릭터 정보를 받는 중...", None, 0, 0, self)
        dlg.setWindowTitle("데이터 업데이트")
        dlg.setWindowModality(Qt.WindowModal)
        dlg.setMinimumDuration(0)
        dlg.show()
        w = Worker(_scrape)

        def prog(p):
            i, n, name = p
            dlg.setMaximum(n)
            dlg.setValue(i)
            dlg.setLabelText(f"[{i}/{n}] {name}")

        def done(data):
            dlg.close()
            self.load_data()
            missing = [c["name"] for c in data["characters"] if not c["guide"]]
            msg = f"{data['count']}명의 데이터를 저장했습니다."
            if missing:
                msg += "\n공식 공략이 없는 캐릭터: " + ", ".join(missing)
            if data["errors"]:
                msg += "\n오류: " + "; ".join(data["errors"][:5])
            QMessageBox.information(self, "업데이트 완료", msg)

        def err(m):
            dlg.close()
            QMessageBox.warning(self, "업데이트 실패", m)

        w.signals.progress.connect(prog)
        w.signals.done.connect(done)
        w.signals.error.connect(err)
        self.start_worker(w)

    def start_worker(self, w: Worker) -> None:
        w.setAutoDelete(False)
        self._workers.add(w)
        w.signals.done.connect(lambda *_: self._workers.discard(w))
        w.signals.error.connect(lambda *_: self._workers.discard(w))
        self.pool.start(w)

    # ----- 캐릭터 / 저장 -----
    def on_character(self) -> None:
        cid = self.char_combo.currentData()
        self.current = next((c for c in self.characters if c.id == cid), None)
        self.info.show_character(self.current)
        self.show_portrait()
        self.show_party()
        self.totals.set_targets(self.current)
        self.gear.set_character(self.current, self.all_sets)
        self.load_build()
        self.schedule_eval()

    def build_path(self) -> Path | None:
        return build_file(self.current.id) if self.current else None

    def load_build(self) -> None:
        self._loading = True
        try:
            p = self.build_path()
            try:
                data = json.loads(p.read_text(encoding="utf-8")) if p and p.exists() else {}
                if not isinstance(data, dict):
                    data = {}
            except (OSError, ValueError):
                data = {}  # 손상된 저장 파일은 무시하고 빈 입력으로 시작
            self.totals.set_totals(data.get("totals", {}))
            self.gear.set_values(data.get("weapon", ""), data.get("sonata", []), data.get("chain"))
            echoes = data.get("echoes", [])
            for i, ed in enumerate(self.echo_editors):
                d = echoes[i] if i < len(echoes) and echoes[i] else None
                ed.set_echo(_echo_from_dict(d) if d else Echo(cost=0))
                ed.status.setText("저장된 입력을 불러왔습니다." if d else "")
        finally:
            self._loading = False

    def save_build(self, build: Build) -> None:
        p = self.build_path()
        if not p:
            return
        echoes = [asdict(ed.get_echo()) if ed.get_echo() else None for ed in self.echo_editors]
        data = {"totals": build.totals, "echoes": echoes,
                "weapon": build.weapon, "sonata": build.sonata, "chain": build.chain}
        p.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")

    # ----- 계산 -----
    def schedule_eval(self) -> None:
        if not self._loading:
            self.eval_timer.start()

    def current_build(self) -> Build:
        weapon, sonata, chain = self.gear.values()
        return Build(self.totals.totals(), [e for ed in self.echo_editors if (e := ed.get_echo())],
                     weapon=weapon, sonata=sonata, chain=chain)

    # ----- 초상화 (저장소 assets 폴더의 이미지만 읽는다, 네트워크 없음) -----
    def pixmap(self, path: Path | None) -> QPixmap | None:
        if path is None:
            return None
        key = str(path)
        if key not in self._pixmaps:
            pix = QPixmap(key)
            self._pixmaps[key] = None if pix.isNull() else pix
        return self._pixmaps[key]

    def show_portrait(self) -> None:
        from .images import portrait_path

        c = self.current
        pix = self.pixmap(portrait_path(c.id)) if c else None
        if pix is None:
            self.portrait.setPixmap(QPixmap())
            self.portrait.setText(c.name if c else "")
        else:
            self.portrait.setPixmap(pix.scaled(PORTRAIT_SIZE, PORTRAIT_SIZE, Qt.KeepAspectRatio,
                                               Qt.SmoothTransformation))

    def portrait_pixmap(self, char_id: str) -> QPixmap | None:
        from .images import portrait_path
        return self.pixmap(portrait_path(char_id))

    def show_party(self) -> None:
        names = {str(k): v.get("name", "") for k, v in (getattr(self, "meta", None) or {}).get("team_members", {}).items()
                 if isinstance(v, dict) and v.get("name")}
        names.update({c.id: c.name for c in getattr(self, "characters", [])})
        self.party.show_character(getattr(self, "current", None), names, load_party_modes(names))

    def refresh_echo_icons(self) -> None:
        """에코 이름에 맞는 초상화를 에코 탭과 결과 표에 표시한다. 이미지가 없는 에코는 코스트만."""
        from .images import echo_image_path

        for i, ed in enumerate(self.echo_editors):
            e = ed.get_echo()
            pix = self.pixmap(echo_image_path(e.name, self.echo_index)) if e else None
            ed.set_icon(pix, e.cost if e else 0)
            self.results.set_echo_icon(i, pix)

    def run_eval(self) -> None:
        if not self.current:
            self.results.show_evaluation(None)
            return
        build = self.current_build()
        if not build.totals and not build.echoes:
            self.results.show_evaluation(None)
            return
        self.results.show_evaluation(evaluate(self.current, build))
        self.refresh_echo_icons()
        self.save_build(build)

    # ----- OCR -----
    def _start_ocr(self, fn, image, on_done, status_label: QLabel) -> None:
        status_label.setText("OCR 분석 중...")
        status_label.setStyleSheet("color: #2980b9;")
        w = Worker(fn, image)
        w.signals.progress.connect(lambda m: status_label.setText(f"{m} 분석 중..."))
        w.signals.done.connect(on_done)
        w.signals.error.connect(lambda m: (status_label.setText(f"OCR 실패: {m}"),
                                           status_label.setStyleSheet("color: #c0392b;")))
        self.start_worker(w)

    def ocr_totals(self, image) -> None:
        def done(totals: dict):
            self.totals.set_totals(totals)
            missing = [t.label or S.label(t.stat) for t in (self.current.targets if self.current else [])
                       if t.stat not in totals]
            msg = f"{len(totals)}개 스탯을 읽었습니다. 값이 맞는지 확인하고 틀리면 직접 고치세요."
            if missing:
                msg += f" 읽지 못한 목표 스탯: {', '.join(missing)}"
            self.totals.status.setText(msg)
            self.totals.status.setStyleSheet("color: #e67e22;" if missing else "color: #27ae60;")
        self._start_ocr(_ocr_totals, image, done, self.totals.status)

    def ocr_echo(self, index: int, image, then: Callable | None = None) -> None:
        ed = self.echo_editors[index]
        self.echo_tabs.setCurrentIndex(index)

        def done(echo: Echo):
            ed.set_echo(echo)
            unknown = sum(1 for s in echo.substats if not s.stat) + (0 if echo.main and echo.main.stat else 1)
            msg = f"{echo.name + ' · ' if echo.name else ''}코스트 {echo.cost or '?'}, 서브옵션 {len(echo.substats)}개를 읽었습니다."
            if echo.level is not None:
                msg += f" (+{echo.level})"
            if unknown or not echo.cost:
                msg += " 비어 있는 항목은 직접 골라 주세요."
            ed.status.setText(msg)
            ed.status.setStyleSheet("color: #e67e22;" if unknown or not echo.cost else "color: #27ae60;")
            if then:
                then()
        self._start_ocr(_ocr_echo, image, done, ed.status)

    # ----- 세팅 자동 측정 (게임 화면 읽기) -----
    def toggle_watch(self, on: bool) -> None:
        import threading

        if not on:
            if self._watch_stop:
                self._watch_stop.set()
            self.b_watch.setText("세팅 자동 측정 시작")
            return
        if not self.characters:
            self.b_watch.setChecked(False)
            return
        self._watch_stop = threading.Event()
        self._watch_cleared = False
        self.b_watch.setText("자동 측정 중지")
        self.watch_label.setStyleSheet("color: #2980b9;")
        self.watch_label.setText("게임 창을 찾는 중...")
        w = Worker(_watch, self.characters, self._watch_stop.is_set)
        w.signals.progress.connect(self._on_watch_event)
        w.signals.done.connect(lambda state: self._watch_finished(None))
        w.signals.error.connect(self._watch_finished)
        self.start_worker(w)

    def _on_watch_event(self, ev: tuple) -> None:
        kind = ev[0]
        if kind == "status":
            self.watch_label.setText(ev[1])
        elif kind == "character":
            _, cid, seen, totals = ev
            if cid and (not self.current or self.current.id != cid):
                self.char_combo.setCurrentIndex(max(self.char_combo.findData(cid), 0))
            elif not cid:
                self.watch_label.setText(f"캐릭터 이름을 알아보지 못했습니다 ({seen or '-'}). 캐릭터를 직접 고르세요.")
            self.totals.set_totals(totals)
            self.totals.status.setText(f"게임 화면에서 {len(totals)}개 스탯을 읽었습니다. 값이 맞는지 확인하세요.")
            self.totals.status.setStyleSheet("color: #27ae60;")
            self._watch_cleared = False  # 새 캐릭터 화면을 읽었으면 다음 에코부터 새로 채운다
        elif kind == "echo":
            _, idx, echo = ev
            if not self._watch_cleared:
                for ed in self.echo_editors:
                    ed.set_echo(Echo(cost=0))
                    ed.status.setText("")
                self._watch_cleared = True
            ed = self.echo_editors[idx]
            ed.set_echo(echo)
            ed.status.setText(f"게임 화면에서 읽었습니다: {echo.name or '이름 없음'} · 코스트 {echo.cost or '?'}")
            ed.status.setStyleSheet("color: #27ae60;")
            self.echo_tabs.setCurrentIndex(idx)

    def _watch_finished(self, error: str | None) -> None:
        if error:
            self.watch_label.setText(f"자동 측정 종료: {error.split(': ', 1)[-1]}")
            self.watch_label.setStyleSheet("color: #c0392b;")
        elif self._watch_stop and self._watch_stop.is_set():
            self.watch_label.setText("자동 측정을 멈췄습니다.")
            self.watch_label.setStyleSheet(f"color: {MUTED_COLOR};")
        else:
            self.watch_label.setStyleSheet("color: #27ae60;")
        self.b_watch.blockSignals(True)
        self.b_watch.setChecked(False)
        self.b_watch.setText("세팅 자동 측정 시작")
        self.b_watch.blockSignals(False)

    def closeEvent(self, e):  # noqa: N802
        if self._watch_stop:
            self._watch_stop.set()
        super().closeEvent(e)

    def open_many_echoes(self) -> None:
        paths, _ = QFileDialog.getOpenFileNames(self, "에코 스크린샷 (최대 5장)", "", IMAGE_FILTER)
        paths = paths[:ECHO_COUNT]
        # 한 장씩 차례로 처리 (Windows OCR을 동시에 여러 번 부르지 않도록)
        def step(i: int):
            if i < len(paths):
                self.ocr_echo(i, paths[i], then=lambda: step(i + 1))
        step(0)

    def paste_current(self) -> None:
        focus = QApplication.focusWidget()
        if focus and self.totals.isAncestorOf(focus):
            self.totals.paste()
        else:
            self.echo_editors[self.echo_tabs.currentIndex()].paste()


def _echo_from_dict(d: dict) -> Echo:
    main = StatLine(**d["main"]) if d.get("main") else None
    return Echo(cost=d.get("cost", 0), main=main, substats=[StatLine(**s) for s in d.get("substats", [])],
                name=d.get("name", ""), level=d.get("level"))


APP_ICON = Path(__file__).resolve().parent.parent / "assets" / "icon.ico"  # 갈브레나 도트 아이콘 (tools/make_icon.py)
APP_USER_MODEL_ID = "wuwastat.build-grader"


def main() -> int:
    if sys.platform == "win32":
        # pythonw로 실행해도 작업 표시줄에 파이썬 대신 이 프로그램 아이콘이 보이게 한다
        try:
            import ctypes
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(APP_USER_MODEL_ID)
        except (AttributeError, OSError):
            pass
    app = QApplication(sys.argv)
    app.setApplicationName("wuwastat")
    app.setOrganizationName("wuwastat")
    font = QFont("Malgun Gothic") if sys.platform == "win32" else app.font()
    font.setPointSize(saved_font_pt())
    app.setFont(font)
    apply_theme(app)
    if APP_ICON.is_file():
        app.setWindowIcon(QIcon(str(APP_ICON)))
    win = MainWindow()
    try:  # 개발용 새로고침: 저장소에서 실행할 때만 켜지고, 릴리즈에는 파일이 없다
        from . import devreload
        win._dev_reloader = devreload.install(win)
    except ImportError:
        pass
    win.show()
    return app.exec()
