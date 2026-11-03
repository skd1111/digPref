"""eaide_update.py 应用器测试（spec: docs/superpowers/specs/2026-11-03-agent-web-incremental-update-design.md §3/§4）。"""
import hashlib
import json

import pytest
from eaide_update import Manifest, ManifestError, gen_manifest


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


@pytest.mark.parametrize("evil", ["../x", "/abs", "C:\\win", "_internal/../../etc/passwd"])
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
