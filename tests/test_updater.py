"""프로그램 업데이트: 버전 비교, 릴리즈 해석, 서명 확인, 압축 풀기, 폴더 교체."""

import base64
import json
import os
import zipfile
from pathlib import Path

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from wuwastat import update_apply, updater
from wuwastat.updater import UpdateError

DL = updater.DOWNLOAD_PREFIX


def keypair():
    key = Ed25519PrivateKey.generate()
    pub = key.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
    return key, base64.b64encode(pub).decode()


def make_zip(path: Path, version: str = "9.0.0", extra: dict | None = None) -> Path:
    files = {
        "wuwastat/wuwastat.bat": "@echo off",
        "wuwastat/README.txt": "new",
        "wuwastat/python/pythonw.exe": "exe",
        "wuwastat/app/wuwastat/__init__.py": f'__version__ = "{version}"\n',
        "wuwastat/app/wuwastat/update_apply.py": "# apply",
    }
    files.update(extra or {})
    with zipfile.ZipFile(path, "w") as z:
        for name, text in files.items():
            z.writestr(name, text)
    return path


def sign(key, zip_path: Path, version: str, name: str | None = None) -> bytes:
    name = name or zip_path.name
    doc = updater.make_signature_doc(key, version, name, updater.file_sha256(zip_path))
    return json.dumps(doc).encode()


# --- 버전 ---------------------------------------------------------------------
def test_version_compare():
    assert updater.is_newer("v0.3.0", "0.2.2")
    assert updater.is_newer("0.10.0", "0.9.9")  # 문자열이 아니라 숫자로 비교
    assert not updater.is_newer("v0.2.2", "0.2.2")
    assert not updater.is_newer("0.2", "0.2.0")
    assert not updater.is_newer("0.1.9", "0.2.0")
    with pytest.raises(UpdateError):
        updater.parse_version("latest")


# --- 릴리즈 정보 -----------------------------------------------------------------
def release_json(tag="v0.9.0", sig=True, zip_url=None):
    name = f"wuwastat-{tag.lstrip('v')}-win64.zip"
    assets = [{"name": name, "browser_download_url": zip_url or f"{DL}{tag}/{name}"}]
    if sig:
        assets.append({"name": name + ".sig", "browser_download_url": f"{DL}{tag}/{name}.sig"})
    return {"tag_name": tag, "assets": assets, "body": "notes",
            "html_url": f"https://github.com/{updater.REPO}/releases/tag/{tag}"}


def test_parse_release_picks_assets():
    r = updater.parse_release(release_json())
    assert r.version == "0.9.0" and r.zip_name == "wuwastat-0.9.0-win64.zip" and r.signed
    assert not updater.parse_release(release_json(sig=False)).signed


def test_parse_release_rejects_foreign_url_and_prerelease():
    with pytest.raises(UpdateError):
        updater.parse_release(release_json(zip_url="https://evil.example/wuwastat-0.9.0-win64.zip"))
    with pytest.raises(UpdateError):
        updater.parse_release({**release_json(), "prerelease": True})
    with pytest.raises(UpdateError):
        updater.parse_release({**release_json(), "assets": []})


def test_download_only_from_release_url(tmp_path):
    with pytest.raises(UpdateError):
        updater.download("https://evil.example/x.zip", tmp_path / "x", 10)


# --- 서명 ---------------------------------------------------------------------
def test_signature_roundtrip(tmp_path):
    key, pub = keypair()
    z = make_zip(tmp_path / "wuwastat-9.0.0-win64.zip")
    updater.verify_update(z, sign(key, z, "9.0.0"), "9.0.0", z.name, pub)


def test_signature_rejects_tampered_zip(tmp_path):
    key, pub = keypair()
    z = make_zip(tmp_path / "wuwastat-9.0.0-win64.zip")
    sig = sign(key, z, "9.0.0")
    with open(z, "ab") as f:
        f.write(b"x")
    with pytest.raises(UpdateError, match="서명된 파일과 다릅니다"):
        updater.verify_update(z, sig, "9.0.0", z.name, pub)


def test_signature_rejects_other_key(tmp_path):
    key, _ = keypair()
    _, other_pub = keypair()
    z = make_zip(tmp_path / "wuwastat-9.0.0-win64.zip")
    with pytest.raises(UpdateError, match="서명이 올바르지 않습니다"):
        updater.verify_update(z, sign(key, z, "9.0.0"), "9.0.0", z.name, other_pub)


