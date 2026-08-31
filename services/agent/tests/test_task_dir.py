"""任务级工作目录回归（用户要求 2026-08-26）。

覆盖：
  - task_dir：workspace/tasks/<时间戳>_<首问摘要>；同一 task_id 复用；空 id 返 None
  - resolve_output_path：task_root 落盘 + 模型自造绝对路径重定向（用户对话中出现过的豁免）
  - ledger_record / ledger_read：产物台账（清理的保留依据）
  - dispatcher：office_create 参与改写、任务目录落盘；office_edit 不改写
  - /workspace/tasks/{id} GET + cleanup POST：清单划分 / 保留产物 / 目录穿越防护
"""

from __future__ import annotations

import json

import pytest
from agent import paths as ws_paths
from agent.builtin.dispatcher import _apply_workspace_rule


@pytest.fixture(autouse=True)
def _ws_env(monkeypatch, tmp_path):
    monkeypatch.setenv("EAIDE_DATA_ROOT", str(tmp_path / "root"))
    monkeypatch.delenv("EAIDE_WORKSPACE_DIR", raising=False)
    monkeypatch.delenv("EAIDE_CONFIG_DIR", raising=False)
    return tmp_path


class TestTaskDir:
    def test_empty_task_id_returns_none(self, _ws_env):
        assert ws_paths.task_dir(None) is None
        assert ws_paths.task_dir("   ") is None

    def test_creates_task_folder_under_workspace_tasks(self, _ws_env):
        d = ws_paths.task_dir("tab-1", "做一个介绍 EAIDE 的 ppt")
        assert d is not None
        assert d.is_dir()
        ws = (_ws_env / "root" / "workspace").resolve()
        assert d.parent == ws / "tasks"
        # 目录名含首问摘要片段（非法字符已剔除）
        assert "做一个介绍" in d.name

    def test_same_task_id_reuses_folder(self, _ws_env):
        d1 = ws_paths.task_dir("tab-1", "首问")
        d2 = ws_paths.task_dir("tab-1", "别的标题也不新建")
        assert d1 == d2

    def test_different_task_id_gets_own_folder(self, _ws_env):
        d1 = ws_paths.task_dir("tab-1", "任务一")
        d2 = ws_paths.task_dir("tab-2", "任务二")
        assert d1 != d2

    def test_index_persisted_to_data_root(self, _ws_env):
        ws_paths.task_dir("tab-x", "标题")
        data = json.loads((_ws_env / "root" / "tasks-index.json").read_text(encoding="utf-8"))
        assert "tab-x" in data

    def test_slug_sanitizes_path_separators(self, _ws_env):
        d = ws_paths.task_dir("tab-evil", "../../etc/passwd")
        assert d is not None
        assert ".." not in d.name
        assert d.parent.name == "tasks"


class TestResolveWithTaskRoot:
    def test_bare_filename_goes_into_task_dir_classified(self, _ws_env):
        root = ws_paths.task_dir("tab-1", "ppt 任务")
        p = ws_paths.resolve_output_path("eaide_intro.pptx", task_root=root)
        assert p == root / "docs" / "eaide_intro.pptx"

    def test_model_fabricated_absolute_path_redirected(self, _ws_env):
        """模型自造的用户目录绝对路径（对话原文未出现）→ 收进任务目录。"""
        root = ws_paths.task_dir("tab-1", "ppt 任务")
        fabricated = str(_ws_env / "home" / "eaide_intro.pptx")
        p = ws_paths.resolve_output_path(
            fabricated, task_root=root, context_texts=("做一个介绍你的ppt",)
        )
        assert p == root / "docs" / "eaide_intro.pptx"

    def test_user_mentioned_absolute_path_respected(self, _ws_env):
        """路径在用户对话原文中出现过 → 视为用户指定，原样放行。"""
        root = ws_paths.task_dir("tab-1", "任务")
        target = _ws_env / "user" / "out.docx"
        p = ws_paths.resolve_output_path(
            str(target), task_root=root, context_texts=(f"请把结果写到 {target}",)
        )
        assert p == target.resolve()

    def test_no_context_keeps_legacy_absolute_passthrough(self, _ws_env):
        """未提供上下文（旧调用）→ 绝对路径维持旧行为原样放行。"""
        target = _ws_env / "legacy" / "a.txt"
        p = ws_paths.resolve_output_path(str(target))
        assert p == target.resolve()


class TestLedger:
    def test_record_and_read(self, _ws_env):
        ws_paths.ledger_record("tab-1", "/tmp/a.pptx", "artifact")
        ws_paths.ledger_record("tab-1", "/tmp/a.pptx", "artifact")  # 去重
        ws_paths.ledger_record("tab-1", "/tmp/build.log", "intermediate")
        got = ws_paths.ledger_read("tab-1")
        assert got["artifacts"] == [str(ws_paths.Path("/tmp/a.pptx").resolve(strict=False))]
        assert len(got["intermediates"]) == 1

    def test_read_unknown_task_empty(self, _ws_env):
        got = ws_paths.ledger_read("no-such-task")
        assert got == {"artifacts": [], "intermediates": []}

    def test_invalid_kind_ignored(self, _ws_env):
        ws_paths.ledger_record("tab-1", "/tmp/x", "weird")
        assert ws_paths.ledger_read("tab-1")["artifacts"] == []


