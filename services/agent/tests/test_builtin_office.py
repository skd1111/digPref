"""Phase 1B V9 · office 工具族测试（OfficeCLI 包装，2026-08-25）。

无二进制的用例（注册 / 沙箱 / 降级 / 参数校验）全部必过（CI 友好）；
子进程行为用 monkeypatch 假执行体模拟，不依赖真实 OfficeCLI 二进制。
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest


@pytest.fixture()
def office_file(tmp_path: Path) -> Path:
    """一个占位 .docx（内容不重要，仅校验路径 / 后缀逻辑）。"""
    p = tmp_path / "report.docx"
    p.write_bytes(b"PK\x03\x04 placeholder")
    return p


def _deny_officecli(monkeypatch: pytest.MonkeyPatch) -> None:
    """模拟 OfficeCLI 未安装（三级回退全部落空）。"""
    import agent.builtin.officecli_runtime as rt

    monkeypatch.setattr(rt, "resolve_officecli_exe", lambda: None)


class TestRegistration:
    """注册三要素（名单 / schema / 描述 / 风险等级），无外部依赖。"""

    def test_names_in_builtin_tool_names(self):
        from agent.builtin.models import BUILTIN_TOOL_NAMES

        for name in ("office_read", "office_edit", "office_create", "office_validate"):
            assert name in BUILTIN_TOOL_NAMES, name

    def test_schemas_defined(self):
        from agent.builtin.schemas import get_builtin_schema

        assert get_builtin_schema("office_read")["required"] == ["path"]
        assert get_builtin_schema("office_edit")["required"] == ["path", "op"]
        assert get_builtin_schema("office_create")["required"] == ["path"]
        assert get_builtin_schema("office_validate")["required"] == ["path"]

    def test_risk_levels_write_ops_are_medium(self):
        from agent.builtin.registry import TOOL_RISK_LEVEL

        assert TOOL_RISK_LEVEL["office_read"] == "read"
        assert TOOL_RISK_LEVEL["office_validate"] == "read"
        # 红线：写操作必须 medium 及以上 → 强制 HITL
        assert TOOL_RISK_LEVEL["office_edit"] in ("medium", "high", "critical")
        assert TOOL_RISK_LEVEL["office_create"] in ("medium", "high", "critical")

    def test_registry_has_all_tools(self):
        from agent.builtin.registry import get_default_registry

        reg = get_default_registry()
        for name in ("office_read", "office_edit", "office_create", "office_validate"):
            assert reg.has(name) and callable(reg.get(name)), name


class TestInputValidation:
    """参数 / 沙箱校验（不触达 OfficeCLI）。"""

    def test_read_rejects_non_office_suffix(self, tmp_path: Path):
        from agent.builtin.office import builtin_office_read

        txt = tmp_path / "note.txt"
        txt.write_text("hi", encoding="utf-8")
        result = builtin_office_read(path=str(txt))
        assert not result.ok
        assert result.error == "not_an_office_file"

    def test_read_rejects_invalid_action(self, office_file: Path):
        from agent.builtin.office import builtin_office_read

        result = builtin_office_read(path=str(office_file), action="bogus")
        assert not result.ok and result.error == "invalid_action"

    def test_read_query_requires_selector(self, office_file: Path):
        from agent.builtin.office import builtin_office_read

        result = builtin_office_read(path=str(office_file), action="query")
        assert not result.ok and result.error == "missing_query"

    def test_edit_rejects_invalid_op(self, office_file: Path):
        from agent.builtin.office import builtin_office_edit

        result = builtin_office_edit(path=str(office_file), op="destroy")
        assert not result.ok and result.error == "invalid_op"

    def test_edit_set_requires_props(self, office_file: Path):
        from agent.builtin.office import builtin_office_edit

        result = builtin_office_edit(path=str(office_file), op="set")
        assert not result.ok and result.error == "empty_props"

    def test_edit_add_requires_type(self, office_file: Path):
        from agent.builtin.office import builtin_office_edit

        result = builtin_office_edit(path=str(office_file), op="add")
        assert not result.ok and result.error == "missing_type"

    def test_edit_move_requires_to_parent(self, office_file: Path):
        from agent.builtin.office import builtin_office_edit

        result = builtin_office_edit(path=str(office_file), op="move")
        assert not result.ok and result.error == "missing_to_parent"

    def test_edit_batch_requires_commands(self, office_file: Path):
        from agent.builtin.office import builtin_office_edit

        result = builtin_office_edit(path=str(office_file), op="batch")
        assert not result.ok and result.error == "empty_commands"

    def test_create_rejects_unsupported_format(self, tmp_path: Path):
        from agent.builtin.office import builtin_office_create

        result = builtin_office_create(path=str(tmp_path / "x.doc"))
        assert not result.ok and result.error == "unsupported_format"

    def test_create_refuses_overwrite_by_default(self, office_file: Path):
        from agent.builtin.office import builtin_office_create

        result = builtin_office_create(path=str(office_file))
        assert not result.ok and result.error == "exists_no_overwrite"

    def test_create_merge_requires_data(self, office_file: Path, tmp_path: Path):
        from agent.builtin.office import builtin_office_create

        result = builtin_office_create(
            path=str(tmp_path / "out.docx"), template=str(office_file)
        )
        assert not result.ok and result.error == "empty_data"


class TestGracefulDegradation:
    """OfficeCLI 未安装时返友好错误，不崩溃。"""

    def test_read_not_installed(self, monkeypatch, office_file: Path):
        from agent.builtin.office import builtin_office_read

        _deny_officecli(monkeypatch)
        result = builtin_office_read(path=str(office_file))
        assert not result.ok and result.error == "officecli_not_installed"
        assert result.hint  # 有修复建议

    def test_edit_not_installed(self, monkeypatch, office_file: Path):
        from agent.builtin.office import builtin_office_edit

        _deny_officecli(monkeypatch)
        result = builtin_office_edit(
            path=str(office_file), op="set", props={"text": "x"}
        )
        assert not result.ok and result.error == "officecli_not_installed"
        assert result.risk_level == "medium"

    def test_create_not_installed(self, monkeypatch, tmp_path: Path):
        from agent.builtin.office import builtin_office_create

        _deny_officecli(monkeypatch)
        result = builtin_office_create(path=str(tmp_path / "new.pptx"))
        assert not result.ok and result.error == "officecli_not_installed"

    def test_validate_not_installed(self, monkeypatch, office_file: Path):
        from agent.builtin.office import builtin_office_validate

        _deny_officecli(monkeypatch)
        result = builtin_office_validate(path=str(office_file))
        assert not result.ok and result.error == "officecli_not_installed"


class TestRuntime:
    """officecli_runtime：定位回退 + 子进程输出解析。"""

    def test_resolve_prefers_explicit_override(self, monkeypatch, tmp_path: Path):
        import agent.builtin.officecli_runtime as rt
        from agent.config import settings

        fake = tmp_path / "officecli.exe"
        fake.write_bytes(b"MZ")
        monkeypatch.setattr(settings, "builtin_officecli_executable", str(fake))
        assert rt.resolve_officecli_exe() == str(fake)

    def test_resolve_falls_back_to_bundled(self, monkeypatch, tmp_path: Path):
        import agent.builtin.officecli_runtime as rt
        from agent.config import settings

        monkeypatch.setattr(settings, "builtin_officecli_executable", "")
        name = rt._PLATFORM_BINARIES[sys.platform][0]
        bundled = tmp_path / "vendor" / "officecli" / name
        bundled.parent.mkdir(parents=True)
        bundled.write_bytes(b"MZ")
        monkeypatch.setattr(rt, "_bundled_candidates", lambda: [bundled])
        monkeypatch.setattr(rt.shutil, "which", lambda _: None)
        assert rt.resolve_officecli_exe() == str(bundled)

    def test_resolve_returns_none_when_all_missing(self, monkeypatch):
        import agent.builtin.officecli_runtime as rt
        from agent.config import settings

        monkeypatch.setattr(settings, "builtin_officecli_executable", "")
        monkeypatch.setattr(rt, "_bundled_candidates", lambda: [])
        monkeypatch.setattr(rt.shutil, "which", lambda _: None)
        assert rt.resolve_officecli_exe() is None

    def test_bundled_candidates_include_meipass(self, monkeypatch, tmp_path: Path):
        import agent.builtin.officecli_runtime as rt

        meipass = tmp_path / "meipass"
        (meipass / "vendor" / "officecli").mkdir(parents=True)
        monkeypatch.setattr(sys, "_MEIPASS", str(meipass), raising=False)
        monkeypatch.chdir(tmp_path)  # 避开真实仓库根 / cwd 的干扰
        candidates = rt._bundled_candidates()
        assert any(str(c).startswith(str(meipass)) for c in candidates)

    def test_run_returns_not_installed_outcome(self, monkeypatch):
        import agent.builtin.officecli_runtime as rt

        monkeypatch.setattr(rt, "resolve_officecli_exe", lambda: None)
        out = rt.run_officecli(["view", "a.docx", "outline"])
        assert not out.ok and out.error == "officecli_not_installed"

    def test_run_appends_json_flag_and_parses_success(self, monkeypatch):
        import agent.builtin.officecli_runtime as rt

        captured: list[list[str]] = []

        def fake_run(exe: str, cmd: list[str], timeout: float):
            captured.append(cmd)
            return subprocess.CompletedProcess(
                args=cmd,
                returncode=0,
                stdout=json.dumps({"success": True, "path": "/body/p[1]"}).encode(),
                stderr=b"",
            )

        monkeypatch.setattr(rt, "resolve_officecli_exe", lambda: "C:/fake.exe")
        monkeypatch.setattr(rt, "_run_impl", fake_run)
        out = rt.run_officecli(["get", "a.docx", "/body/p[1]"])
        assert out.ok and out.data == {"success": True, "path": "/body/p[1]"}
        assert captured[0][-1] == "--json"

    def test_run_parses_structured_error_for_self_healing(self, monkeypatch):
        import agent.builtin.officecli_runtime as rt

        payload = {
            "success": False,
            "error": {
                "error": "Slide 50 not found (total: 8)",
                "code": "not_found",
                "suggestion": "Valid Slide index range: 1-8",
            },
        }

        monkeypatch.setattr(rt, "resolve_officecli_exe", lambda: "C:/fake.exe")
        monkeypatch.setattr(
            rt,
            "_run_impl",
            lambda exe, cmd, t: subprocess.CompletedProcess(
                args=cmd, returncode=1, stdout=json.dumps(payload).encode(), stderr=b""
            ),
        )
        out = rt.run_officecli(["get", "a.pptx", "/slide[50]"])
        assert not out.ok
        assert out.error == "not_found"
        assert out.suggestion and "1-8" in out.suggestion

    def test_run_timeout_maps_to_timed_out(self, monkeypatch):
        import agent.builtin.officecli_runtime as rt

        def raise_timeout(exe: str, cmd: list[str], timeout: float):
            raise subprocess.TimeoutExpired(cmd=cmd, timeout=timeout)

        monkeypatch.setattr(rt, "resolve_officecli_exe", lambda: "C:/fake.exe")
        monkeypatch.setattr(rt, "_run_impl", raise_timeout)
        out = rt.run_officecli(["view", "a.docx", "html"], timeout_sec=1)
        assert not out.ok and out.error == "timed_out"

    def test_child_env_blocks_auto_update_for_intranet(self):
        """内网红线：子进程环境必须带 OFFICECLI_SKIP_UPDATE=1。"""
        import agent.builtin.officecli_runtime as rt

        env = rt._child_env()
        assert env["OFFICECLI_SKIP_UPDATE"] == "1"
        assert env["PYTHONIOENCODING"] == "utf-8"


class TestEditAndCreateWithFakeBinary:
    """用假执行体验证 office_edit / office_create 的命令拼装与结果映射。"""

    @pytest.fixture(autouse=True)
    def _fake_officecli(self, monkeypatch: pytest.MonkeyPatch):
        import agent.builtin.officecli_runtime as rt

        self.captured: list[list[str]] = []

        def fake_run(exe: str, cmd: list[str], timeout: float):
            self.captured.append(cmd[1:])  # 去掉可执行文件本身，只留参数
            return subprocess.CompletedProcess(
                args=cmd,
                returncode=0,
                stdout=json.dumps({"success": True}).encode(),
                stderr=b"",
            )

        monkeypatch.setattr(rt, "resolve_officecli_exe", lambda: "C:/fake.exe")
        monkeypatch.setattr(rt, "_run_impl", fake_run)

    def test_edit_set_builds_prop_args(self, office_file: Path):
        from agent.builtin.office import builtin_office_edit

        result = builtin_office_edit(
            path=str(office_file),
            op="set",
            element_path="/body/p[1]",
            props={"text": "Hello", "bold": True},
        )
        assert result.ok and result.risk_level == "medium"
        cmd = self.captured[0]
        assert cmd[:2] == ["set", str(office_file)]
        assert "--prop" in cmd and "bold=true" in cmd and "text=Hello" in cmd

    def test_edit_batch_atomic(self, office_file: Path):
        from agent.builtin.office import builtin_office_edit

        result = builtin_office_edit(
            path=str(office_file),
            op="batch",
            commands=[{"command": "set", "path": "/body/p[1]", "props": {"bold": True}}],
        )
        assert result.ok
        cmd = self.captured[0]
        assert cmd[0] == "batch" and "--input" in cmd

    def test_create_blank_document(self, tmp_path: Path):
        from agent.builtin.office import builtin_office_create

        out = tmp_path / "new.xlsx"
        result = builtin_office_create(path=str(out))
        assert result.ok and result.content["path"] == str(out)
        assert self.captured[0][:2] == ["create", str(out)]

    def test_create_template_merge(self, office_file: Path, tmp_path: Path):
        from agent.builtin.office import builtin_office_create

        out = tmp_path / "filled.docx"
        result = builtin_office_create(
            path=str(out), template=str(office_file), data={"client": "Acme"}
        )
        assert result.ok and result.content["template"] == str(office_file)
        cmd = self.captured[0]
        assert cmd[:3] == ["merge", str(office_file), str(out)] and "--data" in cmd

    def test_validate_combines_schema_and_issues(self, office_file: Path):
        from agent.builtin.office import builtin_office_validate

        result = builtin_office_validate(path=str(office_file))
        assert result.ok and result.risk_level == "read"
        assert result.content["valid"] is True
        assert len(self.captured) == 2  # validate + view issues 两次调用


def _officecli_available() -> bool:
    from agent.builtin.officecli_runtime import resolve_officecli_exe

    return resolve_officecli_exe() is not None


needs_officecli = pytest.mark.skipif(
    not _officecli_available(),
    reason="OfficeCLI 二进制不可用（运行 infra/scripts/fetch-officecli.ps1）",
)


@needs_officecli
class TestRealBinary:
    """真实二进制端到端（本地 / 构建机有二进制时执行；CI 无二进制自动 skip）。"""

    def test_pptx_create_edit_read_validate_roundtrip(self, tmp_path: Path):
        import subprocess

        from agent.builtin.office import (
            builtin_office_create,
            builtin_office_edit,
            builtin_office_read,
            builtin_office_validate,
        )
        from agent.builtin.officecli_runtime import resolve_officecli_exe

        out = tmp_path / "e2e.pptx"
        r = builtin_office_create(path=str(out))
        assert r.ok, r.hint
        assert out.is_file()

        r = builtin_office_edit(
            path=str(out), op="add", element_path="/", type="slide", props={"title": "Q4 报告"}
        )
        assert r.ok, r.hint

        r = builtin_office_read(path=str(out), action="outline")
        assert r.ok, r.hint
        assert "Q4 报告" in (r.content or "")

        v = builtin_office_validate(path=str(out))
        assert v.ok
        assert v.content["valid"] is True

        # 收尾：释放 OfficeCLI 驻留会话（避免后台进程持有临时文件）
        exe = resolve_officecli_exe()
        assert exe is not None
        subprocess.run([exe, "close", str(out)], capture_output=True, timeout=30, check=False)

    def test_docx_template_merge(self, tmp_path: Path):
        import subprocess

        from agent.builtin.office import builtin_office_create, builtin_office_edit
        from agent.builtin.officecli_runtime import resolve_officecli_exe

        tpl = tmp_path / "tpl.docx"
        r = builtin_office_create(path=str(tpl))
        assert r.ok, r.hint
        r = builtin_office_edit(
            path=str(tpl),
            op="add",
            element_path="/body",
            type="paragraph",
            props={"text": "客户：{{client}}"},
        )
        assert r.ok, r.hint

        out = tmp_path / "filled.docx"
        r = builtin_office_create(path=str(out), template=str(tpl), data={"client": "Acme 集团"})
        assert r.ok, r.hint
        assert out.is_file()

        exe = resolve_officecli_exe()
        assert exe is not None
        for f in (tpl, out):
            subprocess.run([exe, "close", str(f)], capture_output=True, timeout=30, check=False)
