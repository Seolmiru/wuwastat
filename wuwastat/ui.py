"""PySide6 데스크톱 UI."""

from __future__ import annotations

import html
import io
import json
import sys
import traceback
from dataclasses import asdict
from pathlib import Path
from typing import Callable

from PIL import Image
from PySide6.QtCore import QBuffer, QIODevice, QObject, QRunnable, QStandardPaths, Qt, QThreadPool, QTimer, QUrl, Signal
from PySide6.QtGui import QDesktopServices, QFont, QImage, QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QAbstractItemView, QApplication, QComboBox, QCompleter, QDoubleSpinBox, QFileDialog, QFrame, QGridLayout,
    QGroupBox, QHBoxLayout, QHeaderView, QLabel, QMainWindow, QMessageBox, QProgressBar, QProgressDialog,
    QPushButton, QScrollArea, QSplitter, QTableWidget, QTableWidgetItem, QTabWidget, QTextBrowser, QVBoxLayout,
    QWidget,
)

from . import __version__
from . import stats as S
from .models import DEFAULT_DATA, Build, Character, Echo, StatLine, load_characters
from .scoring import Evaluation, evaluate

IMAGE_FILTER = "이미지 (*.png *.jpg *.jpeg *.bmp *.webp)"
GRADE_COLORS = {"SSS": "#d4a017", "SS": "#e67e22", "S": "#c0392b", "A": "#8e44ad",
                "B": "#2980b9", "C": "#27ae60", "D": "#7f8c8d"}
SUBSTAT_KEYS = [S.CRIT_RATE, S.CRIT_DMG, S.ATK_PCT, S.ATK_FLAT, S.HP_PCT, S.HP_FLAT, S.DEF_PCT, S.DEF_FLAT,
                S.ENERGY_REGEN, S.BASIC_DMG, S.HEAVY_DMG, S.SKILL_DMG, S.LIBERATION_DMG]
ECHO_COUNT = 5


def builds_dir() -> Path:
    base = Path(QStandardPaths.writableLocation(QStandardPaths.AppDataLocation) or Path.home() / ".wuwastat")
    p = base / "builds"
    p.mkdir(parents=True, exist_ok=True)
    return p


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


def _ocr_totals(image, progress):
    from .parser import parse_totals
    return parse_totals(image, progress=progress)


def _ocr_echo(image, progress):
    from .parser import parse_echo
    return parse_echo(image, progress=progress)


def _scrape(progress):
    from .scraper import save, scrape_all
    data = scrape_all(lambda i, n, name: progress((i, n, name)))
    save(data, DEFAULT_DATA)
    return data


# ---------------------------------------------------------------------------
# 입력 위젯
# ---------------------------------------------------------------------------
class StatCombo(QComboBox):
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
        self.setButtonSymbols(QDoubleSpinBox.NoButtons)
        self.setMinimumWidth(80)

    def set_stat(self, key: str) -> None:
        self.setSuffix(" %" if key in S.PERCENT_STATS else "")


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
        lay.addLayout(btns)
        b_open.clicked.connect(self._open)
        b_paste.clicked.connect(self.paste)
        b_clear.clicked.connect(self.clear)
        self.image_dropped.connect(self.ocr_requested.emit)

        grid = QGridLayout()
        grid.addWidget(QLabel("코스트"), 0, 0)
        self.cost = QComboBox()
        for c in ("", "4", "3", "1"):
            self.cost.addItem(c or "(선택)", int(c) if c else 0)
        grid.addWidget(self.cost, 0, 1)
        grid.addWidget(QLabel("메인옵션"), 1, 0)
        self.main = StatCombo()
        self.main_val = ValueSpin()
        grid.addWidget(self.main, 1, 1)
        grid.addWidget(self.main_val, 1, 2)
        self.subs: list[tuple[StatCombo, ValueSpin]] = []
        for i in range(5):
            grid.addWidget(QLabel(f"서브옵션 {i + 1}"), 2 + i, 0)
            c, v = StatCombo(), ValueSpin()
            c.set_keys(SUBSTAT_KEYS)
            grid.addWidget(c, 2 + i, 1)
            grid.addWidget(v, 2 + i, 2)
            c.currentIndexChanged.connect(lambda _=0, c=c, v=v: (v.set_stat(c.key()), self.changed.emit()))
            v.valueChanged.connect(lambda *_: self.changed.emit())
            self.subs.append((c, v))
        grid.setColumnStretch(1, 1)
        lay.addLayout(grid)
        self.status = QLabel("에코 상세 화면 스크린샷을 열거나, 이 영역에 끌어다 놓거나, Ctrl+V로 붙여넣으세요.")
        self.status.setWordWrap(True)
        self.status.setStyleSheet("color: gray;")
        lay.addWidget(self.status)
        lay.addStretch()

        self.cost.currentIndexChanged.connect(self._cost_changed)
        self.main.currentIndexChanged.connect(lambda: (self.main_val.set_stat(self.main.key()), self.changed.emit()))
        self.main_val.valueChanged.connect(lambda *_: self.changed.emit())
        self._cost_changed()

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
        widgets = [self.cost, self.main, self.main_val] + [w for pair in self.subs for w in pair]
        for w in widgets:
            w.blockSignals(True)
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
        return Echo(cost=cost, main=main, substats=subs)


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
        self.spins: dict[str, ValueSpin] = {}
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
        self.status = QLabel("캐릭터 속성 상세 화면 스크린샷을 열거나 끌어다 놓으세요. 0은 미입력으로 처리됩니다.")
        self.status.setWordWrap(True)
        self.status.setStyleSheet("color: gray;")
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

    def set_targets(self, char: Character | None) -> None:
        targets = {t.stat: t for t in (char.targets if char else [])}
        bold = QFont()
        bold.setBold(True)
        for r, key in enumerate(S.TOTAL_STAT_KEYS):
            t = targets.get(key)
            self.table.item(r, 2).setText(f"{'≥' if t and t.op == '>=' else '>' if t else ''} "
                                          f"{S.fmt(key, t.value)}" if t else "")
            self.table.item(r, 0).setFont(bold if t else QFont())

    def set_totals(self, totals: dict[str, float]) -> None:
        for key, sp in self.spins.items():
            sp.blockSignals(True)
            sp.setValue(totals.get(key, 0))
            sp.blockSignals(False)
        self.changed.emit()

    def totals(self) -> dict[str, float]:
        return {k: sp.value() for k, sp in self.spins.items() if sp.value() > 0}


