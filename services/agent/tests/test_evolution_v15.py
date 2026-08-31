"""Phase 19 V1.5 回归测试：Few-shot 影子优化实验 + 版本采纳/回滚。

覆盖：
    - 候选 few-shot 解析（合法 / 围栏 / 非法 / 条目过滤）
    - 影子回放实验全链路（假 LLM：旧 3 分 → 新 5 分 → 显著增益产出候选版本）
    - 增益不显著时不产出版本
    - /evolution/prompt-versions apply（写回技能 YAML + 状态流转）与重复采纳 409
    - rollback 恢复上一版本（技能文件同步还原）
"""

from __future__ import annotations

import sqlite3

import pytest
import yaml
from agent.config import settings
from agent.evolution import events, prompt_opt, storage
from agent.evolution.api import router as evolution_router
from fastapi import FastAPI
from fastapi.testclient import TestClient

_SKILL_YAML = """
schema_version: "1.0"
id: daily_report_check
name: 日报核对规范
description: 生成日报前先核对日期范围
enabled: true
trigger_keywords:
  - 日报
system_prompt: |
  生成日报前必须先确认日期范围。
few_shot_examples:
  - role: user
    content: 旧示例请求
  - role: assistant
    content: 旧示例回答
"""

_CANDIDATE_JSON = (
    '{"few_shot": [{"role": "user", "content": "新示例请求"},'
    ' {"role": "assistant", "content": "新示例回答"}]}'
)


# ---- 解析与拼装 -------------------------------------------------------------


class TestParse:
    def test_parse_valid(self):
        out = prompt_opt.parse_candidate_few_shot(_CANDIDATE_JSON)
        assert out is not None and len(out) == 2
        assert out[0]["role"] == "user" and out[0]["content"] == "新示例请求"

    def test_parse_fenced(self):
        out = prompt_opt.parse_candidate_few_shot(f"```json\n{_CANDIDATE_JSON}\n```")
        assert out is not None

    def test_parse_invalid(self):
        assert prompt_opt.parse_candidate_few_shot("没有任何 JSON") is None
        assert prompt_opt.parse_candidate_few_shot('{"few_shot": []}') is None
        assert prompt_opt.parse_candidate_few_shot('{"few_shot": [{"role": "x"}]}') is None

    def test_format_block_skips_bad_entries(self):
        block = prompt_opt.format_few_shot_block(
            [
                {"role": "user", "content": "问题"},
                {"role": "tool", "content": "非法角色"},
                {"role": "assistant", "content": ""},
            ]
        )
        assert "[用户] 问题" in block
        assert "非法角色" not in block


# ---- 影子实验全链路 ----------------------------------------------------------


class _ScriptedRouter:
    """按调用次序应答：候选生成 → 旧版回放打分(3) → 新版回放打分(5)。

    旧版全部回放完成后才开始新版回放，故按 judge 调用次数对半分即可。
    注意：每次 `LMRouter()` 都是新实例，计数用类属性共享。
    """

    judge_calls: int = 0

    async def route(self, *, task: str, prompt: str) -> str:
        if task == "answer_judge":
            type(self).judge_calls += 1
            # 前 2 次评分属旧版（3 分），后续属新版（5 分）
            return '{"score": 3}' if type(self).judge_calls <= 2 else '{"score": 5}'
        if "提示词优化" in prompt:
            return _CANDIDATE_JSON
        # 影子回放草稿生成（prompt 含「示例：」标记）
        return "草稿回答"


@pytest.fixture
def skill_env(tmp_path, monkeypatch):
    """临时技能目录 + 装载器 + 一条可回放轨迹。"""
    from agent.skills.loader import SkillLoader

    loader = SkillLoader(tmp_path / "skills")
    (tmp_path / "skills").mkdir(parents=True, exist_ok=True)
    (tmp_path / "skills" / "daily_report_check.yaml").write_text(_SKILL_YAML, encoding="utf-8")
    loader.load_all()
    monkeypatch.setattr("agent.skills.api._loader", loader)
    _ScriptedRouter.judge_calls = 0
    monkeypatch.setattr("agent.llm.router.LMRouter", _ScriptedRouter)
    return tmp_path