def test_signature_rejects_forged_doc_fields(tmp_path):
    """계정이 털려도 서명 파일 내용(버전·해시)만 고쳐서는 통과할 수 없다."""
    key, pub = keypair()
    real = make_zip(tmp_path / "wuwastat-9.0.0-win64.zip")
    doc = json.loads(sign(key, real, "9.0.0"))
    evil = make_zip(tmp_path / "evil.zip", extra={"wuwastat/app/wuwastat/evil.py": "boom"})
    doc["sha256"] = updater.file_sha256(evil)
    with pytest.raises(UpdateError, match="서명이 올바르지 않습니다"):
        updater.verify_update(evil, json.dumps(doc).encode(), "9.0.0", real.name, pub)


def test_signature_rejects_replayed_old_release(tmp_path):
    """예전 버전의 정상 서명 ZIP을 새 버전 태그로 다시 올려도 거부한다."""
    key, pub = keypair()
    old = make_zip(tmp_path / "wuwastat-9.0.0-win64.zip", "9.0.0")
    sig = sign(key, old, "9.0.0")
    with pytest.raises(UpdateError, match="버전 또는 파일 이름"):
        updater.verify_update(old, sig, "9.9.9", "wuwastat-9.9.9-win64.zip", pub)


def test_signature_rejects_garbage_and_missing_key(tmp_path):
    _, pub = keypair()
    z = make_zip(tmp_path / "wuwastat-9.0.0-win64.zip")
    with pytest.raises(UpdateError):
        updater.verify_update(z, b"not json", "9.0.0", z.name, pub)
    with pytest.raises(UpdateError):
        updater.verify_update(z, b"{}", "9.0.0", z.name, pub)
    with pytest.raises(UpdateError, match="공개 키가 없어"):
        updater.verify_update(z, b"{}", "9.0.0", z.name, "")


def test_app_has_public_key():
    from wuwastat.update_key import PUBLIC_KEY_B64

    updater.load_public_key(PUBLIC_KEY_B64)  # 32바이트 Ed25519 키


def test_no_private_key_in_repo():
    root = Path(updater.__file__).resolve().parents[1]
    for p in root.rglob("*"):
        if any(part in (".venv", "build", ".git") or part.startswith(".venv") for part in p.parts):
            continue
        if p.is_file() and p.suffix in (".pem", ".key", ".py", ".txt", ".json"):
            assert b"PRIVATE KEY" not in p.read_bytes() or p.name == "test_updater.py", p


# --- 압축 풀기 -------------------------------------------------------------------
def test_safe_extract_ok(tmp_path):
    z = make_zip(tmp_path / "u.zip", "9.0.0")
    stage = updater.safe_extract(z, tmp_path / "stage")
    assert updater.staged_version(stage) == "9.0.0"


@pytest.mark.parametrize("bad", ["../evil.txt", "wuwastat/../../evil.txt", "/abs.txt", "other/x.txt", "C:/x.txt"])
def test_safe_extract_rejects_bad_paths(tmp_path, bad):
    z = make_zip(tmp_path / "u.zip", extra={bad: "x"})
    with pytest.raises(UpdateError, match="허용되지 않는 경로"):
        updater.safe_extract(z, tmp_path / "stage")
    assert not (tmp_path / "evil.txt").exists()


def test_safe_extract_requires_files(tmp_path):
    z = tmp_path / "u.zip"
    with zipfile.ZipFile(z, "w") as zf:
        zf.writestr("wuwastat/README.txt", "x")
    with pytest.raises(UpdateError, match="필요한 파일"):
        updater.safe_extract(z, tmp_path / "stage")


# --- 실행기 / 포터블 감지 ----------------------------------------------------------
def test_prepare_runner_copies_core_only(tmp_path):
    py = tmp_path / "python"
    (py / "Lib" / "site-packages").mkdir(parents=True)
    for f in ("python.exe", "pythonw.exe", "python314.dll", "python3.dll", "python314.zip", "_ctypes.pyd"):
        (py / f).write_text(f)
    (py / "python314._pth").write_text("python314.zip\n.\nLib\\site-packages\n..\\app\nimport site\n")
    script = tmp_path / "update_apply.py"
    script.write_text("# apply")
    exe = updater.prepare_runner(py, script, tmp_path / "runner")
    runner = exe.parent
    assert exe.name == "pythonw.exe" and (runner / "_ctypes.pyd").exists() and (runner / "update_apply.py").exists()
    assert not (runner / "Lib").exists()
    assert (runner / "python314._pth").read_text().split() == ["python314.zip", "."]