# ---------------------------------------------------------------------------
# 캐릭터 정보 / 결과
# ---------------------------------------------------------------------------
class CharacterInfo(QTextBrowser):
    def __init__(self):
        super().__init__()
        self.setOpenLinks(False)
        self.anchorClicked.connect(QDesktopServices.openUrl)

    def show_character(self, c: Character | None) -> None:
        if c is None:
            self.setHtml("<p>캐릭터를 선택하세요.</p>")
            return
        e = html.escape
        parts = [f"<h2>{e(c.name)}</h2>",
                 f"<p>{'★' * (c.star or 0)} · {e(S.ELEMENT_KO.get(c.element, ''))}</p>"]
        if not c.guide:
            parts.append("<p style='color:#c0392b'>공식 공략 사이트에 아직 공략이 없습니다. "
                         "기본 딜러 기준(크리티컬 > 공격력% > 공명 효율)으로 계산합니다.</p>")
        else:
            g = c.guide
            parts.append(f"<p><b>공략</b>: <a href='{e(c.source_url)}'>{e(g.get('title') or '')}</a><br>"
                         f"<span style='color:gray'>작성: {e(g.get('author') or '-')} · 추천 {g.get('likes', 0)}</span></p>")
        if c.targets:
            parts.append("<h3>추천 스탯 (Lv.90)</h3><ul>")
            for t in c.targets:
                parts.append(f"<li>{e(t.label or S.label(t.stat))} {'≥' if t.op == '>=' else t.op} "
                             f"<b>{S.fmt(t.stat, t.value)}</b></li>")
            parts.append("</ul>")
        if c.echo_slots:
            parts.append("<h3>추천 에코 메인옵션</h3><ul>")
            for s in sorted(c.echo_slots, key=lambda s: -s.cost):
                parts.append(f"<li>{s.cost}코스트: {e(S.label(s.main))}</li>")
            parts.append("</ul>")
        if c.main_echo or c.sonata:
            parts.append(f"<h3>에코 세트</h3><p>메인 에코: {e(c.main_echo or '-')}<br>"
                         f"세트: {e(', '.join(c.sonata) or '-')}</p>")
        if c.substat_priority_text:
            parts.append(f"<h3>서브옵션 우선순위</h3><p>{e(c.substat_priority_text)}</p>")
        self.setHtml("".join(parts))