class TestExperiment:
    @pytest.mark.asyncio
    async def test_full_experiment_significant_gain(self, skill_env, monkeypatch):
        events.flush_evolution_events()
        monkeypatch.setattr(settings, "prompt_optimize_gain_threshold", 0.5)
        # 造一条可回放轨迹（含改写句）
        await storage.record_trajectory(
            session_id="run-p",
            task_signature="sig-p",
            intent={"rewritten_query": "帮我出昨天的日报"},
            active_skill_id="daily_report_check",
            tool_fp="",
            outcome="fail",
            answer_digest="日期取错",
        )
        result = await prompt_opt.run_prompt_experiment(
            skill_id="daily_report_check", task_signature="sig-p"
        )
        assert result["old_avg"] == 3.0 and result["new_avg"] == 5.0
        assert result["gain"] == 2.0 and result["significant"] is True
        assert result["version_id"] is not None
        assert result["auto_adopted"] is False  # 默认人工确认
        # 候选版本已落库
        versions = await storage.list_prompt_versions("daily_report_check")
        assert len(versions) == 1
        assert versions[0]["status"] == "candidate"
        assert versions[0]["few_shot"][0]["content"] == "新示例请求"
        # 事件已入队
        queued = await events.consume_evolution_events()
        kinds = [k for k, _ in queued]
        assert prompt_opt.EVT_EVOLUTION_EXPERIMENT_DONE in kinds

    @pytest.mark.asyncio
    async def test_insignificant_gain_no_version(self, skill_env, monkeypatch):
        """门槛高于实际增益 → 不产出候选版本。"""
        monkeypatch.setattr(settings, "prompt_optimize_gain_threshold", 2.5)
        await storage.record_trajectory(
            session_id="run-q",
            task_signature="sig-q",
            intent={"rewritten_query": "查询请求"},
            active_skill_id="daily_report_check",
            tool_fp="",
            outcome="fail",
            answer_digest="x",
        )
        result = await prompt_opt.run_prompt_experiment(
            skill_id="daily_report_check", task_signature="sig-q"
        )
        assert result["gain"] == 2.0 and result["significant"] is False
        assert result["version_id"] is None
        assert await storage.list_prompt_versions("daily_report_check") == []

    @pytest.mark.asyncio
    async def test_no_replay_material_raises(self, skill_env):
        with pytest.raises(ValueError, match="无可回放"):
            await prompt_opt.run_prompt_experiment(skill_id="daily_report_check")

    @pytest.mark.asyncio
    async def test_auto_adopt_demotes_previous_active(self, skill_env, monkeypatch):
        """自动采纳也要保证同 skill 单 active：旧 active 降级为 rolled_back。"""
        monkeypatch.setattr(settings, "prompt_optimize_gain_threshold", 0.5)
        monkeypatch.setattr(settings, "evolution_prompt_auto_adopt", True)
        old_id = _seed_version(
            "daily_report_check",
            1,
            '[{"role": "user", "content": "旧"}, {"role": "assistant", "content": "旧答"}]',
            "active",
        )
        await storage.record_trajectory(
            session_id="run-auto",
            task_signature="sig-auto",
            intent={"rewritten_query": "帮我出昨天的日报"},
            active_skill_id="daily_report_check",
            tool_fp="",
            outcome="fail",
            answer_digest="x",
        )
        result = await prompt_opt.run_prompt_experiment(
            skill_id="daily_report_check", task_signature="sig-auto"
        )
        assert result["auto_adopted"] is True and result["version_id"] is not None
        status_by_id = {
            it["id"]: it["status"]
            for it in await storage.list_prompt_versions("daily_report_check")
        }
        assert status_by_id[old_id] == "rolled_back"
        assert status_by_id[result["version_id"]] == "active"
        # 技能 YAML 已写回新版 few-shot（自动采纳生效）
        saved = yaml.safe_load(
            (skill_env / "skills" / "daily_report_check.yaml").read_text(encoding="utf-8")
        )
        assert saved["few_shot_examples"][0]["content"] == "新示例请求"

    @pytest.mark.asyncio
    async def test_unknown_skill_raises(self, skill_env):
        with pytest.raises(ValueError, match="not found"):
            await prompt_opt.run_prompt_experiment(skill_id="ghost_skill")


