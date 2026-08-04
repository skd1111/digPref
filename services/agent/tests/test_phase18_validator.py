"""Phase 18 分层验证器：L1 语法快检 / L2 项目验证命令 / L3 降级。"""
from __future__ import annotations

from agent.coding.validator import CodingValidator


def test_python_syntax_ok(tmp_path):
    f = tmp_path / "a.py"
    f.write_text("x = 1\n", encoding="utf-8")
    r = CodingValidator(project_root=tmp_path).validate([f])
    assert r.ok
    assert r.level in ("full", "syntax_only")


def test_python_syntax_error(tmp_path):
    f = tmp_path / "b.py"
    f.write_text("def broken(:\n", encoding="utf-8")
    r = CodingValidator(project_root=tmp_path).validate([f])
    assert not r.ok
    assert r.error


def test_json_syntax_check(tmp_path):
    good = tmp_path / "ok.json"
    good.write_text('{"a": 1}', encoding="utf-8")
    bad = tmp_path / "bad.json"
    bad.write_text('{"a": ', encoding="utf-8")
    v = CodingValidator(project_root=tmp_path)
    assert v.validate([good]).ok
    assert not v.validate([bad]).ok


def test_unknown_extension_skipped(tmp_path):
    f = tmp_path / "x.unknownext"
    f.write_text("whatever", encoding="utf-8")
    r = CodingValidator(project_root=tmp_path).validate([f])
    assert r.ok  # 无语法检查器 → 不阻断


def test_validate_command_from_config(tmp_path):
    cfg_dir = tmp_path / ".eaide" / "config"
    cfg_dir.mkdir(parents=True)
    (cfg_dir / "agent.yaml").write_text("validate_command: 'exit 1'\n", encoding="utf-8")
    r = CodingValidator(project_root=tmp_path).validate([])
    assert r.level == "full"
    assert not r.ok


def test_validate_command_success(tmp_path):
    cfg_dir = tmp_path / ".eaide" / "config"
    cfg_dir.mkdir(parents=True)
    (cfg_dir / "agent.yaml").write_text("validate_command: 'exit 0'\n", encoding="utf-8")
    r = CodingValidator(project_root=tmp_path).validate([])
    assert r.level == "full"
    assert r.ok


def test_validate_command_changed_files_placeholder(tmp_path):
    cfg_dir = tmp_path / ".eaide" / "config"
    cfg_dir.mkdir(parents=True)
    marker = tmp_path / "marker.txt"
    (cfg_dir / "agent.yaml").write_text(
        "validate_command: 'echo {changed_files} > marker.txt && exit 0'\n",
        encoding="utf-8",
    )
    target = tmp_path / "c.py"
    target.write_text("x = 1\n", encoding="utf-8")
    r = CodingValidator(project_root=tmp_path).validate([target])
    assert r.ok
    assert "c.py" in marker.read_text(encoding="utf-8")


def test_no_config_level_is_syntax_only(tmp_path):
    f = tmp_path / "a.py"
    f.write_text("x = 1\n", encoding="utf-8")
    r = CodingValidator(project_root=tmp_path).validate([f])
    assert r.level == "syntax_only"
