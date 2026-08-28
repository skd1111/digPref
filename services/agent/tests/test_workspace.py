"""工作空间（workspace）配置 + 文件落盘底层规则回归（用户要求 2026-08-17）。

覆盖：
  - workspace_dir() 默认 = 数据根/workspace（生产即安装目录/workspace）
  - 优先级：EAIDE_WORKSPACE_DIR > workspace.json 自定义 > 默认
  - resolve_output_path：纯文件名自动分类建目录；相对子目录保留在工作空间内；
    用户显式指定绝对路径时尊重用户
  - dispatcher._apply_workspace_rule：创建类工具输出路径在调度入口统一改写
  - /workspace GET/POST 端点（保存自定义 / 空串恢复默认 / UNC 拒绝）
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from agent import paths as ws_paths
from agent.builtin.dispatcher import _apply_workspace_rule


@pytest.fixture(autouse=True)
def _ws_env(monkeypatch, tmp_path):
    """干净的工作空间环境：数据根落 tmp，清掉可能的注入残留。"""
    monkeypatch.setenv("EAIDE_DATA_ROOT", str(tmp_path / "root"))
    monkeypatch.delenv("EAIDE_WORKSPACE_DIR", raising=False)
    monkeypatch.delenv("EAIDE_CONFIG_DIR", raising=False)
    return tmp_path


class TestWorkspaceDir:
    def test_default_is_data_root_workspace(self, _ws_env):
        ws = ws_paths.workspace_dir()
        assert ws == (_ws_env / "root" / "workspace").resolve()
        assert ws.is_dir()  # 自动建目录

    def test_env_var_override_wins(self, _ws_env, monkeypatch):
        custom = _ws_env / "elsewhere"
        monkeypatch.setenv("EAIDE_WORKSPACE_DIR", str(custom))
        assert ws_paths.workspace_dir() == custom.resolve()

    def test_saved_override_beats_default(self, _ws_env):
        custom = _ws_env / "my-ws"
        ws_paths.save_workspace_override(str(custom))
        assert ws_paths.workspace_dir() == custom.resolve()
        assert ws_paths.load_workspace_override() == str(custom)

    def test_env_var_beats_saved_override(self, _ws_env, monkeypatch):
        ws_paths.save_workspace_override(str(_ws_env / "saved"))
        monkeypatch.setenv("EAIDE_WORKSPACE_DIR", str(_ws_env / "envws"))
        assert ws_paths.workspace_dir() == (_ws_env / "envws").resolve()

    def test_clear_override_restores_default(self, _ws_env):
        ws_paths.save_workspace_override(str(_ws_env / "my-ws"))
        ws_paths.save_workspace_override(None)
        assert ws_paths.load_workspace_override() is None
        assert ws_paths.workspace_dir() == (_ws_env / "root" / "workspace").resolve()

    def test_corrupt_config_falls_back_to_default(self, _ws_env):
        Path("workspace.json").write_text("{not json", encoding="utf-8")
        assert ws_paths.load_workspace_override() is None
        assert ws_paths.workspace_dir() == (_ws_env / "root" / "workspace").resolve()


class TestResolveOutputPath:
    def test_bare_filename_classified_into_docs(self, _ws_env):
        p = ws_paths.resolve_output_path("报告.docx")
        ws = (_ws_env / "root" / "workspace").resolve()
        assert p == ws / "docs" / "报告.docx"
        assert p.parent.is_dir()

    def test_classification_by_extension(self, _ws_env):
        ws = (_ws_env / "root" / "workspace").resolve()
        assert ws_paths.resolve_output_path("a.png") == ws / "images" / "a.png"
        assert ws_paths.resolve_output_path("b.csv") == ws / "data" / "b.csv"
        assert ws_paths.resolve_output_path("c.md") == ws / "docs" / "c.md"
        assert ws_paths.resolve_output_path("d.xyz") == ws / "other" / "d.xyz"

    def test_relative_subpath_kept_inside_workspace(self, _ws_env):
        p = ws_paths.resolve_output_path("sub/a.txt")
        ws = (_ws_env / "root" / "workspace").resolve()
        assert p == ws / "sub" / "a.txt"
        assert p.parent.is_dir()

    def test_absolute_path_respected_as_user_specified(self, _ws_env):
        target = _ws_env / "user-dir" / "out.txt"
        p = ws_paths.resolve_output_path(str(target))
        assert p == target.resolve()
        assert p.parent.is_dir()  # 用户指定目录也自动建
        assert ws_paths.is_user_specified_output(str(target))

    def test_bare_filename_not_user_specified(self):
        assert not ws_paths.is_user_specified_output("报告.docx")


class TestDispatcherRule:
    def test_write_file_path_rewritten_into_workspace(self, _ws_env):
        args = {"path": "结果.txt", "content": "x"}
        out = _apply_workspace_rule("write_file", args)
        ws = (_ws_env / "root" / "workspace").resolve()
        assert out["path"] == str(ws / "docs" / "结果.txt")
        assert out["content"] == "x"
        # 原 args 不被就地修改
        assert args["path"] == "结果.txt"

    def test_user_mentioned_absolute_path_untouched(self, _ws_env):
        """2026-08-26 收紧：绝对路径只有在用户对话原文中出现过才算用户指定。"""
        target = str(_ws_env / "user" / "x.csv")
        state = {"user_prompt": f"把结果导出到 {target}", "messages": []}
        out = _apply_workspace_rule("excel_export", {"path": target}, state)
        assert out["path"] == str(Path(target).resolve())

    def test_model_fabricated_absolute_path_redirected(self, _ws_env):
        """模型自造的绝对路径（对话未提及）→ 收进工作空间，防散落用户目录。"""
        target = str(_ws_env / "user" / "x.csv")
        state = {"user_prompt": "导出近一周的数据", "messages": []}
        out = _apply_workspace_rule("excel_export", {"path": target}, state)
        ws = (_ws_env / "root" / "workspace").resolve()
        assert out["path"] == str(ws / "data" / "x.csv")

    def test_legacy_call_without_state_redirects_absolute(self, _ws_env):
        """无 state 的旧式调用同样收紧（绝对路径不在对话中 → 进工作空间）。"""
        target = str(_ws_env / "user" / "x.csv")
        out = _apply_workspace_rule("excel_export", {"path": target})
        ws = (_ws_env / "root" / "workspace").resolve()
        assert out["path"] == str(ws / "data" / "x.csv")

    def test_non_create_tool_untouched(self, _ws_env):
        args = {"path": "任意.txt"}
        assert _apply_workspace_rule("read_file", args) is args

    def test_pdf_merge_output_rewritten(self, _ws_env):
        out = _apply_workspace_rule("pdf_merge", {"inputs": [], "output": "merged.pdf"})
        ws = (_ws_env / "root" / "workspace").resolve()
        assert out["output"] == str(ws / "docs" / "merged.pdf")


class TestWorkspaceApi:
    async def test_get_returns_effective_and_default(self, _ws_env):
        from agent.api.workspace import get_workspace

        r = await get_workspace()
        assert r["path"] == str((_ws_env / "root" / "workspace").resolve())
        assert r["custom"] is None
        assert r["default"] == r["path"]

    async def test_save_custom_then_get(self, _ws_env):
        from agent.api.workspace import WorkspaceSaveRequest, get_workspace, save_workspace

        custom = _ws_env / "custom-ws"
        r = await save_workspace(WorkspaceSaveRequest(path=str(custom)))
        assert r["ok"] is True
        assert r["custom"] == str(custom.resolve())
        got = await get_workspace()
        assert got["path"] == str(custom.resolve())

    async def test_save_empty_restores_default(self, _ws_env):
        from agent.api.workspace import WorkspaceSaveRequest, save_workspace

        await save_workspace(WorkspaceSaveRequest(path=str(_ws_env / "custom-ws")))
        r = await save_workspace(WorkspaceSaveRequest(path=""))
        assert r["custom"] is None
        assert r["path"] == str((_ws_env / "root" / "workspace").resolve())

    async def test_save_unc_rejected(self, _ws_env):
        from agent.api.workspace import WorkspaceSaveRequest, save_workspace
        from fastapi import HTTPException

        with pytest.raises(HTTPException) as exc_info:
            await save_workspace(WorkspaceSaveRequest(path="\\\\server\\share"))
        assert exc_info.value.status_code == 400

    def test_config_persisted_as_single_json(self, _ws_env):
        ws_paths.save_workspace_override(str(_ws_env / "w"))
        data = json.loads(Path("workspace.json").read_text(encoding="utf-8"))
        assert data == {"path": str(_ws_env / "w")}