def test_portable_root_detection(tmp_path):
    pkg = tmp_path / "app" / "wuwastat"
    pkg.mkdir(parents=True)
    (pkg / "updater.py").write_text("")
    assert updater.portable_root(pkg / "updater.py") is None
    (tmp_path / "wuwastat.bat").write_text("")
    (tmp_path / "python").mkdir()
    (tmp_path / "python" / "pythonw.exe").write_text("")
    assert updater.portable_root(pkg / "updater.py") == tmp_path.resolve()
    assert updater.portable_root(updater.__file__) is None  # 저장소에서 실행 중


def test_prepare_update_refuses_unsigned_or_old(tmp_path):
    r = updater.parse_release(release_json(sig=False))
    with pytest.raises(UpdateError, match="서명 파일"):
        updater.prepare_update(r, tmp_path)
    old = updater.parse_release(release_json(tag="v0.0.1"))
    with pytest.raises(UpdateError, match="최신 버전"):
        updater.prepare_update(old, tmp_path)


# --- 폴더 교체 (update_apply) ------------------------------------------------------
def make_install(root: Path, label: str) -> None:
    (root / "app" / "wuwastat").mkdir(parents=True)
    (root / "app" / "wuwastat" / "__init__.py").write_text(label)
    (root / "app" / "data").mkdir()
    (root / "python").mkdir()
    (root / "python" / "pythonw.exe").write_text(label)
    (root / "wuwastat.bat").write_text(label)
    (root / "README.txt").write_text(label)


def test_apply_update_replaces_and_keeps_user_data(tmp_path):
    root = tmp_path / "wuwastat"
    make_install(root, "old")
    (root / "app" / "data" / "characters.local.json").write_text("user data")
    stage = root / "_update" / "stage" / "wuwastat"
    make_install(stage, "new")
    update_apply.apply_update(root, stage, log=lambda m: None)
    assert (root / "app" / "wuwastat" / "__init__.py").read_text() == "new"
    assert (root / "python" / "pythonw.exe").read_text() == "new"
    assert (root / "README.txt").read_text() == "new"
    assert (root / "app" / "data" / "characters.local.json").read_text() == "user data"
    assert not (root / update_apply.BACKUP_NAME).exists()


def test_apply_update_rolls_back_on_failure(tmp_path, monkeypatch):
    root = tmp_path / "wuwastat"
    make_install(root, "old")
    stage = root / "_update" / "stage" / "wuwastat"
    make_install(stage, "new")
    real_replace = os.replace

    def flaky(src, dst):  # 새 python 폴더를 넣는 순간 실패
        if Path(src) == stage / "python":
            raise PermissionError("locked")
        return real_replace(src, dst)

    monkeypatch.setattr(update_apply.os, "replace", flaky)
    with pytest.raises(PermissionError):
        update_apply.apply_update(root, stage, log=lambda m: None, attempts=2, delay=0)
    for rel in ("app/wuwastat/__init__.py", "python/pythonw.exe", "wuwastat.bat", "README.txt"):
        assert (root / rel).read_text() == "old", rel
    assert not (root / update_apply.BACKUP_NAME).exists()


def test_check_paths_guards_against_wrong_folders(tmp_path):
    root = tmp_path / "wuwastat"
    make_install(root, "old")
    work = root / "_update"
    stage = work / "stage" / "wuwastat"
    stage.mkdir(parents=True)
    update_apply.check_paths(root, stage, work)
    with pytest.raises(RuntimeError):
        update_apply.check_paths(root, tmp_path / "elsewhere", work)
    with pytest.raises(RuntimeError):
        update_apply.check_paths(root, stage, tmp_path / "_update")
    with pytest.raises(RuntimeError):
        update_apply.check_paths(tmp_path, tmp_path / "_update" / "s", tmp_path / "_update")  # wuwastat.bat 없음


def test_update_apply_uses_stdlib_only():
    """적용기는 site-packages 없이 실행되므로 표준 라이브러리만 불러와야 한다."""
    import ast

    src = Path(update_apply.__file__).read_text(encoding="utf-8")
    allowed = {"__future__", "argparse", "os", "shutil", "subprocess", "sys", "time", "traceback", "pathlib",
               "typing", "ctypes"}
    for node in ast.walk(ast.parse(src)):
        if isinstance(node, ast.Import):
            names = [a.name.split(".")[0] for a in node.names]
        elif isinstance(node, ast.ImportFrom):
            assert node.level == 0, "상대 import 금지"
            names = [(node.module or "").split(".")[0]]
        else:
            continue
        assert set(names) <= allowed, names
    assert update_apply.WORK_NAME == updater.WORK_NAME
