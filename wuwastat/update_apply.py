"""업데이트 적용기. 표준 라이브러리만 쓴다 (wuwastat 패키지를 불러오지 않는다).

앱이 업데이트를 준비하면 임시 폴더에 복사한 작은 Python으로 이 파일을 실행하고 앱은 끝난다.
이 스크립트는 앱이 끝나기를 기다렸다가 프로그램 폴더의 app, python, wuwastat.bat, README.txt 를
새 버전으로 바꾸고 프로그램을 다시 실행한다. 프로그램 폴더의 python 폴더로 실행하지 않으므로 그 폴더를 잠그지 않는다.

교체 중 문제가 생기면 원래 파일로 되돌리고 알림 창을 띄운다.
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
import time
import traceback
from pathlib import Path
from typing import Callable

ITEMS = ("app", "python", "wuwastat.bat", "README.txt")
# 새 버전에도 옮겨 줄 사용자 파일 (프로그램 폴더 안에 저장되는 것)
PRESERVE = ("app/data/characters.local.json",)
WORK_NAME = "_update"
BACKUP_NAME = "_update_backup"
TITLE = "명조 육성 등급 계산기 업데이트"

DETACHED_PROCESS = 0x00000008
CREATE_NEW_PROCESS_GROUP = 0x00000200


def wait_for_exit(pid: int, timeout: float = 300.0) -> bool:
    """프로세스가 끝날 때까지 기다린다. 끝났으면 True."""
    if sys.platform == "win32":
        import ctypes

        kernel32 = ctypes.windll.kernel32
        synchronize = 0x00100000
        handle = kernel32.OpenProcess(synchronize, False, pid)
        if not handle:  # 이미 끝남
            return True
        try:
            return kernel32.WaitForSingleObject(handle, int(timeout * 1000)) == 0
        finally:
            kernel32.CloseHandle(handle)
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            os.kill(pid, 0)  # POSIX: 신호 0은 존재 여부만 확인한다
        except OSError:
            return True
        time.sleep(0.2)
    return False


def _retry(fn: Callable, *args, attempts: int, delay: float):
    """백신 검사 등으로 파일이 잠깐 잠겨 있을 수 있으므로 몇 번 다시 시도한다."""
    for i in range(attempts):
        try:
            return fn(*args)
        except OSError:
            if i == attempts - 1:
                raise
            time.sleep(delay)


def _remove(p: Path) -> None:
    if p.is_dir() and not p.is_symlink():
        shutil.rmtree(p, ignore_errors=True)
    else:
        try:
            p.unlink()
        except OSError:
            pass


def apply_update(root: Path, stage: Path, items=ITEMS, preserve=PRESERVE, log: Callable[[str], None] = print,
                 attempts: int = 40, delay: float = 0.5) -> None:
    """root의 items를 stage의 것으로 바꾼다. 실패하면 원래대로 되돌리고 예외를 다시 던진다."""
    backup = root / BACKUP_NAME
    if backup.exists():
        shutil.rmtree(backup)
    backup.mkdir()
    moved: list[str] = []
    installed: list[str] = []
    try:
        for name in items:
            if (root / name).exists():
                _retry(os.replace, root / name, backup / name, attempts=attempts, delay=delay)
                moved.append(name)
        for name in items:
            if (stage / name).exists():
                _retry(os.replace, stage / name, root / name, attempts=attempts, delay=delay)
                installed.append(name)
        for rel in preserve:
            old, new = backup / rel, root / rel
            if old.is_file():
                new.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(old, new)
    except Exception:
        log("교체 실패, 되돌리는 중:\n" + traceback.format_exc())
        for name in reversed(installed):
            try:
                os.replace(root / name, stage / name)
            except OSError:
                _remove(root / name)
        for name in reversed(moved):
            _retry(os.replace, backup / name, root / name, attempts=attempts, delay=delay)
        shutil.rmtree(backup, ignore_errors=True)
        raise
    shutil.rmtree(backup, ignore_errors=True)


def check_paths(root: Path, stage: Path, work: Path) -> None:
    """엉뚱한 폴더를 건드리지 않도록 경로 관계를 확인한다."""
    root, stage, work = root.resolve(), stage.resolve(), work.resolve()
    if work != root / WORK_NAME:
        raise RuntimeError(f"작업 폴더가 프로그램 폴더 안의 {WORK_NAME}이 아닙니다: {work}")
    if work not in stage.parents:
        raise RuntimeError(f"새 버전 폴더가 작업 폴더 안에 있지 않습니다: {stage}")
    if not (root / "wuwastat.bat").is_file():
        raise RuntimeError(f"프로그램 폴더가 아닙니다: {root}")


def relaunch(root: Path) -> None:
    exe = root / "python" / "pythonw.exe"
    if not exe.is_file():
        return
    flags = DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP if sys.platform == "win32" else 0
    subprocess.Popen([str(exe), "-m", "wuwastat"], cwd=str(root), close_fds=True, creationflags=flags)


def _message(text: str) -> None:
    if sys.platform == "win32":
        import ctypes

        ctypes.windll.user32.MessageBoxW(None, text, TITLE, 0x10)  # MB_ICONERROR
    else:
        print(text, file=sys.stderr)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--pid", type=int, required=True)
    ap.add_argument("--root", required=True)
    ap.add_argument("--stage", required=True)
    ap.add_argument("--work", required=True)
    ap.add_argument("--log", required=True)
    args = ap.parse_args(argv)
    log_path = Path(args.log)

    def log(msg: str) -> None:
        try:
            with open(log_path, "a", encoding="utf-8") as f:
                f.write(time.strftime("%Y-%m-%d %H:%M:%S ") + msg + "\n")
        except OSError:
            pass

    root, stage, work = Path(args.root), Path(args.stage), Path(args.work)
    try:
        check_paths(root, stage, work)
    except RuntimeError as e:
        log(str(e))
        _message(f"업데이트를 진행하지 않았습니다.\n\n{e}")
        return 2

    ok = False
    exited = False
    try:
        log(f"프로그램 종료 대기 (pid {args.pid})")
        exited = wait_for_exit(args.pid)
        if not exited:
            raise RuntimeError("프로그램이 끝나지 않아 업데이트하지 못했습니다. 프로그램을 모두 닫고 다시 시도하세요.")
        apply_update(root, stage, log=log)
        ok = True
        log("교체 완료")
    except Exception as e:  # 사용자에게 알리고 이전 버전을 다시 실행한다
        log(traceback.format_exc())
        _message(f"업데이트에 실패해 이전 버전을 그대로 둡니다.\n\n{e}\n\n기록 파일: {log_path}")
    finally:
        shutil.rmtree(work, ignore_errors=True)
    if exited:  # 아직 실행 중인 프로그램이 있으면 하나 더 띄우지 않는다
        relaunch(root)
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