class ResultsPanel(QWidget):
    def __init__(self):
        super().__init__()
        lay = QVBoxLayout(self)
        top = QHBoxLayout()
        self.grade = QLabel("-")
        f = QFont()
        f.setPointSize(40)
        f.setBold(True)
        self.grade.setFont(f)
        self.grade.setMinimumWidth(140)
        self.grade.setAlignment(Qt.AlignCenter)
        top.addWidget(self.grade)
        self.summary = QLabel("")
        self.summary.setWordWrap(True)
        top.addWidget(self.summary, 1)
        lay.addLayout(top)

        lay.addWidget(QLabel("<b>목표 스탯 달성도</b>"))
        self.targets = QTableWidget(0, 4)
        self.targets.setHorizontalHeaderLabels(["스탯", "현재", "목표", "달성"])
        self._table_style(self.targets)
        self.targets.horizontalHeader().setSectionResizeMode(3, QHeaderView.Stretch)
        lay.addWidget(self.targets)

        lay.addWidget(QLabel("<b>에코 점수</b> (교체 우선순위 순)"))
        self.echoes = QTableWidget(0, 5)
        self.echoes.setHorizontalHeaderLabels(["에코", "코스트", "메인옵션", "점수", "메모"])
        self._table_style(self.echoes)
        self.echoes.horizontalHeader().setSectionResizeMode(4, QHeaderView.Stretch)
        self.echoes.setWordWrap(True)
        lay.addWidget(self.echoes)

        lay.addWidget(QLabel("<b>육성 가이드</b>"))
        self.advice = QTextBrowser()
        lay.addWidget(self.advice, 1)

    @staticmethod
    def _table_style(t: QTableWidget) -> None:
        t.verticalHeader().setVisible(False)
        t.setEditTriggers(QAbstractItemView.NoEditTriggers)
        t.setSelectionMode(QAbstractItemView.NoSelection)
        t.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeToContents)

    def show_evaluation(self, ev: Evaluation | None) -> None:
        if ev is None:
            self.grade.setText("-")
            self.summary.setText("스탯이나 에코를 입력하면 등급을 계산합니다.")
            self.targets.setRowCount(0)
            self.echoes.setRowCount(0)
            self.advice.clear()
            return
        color = GRADE_COLORS.get(ev.grade, "#333")
        self.grade.setText(ev.grade)
        self.grade.setStyleSheet(f"color: {color};")
        ts = f"{ev.target_score:.0f}점" if ev.target_score is not None else "입력 없음"
        es = f"{ev.echo_score:.0f}점 (에코 {len(ev.echoes)}개)" if ev.echo_score is not None else "입력 없음"
        self.summary.setText(f"<span style='font-size:16pt'><b>종합 {ev.total_score:.1f}점</b></span><br>"
                             f"목표 스탯 달성도: {ts}<br>에코 평균 점수: {es}")

        self.targets.setRowCount(len(ev.targets))
        for r, t in enumerate(ev.targets):
            self.targets.setItem(r, 0, QTableWidgetItem(t.label))
            self.targets.setItem(r, 1, QTableWidgetItem("-" if t.current is None else S.fmt(t.stat, t.current)))
            self.targets.setItem(r, 2, QTableWidgetItem(S.fmt(t.stat, t.target)))
            bar = QProgressBar()
            bar.setRange(0, 100)
            bar.setValue(round(t.ratio * 100))
            bar.setFormat("달성" if t.achieved else f"{t.ratio * 100:.0f}%  (-{S.fmt(t.stat, t.deficit)})")
            bar.setStyleSheet(f"QProgressBar::chunk {{ background: {'#27ae60' if t.achieved else '#e67e22'}; }}")
            self.targets.setCellWidget(r, 3, bar)

        self.echoes.setRowCount(len(ev.replace_order))
        for r, e in enumerate(ev.replace_order):
            main = f"{S.label(e.echo.main.stat)} {S.fmt(e.echo.main.stat, e.echo.main.value)}" if e.echo.main and e.echo.main.stat else "-"
            if not e.main_ok:
                main += " ⚠"
            score_item = QTableWidgetItem(f"{e.score:.0f} ({e.grade})")
            score_item.setForeground(Qt.GlobalColor.white)
            score_item.setBackground(_qcolor(GRADE_COLORS.get(e.grade, "#555")))
            self.echoes.setItem(r, 0, QTableWidgetItem(f"{e.index + 1}번"))
            self.echoes.setItem(r, 1, QTableWidgetItem(str(e.echo.cost or "?")))
            self.echoes.setItem(r, 2, QTableWidgetItem(main))
            self.echoes.setItem(r, 3, score_item)
            self.echoes.setItem(r, 4, QTableWidgetItem(" / ".join(e.notes)))
        self.echoes.resizeRowsToContents()

        esc = html.escape
        items = "".join(f"<li><b>{esc(a.title)}</b><br>{esc(a.detail)}</li>" for a in ev.advice)
        self.advice.setHtml(f"<ol>{items}</ol>" if items else "<p>모든 목표를 달성했고 교체할 에코가 없습니다.</p>")


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
        self._loading = False
        self._workers: set[Worker] = set()  # 완료 신호가 전달될 때까지 작업 객체를 붙잡아 둔다

        central = QWidget()
        root = QVBoxLayout(central)
        bar = QHBoxLayout()
        bar.addWidget(QLabel("캐릭터"))
        self.char_combo = QComboBox()
        self.char_combo.setEditable(True)
        self.char_combo.setInsertPolicy(QComboBox.NoInsert)
        self.char_combo.setMinimumWidth(260)
        self.char_combo.completer().setFilterMode(Qt.MatchContains)
        self.char_combo.completer().setCompletionMode(QCompleter.PopupCompletion)
        bar.addWidget(self.char_combo)
        self.data_label = QLabel("")
        self.data_label.setStyleSheet("color: gray;")
        bar.addWidget(self.data_label)
        bar.addStretch()
        self.b_multi = QPushButton("에코 스크린샷 여러 장 열기")
        self.b_update = QPushButton("캐릭터 데이터 업데이트")
        bar.addWidget(self.b_multi)
        bar.addWidget(self.b_update)
        root.addLayout(bar)

        split = QSplitter(Qt.Horizontal)
        self.info = CharacterInfo()
        split.addWidget(self.info)

        mid = QWidget()
        mid_lay = QVBoxLayout(mid)
        self.totals = TotalsEditor()
        mid_lay.addWidget(self.totals)
        echo_box = QGroupBox("장착 에코")
        eb = QVBoxLayout(echo_box)
        self.echo_tabs = QTabWidget()
        self.echo_editors = [EchoEditor(i) for i in range(ECHO_COUNT)]
        for i, ed in enumerate(self.echo_editors):
            self.echo_tabs.addTab(ed, f"에코 {i + 1}")
            ed.changed.connect(self.schedule_eval)
            ed.ocr_requested.connect(lambda img, i=i: self.ocr_echo(i, img))
        eb.addWidget(self.echo_tabs)
        mid_lay.addWidget(echo_box, 1)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(mid)
        split.addWidget(scroll)

        self.results = ResultsPanel()
        split.addWidget(self.results)
        split.setSizes([330, 520, 650])
        root.addWidget(split, 1)
        self.setCentralWidget(central)
        self.statusBar().showMessage("준비")

        self.eval_timer = QTimer(self, singleShot=True, interval=250)
        self.eval_timer.timeout.connect(self.run_eval)
        self.char_combo.currentIndexChanged.connect(self.on_character)
        self.totals.changed.connect(self.schedule_eval)
        self.totals.ocr_requested.connect(self.ocr_totals)
        self.b_update.clicked.connect(self.update_data)
        self.b_multi.clicked.connect(self.open_many_echoes)
        QShortcut(QKeySequence.Paste, self, activated=self.paste_current)

        self.load_data()

    # ----- 데이터 -----
    def load_data(self) -> None:
        if not DEFAULT_DATA.exists():
            self.info.setHtml("<p>캐릭터 데이터가 없습니다. <b>캐릭터 데이터 업데이트</b>를 눌러 공식 공략 사이트에서 받아오세요.</p>")
            QTimer.singleShot(300, self._ask_initial_update)
            return
        self.characters, self.meta = load_characters(DEFAULT_DATA)
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
        self.totals.set_targets(self.current)
        self.load_build()
        self.schedule_eval()

    def build_path(self) -> Path | None:
        return builds_dir() / f"{self.current.id}.json" if self.current else None

    def load_build(self) -> None:
        self._loading = True
        try:
            p = self.build_path()
            data = json.loads(p.read_text(encoding="utf-8")) if p and p.exists() else {}
            self.totals.set_totals(data.get("totals", {}))
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
        p.write_text(json.dumps({"totals": build.totals, "echoes": echoes}, ensure_ascii=False, indent=1),
                     encoding="utf-8")

    # ----- 계산 -----
    def schedule_eval(self) -> None:
        if not self._loading:
            self.eval_timer.start()

    def current_build(self) -> Build:
        return Build(self.totals.totals(), [e for ed in self.echo_editors if (e := ed.get_echo())])

    def run_eval(self) -> None:
        if not self.current:
            self.results.show_evaluation(None)
            return
        build = self.current_build()
        if not build.totals and not build.echoes:
            self.results.show_evaluation(None)
            return
        self.results.show_evaluation(evaluate(self.current, build))
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
            msg = f"코스트 {echo.cost or '?'}, 서브옵션 {len(echo.substats)}개를 읽었습니다."
            if echo.level is not None:
                msg += f" (+{echo.level})"
            if unknown or not echo.cost:
                msg += " 비어 있는 항목은 직접 골라 주세요."
            ed.status.setText(msg)
            ed.status.setStyleSheet("color: #e67e22;" if unknown or not echo.cost else "color: #27ae60;")
            if then:
                then()
        self._start_ocr(_ocr_echo, image, done, ed.status)

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


def main() -> int:
    app = QApplication(sys.argv)
    app.setApplicationName("wuwastat")
    app.setOrganizationName("wuwastat")
    win = MainWindow()
    win.show()
    return app.exec()