# ---- API：版本采纳与回滚 -----------------------------------------------------


@pytest.fixture
def client(monkeypatch):
    async def _fake_experiment(*, skill_id, task_signature=""):
        return {
            "skill_id": skill_id,
            "old_avg": 3.0,
            "new_avg": 5.0,
            "gain": 2.0,
            "significant": True,
            "version_id": 1,
            "auto_adopted": False,
        }

    monkeypatch.setattr("agent.evolution.prompt_opt.run_prompt_experiment", _fake_experiment)
    app = FastAPI()
    app.include_router(evolution_router)
    return TestClient(app)


def _seed_version(skill_id: str, version: int, few_shot_text: str, status: str) -> int:
    conn = sqlite3.connect(settings.evolution_db_path)
    try:
        conn.executescript(storage.SCHEMA_CREATE_TABLES)
        cur = conn.execute(
            "INSERT INTO prompt_versions (skill_id, version, few_shot_json, gain, status, ts)"
            " VALUES (?, ?, ?, ?, ?, ?)",
            (skill_id, version, few_shot_text, 1.0, status, "2026-08-31T00:00:00"),
        )
        version_id = cur.lastrowid
        conn.commit()
    finally:
        conn.close()
    return int(version_id or 0)


class TestVersionApi:
    def test_run_experiment_endpoint(self, client):
        resp = client.post(
            "/evolution/prompt-optimization/run", json={"skillId": "daily_report_check"}
        )
        assert resp.status_code == 200
        assert resp.json()["gain"] == 2.0

    def test_apply_writes_skill_few_shot(self, client, skill_env):
        vid = _seed_version(
            "daily_report_check",
            1,
            '[{"role": "user", "content": "V1 请求"}, {"role": "assistant", "content": "V1 回答"}]',
            "candidate",
        )
        resp = client.post(f"/evolution/prompt-versions/{vid}/apply")
        assert resp.status_code == 200 and resp.json()["status"] == "active"
        # 技能 YAML 的 few-shot 已替换
        saved = yaml.safe_load(
            (skill_env / "skills" / "daily_report_check.yaml").read_text(encoding="utf-8")
        )
        assert saved["few_shot_examples"][0]["content"] == "V1 请求"
        # 重复采纳被拒
        assert client.post(f"/evolution/prompt-versions/{vid}/apply").status_code == 409

    def test_rollback_restores_previous(self, client, skill_env):
        v1 = _seed_version(
            "daily_report_check",
            1,
            '[{"role": "user", "content": "V1 请求"}, {"role": "assistant", "content": "V1 回答"}]',
            "rolled_back",
        )
        v2 = _seed_version(
            "daily_report_check",
            2,
            '[{"role": "user", "content": "V2 请求"}, {"role": "assistant", "content": "V2 回答"}]',
            "active",
        )
        resp = client.post(f"/evolution/prompt-versions/{v2}/rollback")
        assert resp.status_code == 200
        body = resp.json()
        assert body["rolled_back_to"] == 1
        # 技能文件还原为 V1 示例；V1 状态回 active
        saved = yaml.safe_load(
            (skill_env / "skills" / "daily_report_check.yaml").read_text(encoding="utf-8")
        )
        assert saved["few_shot_examples"][0]["content"] == "V1 请求"
        versions = client.get("/evolution/prompt-versions").json()["items"]
        status_by_id = {it["id"]: it["status"] for it in versions}
        assert status_by_id[v1] == "active" and status_by_id[v2] == "rolled_back"

    def test_rollback_requires_active(self, client, skill_env):
        vid = _seed_version("daily_report_check", 1, "[]", "candidate")
        assert client.post(f"/evolution/prompt-versions/{vid}/rollback").status_code == 409

    def test_missing_version_404(self, client):
        assert client.post("/evolution/prompt-versions/9999/apply").status_code == 404
        assert client.post("/evolution/prompt-versions/9999/rollback").status_code == 404
