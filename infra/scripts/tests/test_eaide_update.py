"""eaide_update.py 应用器测试（spec: docs/superpowers/specs/2026-11-03-agent-web-incremental-update-design.md §3/§4）。"""

import hashlib
import json

import pytest
from eaide_update import Manifest, ManifestError, _to_local_path, gen_manifest


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
