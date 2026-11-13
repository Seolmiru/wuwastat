"""개발용 새로고침. 릴리즈(포터블 ZIP)에는 이 파일이 들어가지 않는다 (tools/build_portable.py APP_EXCLUDE).

저장소에서 실행할 때(.git 폴더가 있을 때)만 켜진다. 환경 변수 WUWASTAT_DEV=0 이면 끈다.

  - data/*.json 저장  → 프로그램을 끄지 않고 데이터만 다시 읽는다 (선택한 캐릭터 유지)
  - wuwastat/*.py 저장 → 문법 검사 후 프로그램을 다시 실행한다 (선택한 캐릭터·창 위치 유지)
  - F5               → 데이터 다시 읽기,  Ctrl+F5 → 프로그램 다시 실행
"""

from __future__ import annotations

import os
import sys
import threading
from pathlib import Path

from PySide6.QtCore import QByteArray, QFileSystemWatcher, QProcess, QProcessEnvironment, QTimer
from PySide6.QtGui import QKeySequence, QShortcut
from PySide6.QtWidgets import QApplication

ROOT = Path(__file__).resolve().parent.parent
CODE_DIR = ROOT / "wuwastat"
DATA_DIR = ROOT / "data"
ENV_CHAR = "WUWASTAT_DEV_CHAR"
ENV_GEOMETRY = "WUWASTAT_DEV_GEOMETRY"


def enabled() -> bool:
    return (ROOT / ".git").exists() and os.environ.get("WUWASTAT_DEV", "1") != "0"


class DevReloader:
    def __init__(self, win):
        self.win = win
        self.pending: set[Path] = set()
        self.watcher = QFileSystemWatcher(win)
        self.watcher.fileChanged.connect(self._changed)
        self.watcher.directoryChanged.connect(self._dir_changed)
        self.timer = QTimer(win, singleShot=True, interval=300)  # 편집기가 여러 번 나눠 저장해도 한 번만
        self.timer.timeout.connect(self._apply)
        self._watch_all()
        QShortcut(QKeySequence("F5"), win, activated=self.reload_data)
        QShortcut(QKeySequence("Ctrl+F5"), win, activated=lambda: self.restart(check=True))
        self._restore()
        win.setWindowTitle(win.windowTitle() + "  [개발 모드]")
        self._status("개발 모드: 파일을 저장하면 자동으로 갱신합니다 (F5 데이터 다시 읽기, Ctrl+F5 다시 실행)")

    # ----- 감시 -----
    def _files(self) -> list[str]:
        return [str(p) for p in [*CODE_DIR.glob("*.py"), *DATA_DIR.glob("*.json")]]

    def _watch_all(self) -> None:
        # 편집기가 파일을 지우고 새로 쓰면 감시가 풀리므로 매번 다시 건다
        files = [f for f in self._files() if f not in self.watcher.files()]
        if files:
            self.watcher.addPaths(files)
        dirs = [str(d) for d in (CODE_DIR, DATA_DIR) if str(d) not in self.watcher.directories()]
        if dirs:
            self.watcher.addPaths(dirs)

    def _changed(self, path: str) -> None:
        p = Path(path)
        if p.suffix in (".py", ".json"):
            self.pending.add(p)
            self.timer.start()

    def _dir_changed(self, _path: str) -> None:
        before = set(self.watcher.files())
        self._watch_all()
        for f in set(self.watcher.files()) - before:  # 새로 생긴 파일
            self._changed(f)

    def _apply(self) -> None:
        self._watch_all()
        changed, self.pending = self.pending, set()
        code = [p for p in changed if p.suffix == ".py" and p.parent == CODE_DIR and p.name != "devreload.py"]
        if code:
            self.restart(check=True, files=code)
        elif any(p.suffix == ".json" for p in changed):
            self.reload_data(", ".join(sorted(p.name for p in changed)))

    # ----- 동작 -----
    def _status(self, msg: str) -> None:
        self.win.statusBar().showMessage(msg, 8000)

    def reload_data(self, what: str = "") -> None:
        try:
            self.win.load_data()  # 선택한 캐릭터를 유지한 채 다시 그린다
        except Exception as e:  # 저장 도중의 깨진 JSON 등
            self._status(f"데이터 다시 읽기 실패: {e}")
            return
        self._status(f"데이터를 다시 읽었습니다{f' ({what})' if what else ''}")

    def restart(self, check: bool = True, files: list[Path] | None = None) -> None:
        if check:  # 문법 오류가 있으면 다시 실행하지 않는다 (pythonw는 오류 창 없이 꺼지므로)
            for f in files or list(CODE_DIR.glob("*.py")):
                try:
                    compile(Path(f).read_text(encoding="utf-8"), str(f), "exec")
                except (SyntaxError, ValueError, OSError) as e:
                    self._status(f"문법 오류로 다시 실행하지 않았습니다: {Path(f).name}: {e}")
                    return
        env = dict(os.environ)
        cur = getattr(self.win, "current", None)
        if cur is not None:
            env[ENV_CHAR] = cur.id
        env[ENV_GEOMETRY] = bytes(self.win.saveGeometry().toHex()).decode()
        proc = QProcess()
        proc.setProgram(sys.executable)
        proc.setArguments(["-m", "wuwastat"])
        proc.setWorkingDirectory(str(ROOT))
        penv = QProcessEnvironment()
        for k, v in env.items():
            penv.insert(k, v)
        proc.setProcessEnvironment(penv)
        ok = proc.startDetached()  # PySide6는 bool만 돌려준다
        if isinstance(ok, tuple):
            ok = ok[0]
        if not ok:
            self._status("다시 실행하지 못했습니다.")
            return
        self.win.close()
        QApplication.quit()
        # 작업 스레드(업데이트 확인, OCR 등)가 남아 있으면 종료가 늦어지므로 잠시 뒤 강제로 끝낸다
        t = threading.Timer(2.0, os._exit, (0,))
        t.daemon = True
        t.start()

    def _restore(self) -> None:
        geo = os.environ.pop(ENV_GEOMETRY, "")
        if geo:
            self.win.restoreGeometry(QByteArray.fromHex(geo.encode()))
        cid = os.environ.pop(ENV_CHAR, "")
        if cid:
            i = self.win.char_combo.findData(cid)
            if i >= 0:
                self.win.char_combo.setCurrentIndex(i)


def install(win) -> DevReloader | None:
    if not enabled():
        return None
    return DevReloader(win)
