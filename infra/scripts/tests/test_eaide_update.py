"""eaide_update.py 应用器测试（spec: docs/superpowers/specs/2026-11-03-agent-web-incremental-update-design.md §3/§4）。"""

import hashlib
import json
import os
import sys
from pathlib import Path

import pytest
from eaide_update import (
    FACTORY,
    Manifest,
    ManifestError,
    _to_local_path,
    apply_manifest,
    cleanup_staging,
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


# ---- Task 3 复审回归（917afc7 review）----


def _hist(root, package="agent"):
    return (root / "versions" / "history.jsonl").read_text(encoding="utf-8")


def test_history_records_have_ts(tmp_path):
    payload = _mk_payload(tmp_path, "src", {"a": b"1"})
    root = tmp_path / "root"
    apply_manifest(_manifest_doc(payload, "agent", "0.1.8"), root, dry_run=False)
    for line in _hist(root).splitlines():
        rec = json.loads(line)
        assert isinstance(rec["ts"], int) and rec["ts"] > 0


def test_rollback_survives_corrupt_history(tmp_path):
    p1 = _mk_payload(tmp_path, "s1", {"a": b"1"})
    p2 = _mk_payload(tmp_path, "s2", {"a": b"2"})
    root = tmp_path / "root"
    apply_manifest(_manifest_doc(p1, "agent", "0.1.7"), root, dry_run=False)
    apply_manifest(_manifest_doc(p2, "agent", "0.1.8"), root, dry_run=False)
    hf = root / "versions" / "history.jsonl"
    lines = hf.read_text(encoding="utf-8").rstrip("\n").split("\n")
    hf.write_text("\n".join([*lines, '{"package": "ag', "123"]) + "\n", encoding="utf-8")
    rollback(root, "agent")  # 坏行跳过，仍退到 0.1.7
    assert read_active(root / "versions" / "agent-active.txt", FACTORY) == "0.1.7"


def test_rollback_null_from_goes_factory(tmp_path):
    payload = _mk_payload(tmp_path, "src", {"a": b"1"})
    root = tmp_path / "root"
    apply_manifest(_manifest_doc(payload, "agent", "0.1.8"), root, dry_run=False)
    hf = root / "versions" / "history.jsonl"
    rec = json.loads(hf.read_text(encoding="utf-8").splitlines()[-1])
    rec["from"] = None
    hf.write_text(json.dumps(rec) + "\n", encoding="utf-8")
    rollback(root, "agent")
    assert read_active(root / "versions" / "agent-active.txt", FACTORY) == FACTORY


def test_rollback_after_duplicate_apply(tmp_path):
    """重复 apply 当前版本不得污染回滚链（from==to 不动点）。"""
    p1 = _mk_payload(tmp_path, "s1", {"a": b"1"})
    p2 = _mk_payload(tmp_path, "s2", {"a": b"2"})
    root = tmp_path / "root"
    apply_manifest(_manifest_doc(p1, "agent", "0.1.7"), root, dry_run=False)
    doc2 = _manifest_doc(p2, "agent", "0.1.8")
    apply_manifest(doc2, root, dry_run=False)
    apply_manifest(doc2, root, dry_run=False)  # 幂等重复
    rollback(root, "agent")
    assert read_active(root / "versions" / "agent-active.txt", FACTORY) == "0.1.7"
    rollback(root, "agent")  # 二次回滚不得反弹回 0.1.8
    assert read_active(root / "versions" / "agent-active.txt", FACTORY) == FACTORY


def test_rollback_dot_version_rejected(tmp_path):
    root = tmp_path / "root"
    (root / "versions" / "agent").mkdir(parents=True)
    with pytest.raises(SystemExit, match="bad version"):
        rollback(root, "agent", version="..")


def test_rollback_missing_dir_after_damage(tmp_path):
    payload = _mk_payload(tmp_path, "src", {"a": b"1"})
    doc = _manifest_doc(payload, "agent", "0.1.8")
    root = tmp_path / "root"
    apply_manifest(doc, root, dry_run=False)
    (root / "versions" / "agent" / "0.1.8" / "a").write_bytes(b"CORRUPT")
    p2 = _mk_payload(tmp_path, "s2", {"a": b"2"})
    apply_manifest(_manifest_doc(p2, "agent", "0.1.9"), root, dry_run=False)
    with pytest.raises(SystemExit, match="verify"):
        rollback(root, "agent", version="0.1.8")  # 目标目录损坏 → 拒指


def test_apply_adopts_relative_source_dir(tmp_path, monkeypatch):
    payload = _mk_payload(tmp_path, "src", {"a": b"1", "sub/b": b"2"})
    root = tmp_path / "root"
    monkeypatch.chdir(tmp_path)
    apply_manifest(
        _manifest_doc(payload, "agent", "0.1.8"),
        root,
        dry_run=False,
        source_dir=Path("src"),
    )
    assert (root / "versions" / "agent" / "0.1.8" / "sub" / "b").read_bytes() == b"2"


def test_apply_plain_directory_baseurl(tmp_path):
    """§3：baseUrl 允许裸本地路径。"""
    payload = _mk_payload(tmp_path, "src", {"a": b"1"})
    doc = _manifest_doc(payload, "agent", "0.1.8")
    doc["baseUrl"] = str(payload)  # 裸 Windows 路径
    root = tmp_path / "root"
    apply_manifest(doc, root, dry_run=False)
    assert (root / "versions" / "agent" / "0.1.8" / "a").read_bytes() == b"1"


def test_apply_http_branch_with_quoting(tmp_path, monkeypatch):
    import io
    import urllib.parse

    import eaide_update

    payload = _mk_payload(tmp_path, "src", {"a b/c.bin": b"Z"})
    doc = _manifest_doc(payload, "agent", "0.1.8")
    doc["baseUrl"] = "https://example.invalid/updates/0.1.8"
    seen = []

    def fake_urlopen(req_or_url, timeout=None):
        seen.append((str(req_or_url), timeout))
        return io.BytesIO(b"Z")

    monkeypatch.setattr(eaide_update.urllib.request, "urlopen", fake_urlopen)
    apply_manifest(doc, tmp_path / "root", dry_run=False)
    got = tmp_path / "root" / "versions" / "agent" / "0.1.8" / "a b" / "c.bin"
    assert got.read_bytes() == b"Z"
    assert urllib.parse.quote("a b") in seen[0][0] and seen[0][0].endswith("c.bin")
    assert seen[0][1] == 30  # timeout 必须透传（Minor#4）


def test_pointer_file_is_single_line_with_lf(tmp_path):
    ptr = tmp_path / "agent-active.txt"
    write_active(ptr, "0.1.9")
    assert ptr.read_bytes() == b"0.1.9\n"
    ptr.write_bytes(b"0.1.9\n")
    assert read_active(ptr, default=FACTORY) == "0.1.9"  # LF 回读兼容


def test_repair_branch_file_atomic_and_filehash(tmp_path):
    payload = _mk_payload(tmp_path, "src", {"a": b"1", "b": b"2"})
    doc = _manifest_doc(payload, "agent", "0.1.8")
    root = tmp_path / "root"
    apply_manifest(doc, root, dry_run=False)
    vdir = root / "versions" / "agent" / "0.1.8"
    (vdir / "a").unlink()  # 模拟缺失
    apply_manifest(doc, root, dry_run=False)
    assert (vdir / "a").read_bytes() == b"1"
    cache = json.loads((vdir / ".filehash.json").read_text(encoding="utf-8"))
    assert cache == {f["path"]: f["sha256"] for f in doc["files"]}  # 修复后刷新
    assert not (vdir / ".fetch").exists()  # 旁路临时目录不留残留


def test_manifest_rejects_reserved_version(fake_dir):
    doc = {
        "schema": 1,
        "package": "agent",
        "version": "factory",
        "baseUrl": "x",
        "files": [],
    }
    with pytest.raises(ManifestError, match="version"):
        Manifest.from_dict(doc)


# ---- Task 3 二轮复审回归（461dcd0 review）----


@pytest.mark.parametrize(
    "reserved",
    [".fetch/x", ".staging/y", ".filehash.json", ".filehash.json/b", "a/.filehash.json"],
)
def test_manifest_rejects_reserved_internal_paths(reserved):
    doc = {
        "schema": 1,
        "package": "agent",
        "version": "0.1.8",
        "baseUrl": "x",
        "files": [{"path": reserved, "sha256": "0" * 64, "size": 1}],
    }
    with pytest.raises(ManifestError, match="reserved"):
        Manifest.from_dict(doc)


def test_rollback_at_factory_is_terminal(tmp_path):
    """第三次 rollback 不得把 0.1.7 反弹回来（factory 终态）。"""
    p1 = _mk_payload(tmp_path, "s1", {"a": b"1"})
    p2 = _mk_payload(tmp_path, "s2", {"a": b"2"})
    root = tmp_path / "root"
    apply_manifest(_manifest_doc(p1, "agent", "0.1.7"), root, dry_run=False)
    apply_manifest(_manifest_doc(p2, "agent", "0.1.8"), root, dry_run=False)
    rollback(root, "agent")
    rollback(root, "agent")
    assert read_active(root / "versions" / "agent-active.txt", FACTORY) == FACTORY
    with pytest.raises(SystemExit, match="factory"):
        rollback(root, "agent")
    assert read_active(root / "versions" / "agent-active.txt", FACTORY) == FACTORY


def test_rollback_consumes_records_in_downgrade_cycle(tmp_path):
    """7→8→7 降级链后 rollback：直退 factory，不重新激活刚离开的 8。"""
    p1 = _mk_payload(tmp_path, "s1", {"a": b"1"})
    p2 = _mk_payload(tmp_path, "s2", {"a": b"2"})
    root = tmp_path / "root"
    doc1 = _manifest_doc(p1, "agent", "0.1.7")
    doc2 = _manifest_doc(p2, "agent", "0.1.8")
    apply_manifest(doc1, root, dry_run=False)
    apply_manifest(doc2, root, dry_run=False)
    apply_manifest(doc1, root, dry_run=False)  # 降级回 7
    rollback(root, "agent")
    ptr = read_active(root / "versions" / "agent-active.txt", FACTORY)
    assert ptr != "0.1.8"  # 不得重新激活刚离开的版本
    assert ptr == FACTORY  # 首现即切入链起点 → factory 终态
    with pytest.raises(SystemExit, match="factory"):
        rollback(root, "agent")  # 终态后继续回退被拒


def test_rollback_nonstring_from_no_traceback(tmp_path):
    payload = _mk_payload(tmp_path, "src", {"a": b"1"})
    root = tmp_path / "root"
    apply_manifest(_manifest_doc(payload, "agent", "0.1.8"), root, dry_run=False)
    hf = root / "versions" / "history.jsonl"
    rec = json.loads(hf.read_text(encoding="utf-8").splitlines()[-1])
    rec["from"] = 123
    hf.write_text(json.dumps(rec) + "\n", encoding="utf-8")
    rollback(root, "agent")  # 非字符串 from → 归一 factory，不 TypeError
    assert read_active(root / "versions" / "agent-active.txt", FACTORY) == FACTORY


def test_rollback_corrupt_filehash_gives_friendly_error(tmp_path):
    p1 = _mk_payload(tmp_path, "s1", {"a": b"1"})
    p2 = _mk_payload(tmp_path, "s2", {"a": b"2"})
    root = tmp_path / "root"
    apply_manifest(_manifest_doc(p1, "agent", "0.1.7"), root, dry_run=False)
    apply_manifest(_manifest_doc(p2, "agent", "0.1.8"), root, dry_run=False)
    cache = root / "versions" / "agent" / "0.1.7" / ".filehash.json"
    cache.write_text('{"a": "', encoding="utf-8")  # 截断的缓存
    with pytest.raises(SystemExit, match="filehash"):
        rollback(root, "agent")
    assert read_active(root / "versions" / "agent-active.txt", FACTORY) == "0.1.8"


@pytest.mark.parametrize("junk", ["[1,2]", "42", "null"])
def test_rollback_nonobject_filehash_no_traceback(tmp_path, junk):
    """合法 JSON 但非对象（list/int/null）同样友好拒绝，不 AttributeError。"""
    p1 = _mk_payload(tmp_path, "s1", {"a": b"1"})
    p2 = _mk_payload(tmp_path, "s2", {"a": b"2"})
    root = tmp_path / "root"
    apply_manifest(_manifest_doc(p1, "agent", "0.1.7"), root, dry_run=False)
    apply_manifest(_manifest_doc(p2, "agent", "0.1.8"), root, dry_run=False)
    cache = root / "versions" / "agent" / "0.1.7" / ".filehash.json"
    cache.write_text(junk, encoding="utf-8")
    with pytest.raises(SystemExit, match="filehash"):
        rollback(root, "agent")
    assert read_active(root / "versions" / "agent-active.txt", FACTORY) == "0.1.8"


def test_repair_branch_leaves_no_fetch_junk_after_failure(tmp_path, monkeypatch):
    """修复分支中途失败（磁盘文件拉不回）不在活跃目录留旁路垃圾。"""
    payload = _mk_payload(tmp_path, "src", {"a": b"1", "b": b"2"})
    doc = _manifest_doc(payload, "agent", "0.1.8")
    root = tmp_path / "root"
    apply_manifest(doc, root, dry_run=False)
    vdir = root / "versions" / "agent" / "0.1.8"
    (vdir / "a").write_bytes(b"BAD")
    import eaide_update

    def boom(*a, **k):
        raise OSError("disk gone")

    monkeypatch.setattr(eaide_update.shutil, "copyfile", boom)
    with pytest.raises(OSError):
        apply_manifest(doc, root, dry_run=False)
    junk = [
        p for p in vdir.rglob("*") if p.is_file() and p.name not in ("a", "b", ".filehash.json")
    ]
    assert junk == []
    assert not (vdir / ".fetch").exists()


# ---- Task 4：复用 / 清扫 / 进程锁 ----


def test_reuse_from_factory_hardlink_or_copy(tmp_path, monkeypatch):
    """factory 根（安装目录平铺 eaide-agent.exe + _internal/）未变文件不下载，本地复用。"""
    import eaide_update

    install = tmp_path / "install"
    install.mkdir()
    (install / "eaide-agent.exe").write_bytes(b"A")
    (install / "_internal").mkdir()
    (install / "_internal" / "x").write_bytes(b"1")
    payload = _mk_payload(tmp_path, "src", {"eaide-agent.exe": b"A", "_internal/x": b"2"})
    doc = _manifest_doc(payload, "agent", "0.1.8")

    fetched = []
    real_fetch = eaide_update._fetch

    def spy_fetch(base, rel, dest):
        fetched.append(rel)
        return real_fetch(base, rel, dest)

    monkeypatch.setattr(eaide_update, "_fetch", spy_fetch)
    apply_manifest(doc, install, dry_run=False)
    got = install / "versions" / "agent" / "0.1.8"
    assert (got / "eaide-agent.exe").read_bytes() == b"A"
    assert (got / "_internal" / "x").read_bytes() == b"2"
    assert fetched == ["_internal/x"]  # 只有变化文件走取数，未变文件本地复用
    if sys.platform != "win32":
        # 硬链真实生效（而非退 copy）：同 inode（Win 跨盘/权限场景不断言）
        assert (install / "eaide-agent.exe").stat().st_ino == (
            got / "eaide-agent.exe"
        ).stat().st_ino


def test_staging_cleanup_removes_old_junk(tmp_path):
    vroot = tmp_path / "versions"
    junk = vroot / ".staging" / "agent-0.1.7-999"
    junk.mkdir(parents=True)
    old = os.path.getmtime(__file__) - 40 * 86400
    os.utime(junk, (old, old))
    fresh = vroot / ".staging" / "agent-0.1.8-998"
    fresh.mkdir()
    cleanup_staging(vroot, max_age_s=24 * 3600)
    assert not junk.exists()  # 超龄清扫
    assert fresh.exists()  # 新暂存不动


def test_agent_running_blocks_activation_on_windows(tmp_path, monkeypatch):
    import eaide_update

    monkeypatch.setattr(eaide_update, "agent_process_running", lambda: True)
    p1 = _mk_payload(tmp_path, "s1", {"a": b"1"})
    p2 = _mk_payload(tmp_path, "s2", {"a": b"2"})
    root = tmp_path / "root"
    monkeypatch.setattr(eaide_update, "IS_WIN", False)  # 首次安装不拦
    apply_manifest(_manifest_doc(p1, "agent", "0.1.7"), root, dry_run=False)
    monkeypatch.setattr(eaide_update, "IS_WIN", True)
    with pytest.raises(SystemExit, match="退出 EAIDE"):
        apply_manifest(_manifest_doc(p2, "agent", "0.1.8"), root, dry_run=False)
    assert read_active(root / "versions" / "agent-active.txt", FACTORY) == "0.1.7"


def test_agent_running_blocks_rollback_on_windows(tmp_path, monkeypatch):
    """spec §4.4：rollback 与 apply 同等的进程锁检查。"""
    import eaide_update

    p1 = _mk_payload(tmp_path, "s1", {"a": b"1"})
    p2 = _mk_payload(tmp_path, "s2", {"a": b"2"})
    root = tmp_path / "root"
    monkeypatch.setattr(eaide_update, "IS_WIN", False)
    apply_manifest(_manifest_doc(p1, "agent", "0.1.7"), root, dry_run=False)
    apply_manifest(_manifest_doc(p2, "agent", "0.1.8"), root, dry_run=False)
    monkeypatch.setattr(eaide_update, "IS_WIN", True)
    monkeypatch.setattr(eaide_update, "agent_process_running", lambda: True)
    with pytest.raises(SystemExit, match="退出 EAIDE"):
        rollback(root, "agent")
    assert read_active(root / "versions" / "agent-active.txt", FACTORY) == "0.1.8"


def test_running_match_is_case_insensitive(tmp_path, monkeypatch):
    import eaide_update

    monkeypatch.setattr(
        eaide_update.subprocess,
        "run",
        lambda *a, **k: type("R", (), {"stdout": "EAIDE-AGENT.EXE    1234 Console"})(),
    )
    monkeypatch.setattr(eaide_update, "IS_WIN", True)
    assert eaide_update.agent_process_running()  # 大写镜像名不得漏检（#1）


def test_cleanup_staging_keeps_active_deep_download(tmp_path):
    """顶层 mtime 冻结但深层仍在写的暂存不得误清（#I-3）。"""
    vroot = tmp_path / "versions"
    deep = vroot / ".staging" / "agent-0.1.8-1"
    deep.mkdir(parents=True)
    leaf = deep / "_internal" / "agent"
    leaf.mkdir(parents=True)
    (leaf / "f.py").write_bytes(b"x")
    old = os.path.getmtime(__file__) - 40 * 86400
    for d in (deep, deep / "_internal"):
        os.utime(d, (old, old))
    # 叶子 f.py 保持新鲜 mtime → 整棵暂存视为在用
    cleanup_staging(vroot, max_age_s=24 * 3600)
    assert deep.exists()


def test_cli_reject_adopt_with_reuse_from(tmp_path):
    import eaide_update

    with pytest.raises(SystemExit):
        eaide_update.main(
            [
                "apply",
                "--manifest",
                "x.json",
                "--root",
                str(tmp_path),
                "--adopt",
                str(tmp_path / "a"),
                "--reuse-from",
                str(tmp_path / "b"),
            ]
        )


def test_apply_unwritable_root_friendly_error(tmp_path):
    if sys.platform == "win32":
        pytest.skip("Windows chmod 仅切只读位，同用户仍可写，无法可靠模拟不可写")
    if hasattr(os, "geteuid") and os.geteuid() == 0:
        pytest.skip("root 下 chmod 拦不住")
    payload = _mk_payload(tmp_path, "src", {"a": b"1"})
    root = tmp_path / "ro-root"
    root.mkdir()
    vroot = root / "versions"
    vroot.mkdir()
    vroot.chmod(0o500)  # 只读
    try:
        with pytest.raises(SystemExit, match="EAIDE_VERSIONS_DIR"):
            apply_manifest(_manifest_doc(payload, "agent", "0.1.8"), root, dry_run=False)
    finally:
        vroot.chmod(0o700)