class TestDispatcherTaskRule:
    def test_office_create_rewritten_into_task_dir(self, _ws_env):
        state = {
            "task_id": "tab-1",
            "task_title": "介绍 EAIDE",
            "user_prompt": "做一个介绍 EAIDE 的 ppt",
            "messages": [],
        }
        out = _apply_workspace_rule("office_create", {"path": "eaide_intro.pptx"}, state)
        root = ws_paths.task_dir("tab-1", "介绍 EAIDE")
        assert out["path"] == str(root / "docs" / "eaide_intro.pptx")

    def test_model_absolute_path_redirected_with_state(self, _ws_env):
        fabricated = str(_ws_env / "home" / "x.pptx")
        state = {"task_id": "tab-1", "task_title": "t", "user_prompt": "做个ppt", "messages": []}
        out = _apply_workspace_rule("office_create", {"path": fabricated}, state)
        root = ws_paths.task_dir("tab-1", "t")
        assert out["path"] == str(root / "docs" / "x.pptx")

    def test_office_edit_not_rewritten(self, _ws_env):
        """office_edit 指向已有文件，绝不能被改写进任务目录。"""
        state = {"task_id": "tab-1", "task_title": "t", "user_prompt": "改一下", "messages": []}
        args = {"path": str(_ws_env / "existing.pptx"), "op": "add"}
        assert _apply_workspace_rule("office_edit", args, state) is args


class TestTaskApi:
    async def _setup_task(self, _ws_env):
        root = ws_paths.task_dir("tab-api", "API 任务")
        docs = root / "docs"
        docs.mkdir(parents=True, exist_ok=True)
        artifact = docs / "report.pptx"
        artifact.write_text("x", encoding="utf-8")
        inter = root / "scratch.json"
        inter.write_text("{}", encoding="utf-8")
        ws_paths.ledger_record("tab-api", str(artifact), "artifact")
        return root, artifact, inter

    async def test_get_task_files_splits_artifacts(self, _ws_env):
        from agent.api.workspace import get_task_files

        root, artifact, inter = await self._setup_task(_ws_env)
        r = await get_task_files("tab-api")
        assert r["task_dir"] == str(root)
        assert r["artifacts"] == [str(artifact.resolve())]
        assert r["intermediates"] == [str(inter.resolve())]

    async def test_cleanup_removes_intermediates_keeps_artifacts(self, _ws_env):
        from agent.api.workspace import TaskCleanupRequest, cleanup_task_files

        _root, artifact, inter = await self._setup_task(_ws_env)
        r = await cleanup_task_files("tab-api", TaskCleanupRequest(keep=[]))
        assert r["ok"] is True
        assert str(inter.resolve()) in r["deleted"]
        assert str(artifact.resolve()) in r["kept"]
        assert artifact.exists()
        assert not inter.exists()

    async def test_cleanup_empty_task_dir_removed(self, _ws_env):
        """任务目录内只有中间文件 → 清理后连空目录一起删除。"""
        from agent.api.workspace import TaskCleanupRequest, cleanup_task_files

        root, artifact, _inter = await self._setup_task(_ws_env)
        artifact.unlink()  # 产物不在了（未记台账的普通文件也当中间文件）
        ws_paths.ledger_record("tab-api", "不存在的产物占位", "artifact")
        r = await cleanup_task_files("tab-api", TaskCleanupRequest(keep=[]))
        assert r["task_dir_removed"] is True
        assert not root.exists()

    async def test_cleanup_keeps_dir_when_artifacts_remain(self, _ws_env):
        from agent.api.workspace import TaskCleanupRequest, cleanup_task_files

        root, _artifact, _inter = await self._setup_task(_ws_env)
        ws_paths.ledger_record("tab-api", str(root / "scratch.json"), "artifact")
        r = await cleanup_task_files("tab-api", TaskCleanupRequest(keep=[]))
        # 两个文件都是产物 → 全保留，目录不清空不删
        assert r["task_dir_removed"] is False
        assert root.exists()

    async def test_unknown_task_dir_safe(self, _ws_env):
        from agent.api.workspace import TaskCleanupRequest, cleanup_task_files

        # 从未产生过文件的 task_id：目录不存在 → 安全返回
        r = await cleanup_task_files("never-used", TaskCleanupRequest(keep=[]))
        assert r["ok"] is True
        assert r["deleted"] == []

    async def test_empty_task_id_rejected(self, _ws_env):
        from agent.api.workspace import get_task_files
        from fastapi import HTTPException

        with pytest.raises(HTTPException) as exc_info:
            await get_task_files("   ")
        assert exc_info.value.status_code == 400


class TestContextAnchor:
    """任务台账锚点（治「又没接上上下文」）：此前交付的文件路径注入为 system 事实。"""

    def test_artifact_note_lists_paths(self, _ws_env):
        from agent.graph.stream import _task_artifact_note

        ws_paths.ledger_record("tab-c", str(_ws_env / "a.pptx"), "artifact")
        note = _task_artifact_note("tab-c")
        assert note is not None
        assert str(_ws_env / "a.pptx") in note
        assert "禁止反问看不到内容" in note

    def test_artifact_note_empty_without_task_or_artifacts(self, _ws_env):
        from agent.graph.stream import _task_artifact_note

        assert _task_artifact_note(None) is None
        assert _task_artifact_note("tab-no-artifacts") is None


def test_chat_request_accepts_new_fields():
    """ChatRequest 新透传字段（别名兼容前端 camelCase）。"""
    from agent.api.chat import ChatRequest

    body = ChatRequest.model_validate(
        {
            "prompt": "太丑了，优化一下",
            "lastSkillId": "office_pptx_designer",
            "pinnedSkillId": "office_doc_writer",
            "taskId": "tab-1",
            "taskTitle": "做个介绍 ppt",
        }
    )
    assert body.last_skill_id == "office_pptx_designer"
    assert body.pinned_skill_id == "office_doc_writer"  # `/` 指令强钉（2026-08-28）
    assert body.task_id == "tab-1"
    assert body.task_title == "做个介绍 ppt"
