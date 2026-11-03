"""eaide_update.py 应用器测试（spec: docs/superpowers/specs/2026-11-03-agent-web-incremental-update-design.md §3/§4）。"""

import hashlib
import json

import pytest
from eaide_update import (
    FACTORY,
    Manifest,
    ManifestError,
    _to_local_path,
    apply_manifest,
    gen_manifest,
    read_active,
    rollback,
    verify_version,
    write_active,
)


def _sha(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


@pytest.fixture
def fake_dir(tmp_path):
    (tmp_path / "_internal" / "agent").mkdir(parents=True)
    (tmp_path / "eaide-agent.exe").write_bytes(b"BOOT")
    (tmp_path / "_internal" / "agent" / "a.py").write_bytes(b"CODE")
    return tmp_path


def test_gen_manifest_walk_and_hash(fake_dir):
    files = gen_manifest(fake_dir)
    paths = {f["path"] for f in files}
    assert paths == {"eaide-agent.exe", "_internal/agent/a.py"}  # POSIX 风格相对路径
    entry = next(f for f in files if f["path"] == "eaide-agent.exe")
    assert entry["sha256"] == _sha(b"BOOT") and entry["size"] == 4


def test_manifest_parse_ok_and_schema_gate(tmp_path, fake_dir):
    doc = {
        "schema": 1,
        "package": "agent",
        "version": "0.1.8",
        "baseUrl": "file:///" + str(fake_dir).replace("\\", "/"),
        "files": gen_manifest(fake_dir),
    }
    (tmp_path / "manifest.json").write_text(json.dumps(doc), encoding="utf-8")
    m = Manifest.load(tmp_path / "manifest.json")
    assert m.package == "agent" and len(m.files) == 2
    bad = dict(doc, schema=99)
    (tmp_path / "bad.json").write_text(json.dumps(bad), encoding="utf-8")
    with pytest.raises(ManifestError, match="schema"):
        Manifest.load(tmp_path / "bad.json")


@pytest.mark.parametrize(
    "evil",
    [
        "../x",
        "/abs",
        "C:\\win",
        "_internal/../../etc/passwd",
        "..\\..\\evil",  # 反斜杠穿越（Windows 下 \ 即分隔符）
        "a\\..\\b",  # 反斜杠内嵌穿越
        "\\\\server\\share\\x",  # UNC
        ".",  # 裸点：指向版本目录自身
    ],
)
def test_manifest_rejects_path_traversal(fake_dir, evil):
    doc = {
        "schema": 1,
        "package": "agent",
        "version": "0.1.8",
        "baseUrl": "x",
        "files": [{"path": evil, "sha256": "0" * 64, "size": 1}],
    }
    with pytest.raises(ManifestError, match="path"):
        Manifest.from_dict(doc)


@pytest.mark.parametrize("bad_version", ["..", ".", "", "0.1.8\n", "a/b"])
def test_manifest_rejects_bad_version(fake_dir, bad_version):
    doc = {
        "schema": 1,
        "package": "agent",
        "version": bad_version,
        "baseUrl": "x",
        "files": [],
    }
    with pytest.raises(ManifestError, match="version"):
        Manifest.from_dict(doc)


def test_manifest_requires_baseurl_and_path_key(fake_dir):
    no_base = {
        "schema": 1,
        "package": "agent",
        "version": "0.1.8",
        "files": [],
    }
    with pytest.raises(ManifestError, match="baseUrl"):
        Manifest.from_dict(no_base)
    missing_path = {
        "schema": 1,
        "package": "agent",
        "version": "0.1.8",
        "baseUrl": "x",
        "files": [{"sha256": "0" * 64, "size": 1}],
    }
    with pytest.raises(ManifestError, match="path"):
        Manifest.from_dict(missing_path)


def test_manifest_rejects_duplicate_path(fake_dir):
    doc = {
        "schema": 1,
        "package": "agent",
        "version": "0.1.8",
        "baseUrl": "x",
        "files": [
            {"path": "a", "sha256": "0" * 64, "size": 1},
            {"path": "a", "sha256": "1" * 64, "size": 1},
        ],
    }
    with pytest.raises(ManifestError, match="duplicate"):
        Manifest.from_dict(doc)


def test_load_file_uri_on_windows_style_path(tmp_path):
    """file:/// URI（含盘符风格路径 + %20 空格编码）必须能被 load。"""
    doc = {
        "schema": 1,
        "package": "web",
        "version": "0.1.8",
        "baseUrl": "x",
        "files": [{"path": "index.html", "sha256": "0" * 64, "size": 1}],
    }
    mdir = tmp_path / "my dir"
    mdir.mkdir()
    mf = mdir / "manifest.json"
    mf.write_text(json.dumps(doc), encoding="utf-8")
    uri = "file:///" + str(mf).replace("\\", "/").replace(" ", "%20")
    m = Manifest.load(uri)
    assert m.package == "web" and m.version == "0.1.8"


def test_to_local_path_case_insensitive_and_unc():
    # scheme/权威名大小写不敏感（S-2）
    assert _to_local_path("FILE:///C:/x/m.json") == _to_local_path("file:///C:/x/m.json")
    assert _to_local_path("file://LOCALHOST/C:/x/m.json").as_posix().endswith("C:/x/m.json")
    # 非空权威 → UNC，且不对 UNC 路径剥盘符
    unc = _to_local_path("file://server/share/x.json")
    assert str(unc).replace("\\", "/").startswith("//server/share")
    drive_as_host = _to_local_path("file://server/C:/share/x")
    assert (
        str(drive_as_host).replace("\\", "/").startswith("//server/C:")
    )  # 非 localhost 权威不剥盘符


# ---- Task 3：指针 / apply / verify / rollback ----


def _mk_payload(base, name, content_map):
    d = base / name
    for rel, data in content_map.items():
        f = d / rel
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_bytes(data)
    return d


def _manifest_doc(root, package, version):
    return {
        "schema": 1,
        "package": package,
        "version": version,
        "baseUrl": "file:///" + str(root).replace("\\", "/"),
        "files": gen_manifest(root),
    }


def test_pointer_roundtrip_and_bad_value_rejected(tmp_path):
    ptr = tmp_path / "agent-active.txt"
    assert read_active(ptr, default=FACTORY) == FACTORY  # 缺省 factory
    write_active(ptr, "0.1.8")
    assert read_active(ptr, default=FACTORY) == "0.1.8"
    ptr.write_text("../../evil", encoding="utf-8")
    assert read_active(ptr, default=FACTORY) == FACTORY  # 坏指针 → default


def test_apply_new_version_and_history(tmp_path):
    payload = _mk_payload(tmp_path, "src", {"eaide-agent.exe": b"A", "_internal/x": b"1"})
    root = tmp_path / "root"
    apply_manifest(_manifest_doc(payload, "agent", "0.1.8"), root, dry_run=False)
    active = root / "versions" / "agent" / "0.1.8"
    assert (active / "eaide-agent.exe").read_bytes() == b"A"
    assert read_active(root / "versions" / "agent-active.txt", FACTORY) == "0.1.8"
    hist = (root / "versions" / "history.jsonl").read_text(encoding="utf-8")
    assert '"from": "factory"' in hist and '"to": "0.1.8"' in hist
    assert (active / ".filehash.json").exists()  # 安装哈希缓存


def test_apply_idempotent_and_repairs_damage(tmp_path):
    payload = _mk_payload(tmp_path, "src", {"a": b"1", "b": b"2"})
    root = tmp_path / "root"
    doc = _manifest_doc(payload, "agent", "0.1.8")
    apply_manifest(doc, root, dry_run=False)
    target = root / "versions" / "agent" / "0.1.8" / "b"
    target.write_bytes(b"TAMPERED")  # 模拟本地损坏
    apply_manifest(doc, root, dry_run=False)  # 幂等修复
    assert target.read_bytes() == b"2"


def test_apply_hash_mismatch_fails_and_keeps_pointer(tmp_path):
    payload = _mk_payload(tmp_path, "src", {"a": b"1"})
    doc = _manifest_doc(payload, "agent", "0.1.8")
    doc["files"][0]["sha256"] = "0" * 64  # 故意错 hash
    root = tmp_path / "root"
    with pytest.raises(ManifestError, match="sha256"):
        apply_manifest(doc, root, dry_run=False)
    assert read_active(root / "versions" / "agent-active.txt", FACTORY) == FACTORY
    assert not (root / "versions" / "agent" / "0.1.8").exists()  # 坏版本未落位
    staging = root / "versions" / ".staging"
    assert not (staging.exists() and list(staging.glob("*")))  # 暂存已清理


def test_apply_dry_run_writes_nothing(tmp_path):
    payload = _mk_payload(tmp_path, "src", {"a": b"1"})
    root = tmp_path / "root"
    apply_manifest(_manifest_doc(payload, "agent", "0.1.8"), root, dry_run=True)
    assert not (root / "versions").exists()


def test_rollback_explicit_and_history(tmp_path):
    p1 = _mk_payload(tmp_path, "s1", {"a": b"1"})
    p2 = _mk_payload(tmp_path, "s2", {"a": b"2"})
    root = tmp_path / "root"
    apply_manifest(_manifest_doc(p1, "agent", "0.1.7"), root, dry_run=False)
    apply_manifest(_manifest_doc(p2, "agent", "0.1.8"), root, dry_run=False)
    rollback(root, "agent")  # → 0.1.7（history 倒数）
    assert read_active(root / "versions" / "agent-active.txt", FACTORY) == "0.1.7"
    rollback(root, "agent", version=FACTORY)  # 显式 factory
    assert read_active(root / "versions" / "agent-active.txt", FACTORY) == FACTORY


def test_rollback_unknown_version_rejected(tmp_path):
    root = tmp_path / "root"
    (root / "versions" / "agent").mkdir(parents=True)
    with pytest.raises(SystemExit, match="not found"):
        rollback(root, "agent", version="9.9.9")


def test_verify_full_rehash(tmp_path):
    payload = _mk_payload(tmp_path, "src", {"a": b"1"})
    doc = _manifest_doc(payload, "agent", "0.1.8")
    root = tmp_path / "root"
    apply_manifest(doc, root, dry_run=False)
    vdir = root / "versions" / "agent" / "0.1.8"
    verify_version(vdir, doc)  # OK 不抛
    (vdir / "a").write_bytes(b"x")
    with pytest.raises(ManifestError, match="mismatch"):
        verify_version(vdir, doc)
