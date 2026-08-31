"""Phase 19 V1 自进化回归测试：技能蒸馏 + 主对话 Judge + 草稿审核 API + 看板。

覆盖：
    - 蒸馏输出解析（合法 / 围栏 / schema 不过 / 含 DSN → 拒绝）
    - 蒸馏全链路（假 LLM → 草稿落库 + 事件；强制 enabled: false）
    - 蒸馏触发条件（门槛 / Skill 覆盖 / 草稿去重）
    - Judge 确定性抽样 + 打分落信号表
    - 轨迹钩子成功路径触发 Judge 与蒸馏
    - /evolution/skill-drafts 审核 API（approve 写技能文件 / reject / 冲突）
    - /evolution/stats 看板统计
"""

from __future__ import annotations

import sqlite3

import pytest
import yaml
from agent.config import settings
from agent.evolution import events, judge, skill_distiller, storage, trajectory
from agent.evolution.api import router as evolution_router
from fastapi import FastAPI
from fastapi.testclient import TestClient

_VALID_SKILL_YAML = """
schema_version: "1.0"
id: daily_report_check
name: 日报核对规范
description: 生成日报前先核对日期范围与数据源
trigger_keywords:
  - 日报
  - 核对
system_prompt: |
  生成日报前必须先确认日期范围。
few_shot_examples:
  - role: user
    content: 帮我出昨天的日报
  - role: assistant
    content: 先确认日期范围为昨天，再查询数据源。
"""


# ---- 蒸馏输出解析 -----------------------------------------------------------


class TestParseSkillYaml:
    def test_valid_yaml_forced_disabled(self):
        data = skill_distiller.parse_skill_yaml_output(_VALID_SKILL_YAML)
        assert data is not None
        assert data["id"] == "daily_report_check"
        assert data["enabled"] is False  # 红线：无论模型写什么都强制不启用

    def test_model_sets_enabled_true_still_forced_false(self):
        text = _VALID_SKILL_YAML + "enabled: true\n"
        data = skill_distiller.parse_skill_yaml_output(text)
        assert data is not None and data["enabled"] is False

    def test_fenced_yaml(self):
        data = skill_distiller.parse_skill_yaml_output(f"```yaml\n{_VALID_SKILL_YAML}\n```")
        assert data is not None

    def test_invalid_schema_rejected(self):
        # id 不满足 ^[a-z][a-z0-9_]{2,63}$
        bad = _VALID_SKILL_YAML.replace("id: daily_report_check", "id: X")
        assert skill_distiller.parse_skill_yaml_output(bad) is None

    def test_dsn_rejected(self):
        bad = _VALID_SKILL_YAML.replace(
            "description: 生成日报前先核对日期范围与数据源",
            "description: 连接 mysql://user:pass@host/db 后生成",
        )
        assert skill_distiller.parse_skill_yaml_output(bad) is None

    def test_garbage_rejected(self):
        assert skill_distiller.parse_skill_yaml_output("模型胡言乱语") is None
        assert skill_distiller.parse_skill_yaml_output("") is None


# ---- 蒸馏全链路与触发条件 ----------------------------------------------------


class TestDistiller:
    @pytest.mark.asyncio
    async def test_run_skill_distill_full_path(self, monkeypatch):
        events.flush_evolution_events()

        class _FakeRouter:
            async def route(self, *, task: str, prompt: str) -> str:
                assert task == "skill_distill"
                assert "轨迹" in prompt
                return _VALID_SKILL_YAML

        monkeypatch.setattr("agent.llm.router.LMRouter", _FakeRouter)
        # 造两条成功轨迹作素材
        for i in range(2):
            await storage.record_trajectory(
                session_id=f"run-d{i}",
                task_signature="sig-d",
                intent={"rewritten_query": "出昨天的日报"},
                active_skill_id="",
                tool_fp="db.query",
                outcome="success",
                answer_digest="日报已生成",
            )
        draft = await skill_distiller.run_skill_distill("sig-d")
        assert draft is not None and draft["slug"] == "daily_report_check"
        queued = await events.consume_evolution_events()
        kinds = [k for k, _ in queued]
        assert skill_distiller.EVT_SKILL_DRAFT_READY in kinds

    @pytest.mark.asyncio
    async def test_trigger_below_threshold_skips(self, monkeypatch):
        monkeypatch.setattr(settings, "skill_draft_min_successes", 3)
        await storage.record_trajectory(
            session_id="r1",
            task_signature="sig-low",
            intent={},
            active_skill_id="",
            tool_fp="",
            outcome="success",
            answer_digest="ok",
        )
        called = []
        monkeypatch.setattr(
            "agent.evolution.skill_distiller.run_skill_distill",
            lambda *a, **k: called.append(1),  # type: ignore[arg-type]
        )
        assert await skill_distiller.maybe_distill_for_signature("sig-low") is None
        assert called == []

    @pytest.mark.asyncio
    async def test_trigger_honors_conditions(self, monkeypatch):
        monkeypatch.setattr(settings, "skill_draft_min_successes", 2)
        captured: list[str] = []

        async def _fake_distill(sig, *a, **k):
            captured.append(sig)
            # 真实落一条草稿，验证后续同签名的去重判断（不重复蒸馏）
            await storage.insert_skill_draft(
                slug=f"auto_{sig}", name="x", yaml_text="{}", task_signature=sig
            )
            return {"id": 1, "slug": "x", "name": "x"}

        monkeypatch.setattr("agent.evolution.skill_distiller.run_skill_distill", _fake_distill)

        # 2 条成功且无 Skill 覆盖 → 触发
        for i in range(2):
            await storage.record_trajectory(
                session_id=f"run-t{i}",
                task_signature="sig-t",
                intent={},
                active_skill_id="",
                tool_fp="db.query",
                outcome="success",
                answer_digest="ok",
            )
        assert await skill_distiller.maybe_distill_for_signature("sig-t") is not None
        assert captured == ["sig-t"]

        # 同签名已有待审草稿 → 不重复蒸馏
        captured.clear()
        assert await skill_distiller.maybe_distill_for_signature("sig-t") is None
        assert captured == []

    @pytest.mark.asyncio
    async def test_approved_draft_blocks_re_distill(self, monkeypatch):
        """已采纳草稿也拦重复蒸馏：新技能被 approve 后、带 active_skill_id 的
        轨迹产生前的窗口期内，不能对同签名再蒸馏出雷同草稿。"""
        monkeypatch.setattr(settings, "skill_draft_min_successes", 2)
        draft_id = await storage.insert_skill_draft(
            slug="approved_one", name="x", yaml_text="{}", task_signature="sig-a"
        )
        await storage.set_skill_draft_status(draft_id, "approved")
        for i in range(2):
            await storage.record_trajectory(
                session_id=f"run-a{i}",
                task_signature="sig-a",
                intent={},
                active_skill_id="",  # 新技能尚未产生带 skill 的轨迹（窗口期）
                tool_fp="db.query",
                outcome="success",
                answer_digest="ok",
            )
        called: list[str] = []

        async def _fake_distill(sig, *a, **k):
            called.append(sig)

        monkeypatch.setattr("agent.evolution.skill_distiller.run_skill_distill", _fake_distill)
        assert await skill_distiller.maybe_distill_for_signature("sig-a") is None
        assert called == []

    @pytest.mark.asyncio
    async def test_existing_skill_coverage_skips(self, monkeypatch):
        monkeypatch.setattr(settings, "skill_draft_min_successes", 2)
        for i in range(2):
            await storage.record_trajectory(
                session_id=f"run-c{i}",
                task_signature="sig-c",
                intent={},
                active_skill_id="existing_skill",  # 已有技能承接
                tool_fp="",
                outcome="success",
                answer_digest="ok",
            )
        called = []

        async def _fake_distill(sig, *a, **k):
            called.append(sig)

        monkeypatch.setattr("agent.evolution.skill_distiller.run_skill_distill", _fake_distill)
        assert await skill_distiller.maybe_distill_for_signature("sig-c") is None
        assert called == []


# ---- 主对话 Judge -----------------------------------------------------------


class TestJudge:
    def setup_method(self):
        judge.reset_judge_counter()

    def test_sampling_deterministic(self):
        judge.reset_judge_counter()
        assert judge._sampled(sample_rate=0.0) is False
        judge.reset_judge_counter()
        results = [judge._sampled(sample_rate=0.5) for _ in range(4)]
        assert results.count(True) == 2  # 每 2 次抽 1 次，确定性可复现
        judge.reset_judge_counter()
        assert all(judge._sampled(sample_rate=1.0) for _ in range(3))

    @pytest.mark.asyncio
    async def test_judge_scores_and_records(self, monkeypatch):
        judge.reset_judge_counter()

        class _FakeRouter:
            async def route(self, *, task: str, prompt: str) -> str:
                assert task == "answer_judge"
                return '{"score": 4, "reason": "基本完成"}'

        monkeypatch.setattr("agent.llm.router.LMRouter", _FakeRouter)
        result = await judge.maybe_judge_answer(
            run_id="run-j",
            task_signature="sig-j",
            user_prompt="查询订单",
            final_answer="已查到 42 条。",
            sample_rate=1.0,
        )
        assert result is not None and result["score"] == 4

    @pytest.mark.asyncio
    async def test_judge_not_sampled_no_llm_call(self, monkeypatch):
        judge.reset_judge_counter()
        called = []

        class _FakeRouter:
            async def route(self, *, task: str, prompt: str) -> str:
                called.append(task)
                return '{"score": 5}'

        monkeypatch.setattr("agent.llm.router.LMRouter", _FakeRouter)
        result = await judge.maybe_judge_answer(
            run_id="r",
            task_signature="s",
            user_prompt="q",
            final_answer="a",
            sample_rate=0.0,
        )
        assert result is None and called == []


# ---- 轨迹钩子成功路径 ---------------------------------------------------------


class TestTrajectorySuccessPath:
    @pytest.mark.asyncio
    async def test_success_triggers_judge_and_distill(self, monkeypatch):
        judged: list[str] = []
        distilled: list[str] = []

        async def _fake_judge(**kw):
            judged.append(kw["task_signature"])
            return None

        async def _fake_distill(sig, **kw):
            distilled.append(sig)
            return None

        monkeypatch.setattr("agent.evolution.judge.maybe_judge_answer", _fake_judge)
        monkeypatch.setattr(
            "agent.evolution.skill_distiller.maybe_distill_for_signature", _fake_distill
        )
        await trajectory.record_run_outcome(
            run_id="run-s",
            user_prompt="查询",
            state={
                "intent_analysis": {"intent_category": "data_query"},
                "final_answer": "查询完成。",
                "tool_results": [{"name": "db.query"}],
                "trace": [],
            },
        )
        assert len(judged) == 1 and len(distilled) == 1
        assert judged[0] == distilled[0]

    @pytest.mark.asyncio
    async def test_fail_does_not_trigger_judge(self, monkeypatch):
        judged: list[str] = []

        async def _fake_judge(**kw):
            judged.append("x")

        async def _fake_reflect(traj, **kw):
            return None

        monkeypatch.setattr("agent.evolution.judge.maybe_judge_answer", _fake_judge)
        monkeypatch.setattr("agent.evolution.reflection.run_reflection", _fake_reflect)
        await trajectory.record_run_outcome(
            run_id="run-f",
            user_prompt="x",
            state={"final_answer": "", "trace": []},  # 无终答 → fail
        )
        assert judged == []


# ---- API：草稿审核 + 看板 ----------------------------------------------------


@pytest.fixture
def client(monkeypatch, tmp_path):
    async def _no_reflect(traj, **kw):
        return None

    monkeypatch.setattr("agent.evolution.reflection.run_reflection", _no_reflect)
    # approve 写入的 skills 目录指向临时路径（不污染真实技能库）
    from agent.skills.loader import SkillLoader

    fake_loader = SkillLoader(tmp_path / "skills")
    monkeypatch.setattr("agent.skills.api._loader", fake_loader)
    app = FastAPI()
    app.include_router(evolution_router)
    return TestClient(app)


def _seed_draft(slug: str = "daily_report_check", yaml_text: str | None = None) -> int:
    conn = sqlite3.connect(settings.evolution_db_path)
    try:
        conn.executescript(storage.SCHEMA_CREATE_TABLES)
        cur = conn.execute(
            "INSERT INTO skill_drafts (slug, name, yaml_text, task_signature, ts)"
            " VALUES (?, ?, ?, ?, ?)",
            (
                slug,
                "日报核对规范",
                yaml_text or _VALID_SKILL_YAML,
                "sig-api",
                "2026-08-31T00:00:00",
            ),
        )
        draft_id = cur.lastrowid
        conn.commit()
    finally:
        conn.close()
    return int(draft_id or 0)


class TestDraftApi:
    def test_list_drafts(self, client):
        _seed_draft()
        body = client.get("/evolution/skill-drafts").json()
        assert body["ok"] is True
        assert any(it["slug"] == "daily_report_check" for it in body["items"])

    def test_approve_writes_skill_file_and_enables(self, client, tmp_path):
        draft_id = _seed_draft()
        resp = client.post(f"/evolution/skill-drafts/{draft_id}/approve")
        assert resp.status_code == 200
        body = resp.json()
        assert body["skill_id"] == "daily_report_check"
        # 技能文件已写入且启用（approve 是唯一启用入口）
        path = tmp_path / "skills" / "daily_report_check.yaml"
        assert path.exists()
        saved = yaml.safe_load(path.read_text(encoding="utf-8"))
        assert saved["enabled"] is True
        # 重复审核被拒（状态已流转）
        assert client.post(f"/evolution/skill-drafts/{draft_id}/approve").status_code == 409

    def test_approve_conflict_with_existing_skill(self, client, tmp_path):
        # 预置同名技能文件 → approve 撞车 409
        skills_dir = tmp_path / "skills"
        skills_dir.mkdir(parents=True, exist_ok=True)
        (skills_dir / "daily_report_check.yaml").write_text(_VALID_SKILL_YAML, encoding="utf-8")
        from agent.skills.api import get_loader

        get_loader().load_all()
        draft_id = _seed_draft()
        assert client.post(f"/evolution/skill-drafts/{draft_id}/approve").status_code == 409

    def test_reject(self, client):
        draft_id = _seed_draft()
        resp = client.post(f"/evolution/skill-drafts/{draft_id}/reject")
        assert resp.status_code == 200 and resp.json()["status"] == "rejected"
        # 拒绝后不再出现在待审列表
        assert all(
            it["id"] != draft_id for it in client.get("/evolution/skill-drafts").json()["items"]
        )

    def test_missing_draft_404(self, client):
        assert client.post("/evolution/skill-drafts/9999/approve").status_code == 404
        assert client.post("/evolution/skill-drafts/9999/reject").status_code == 404


class TestStatsApi:
    def test_stats_summary(self, client):
        conn = sqlite3.connect(settings.evolution_db_path)
        try:
            conn.executescript(storage.SCHEMA_CREATE_TABLES)
            conn.execute(
                "INSERT INTO evaluation_signals"
                " (session_id, task_signature, source, score, rating, ts)"
                " VALUES ('s', 'sig', 'judge', 0.8, 4, '2026-08-31T00:00:00')"
            )
            conn.execute(
                "INSERT INTO evaluation_signals"
                " (session_id, task_signature, source, score, rating, ts)"
                " VALUES ('s', 'sig', 'user', 1.0, 1, '2026-08-31T00:00:00')"
            )
            conn.execute(
                "INSERT INTO experiences (insight, tags_json, ts)"
                " VALUES ('x', '[]', '2026-08-31T00:00:00')"
            )
            conn.commit()
        finally:
            conn.close()
        body = client.get("/evolution/stats").json()
        assert body["ok"] is True
        assert body["signals_total"] == 2
        assert body["user_signals"] == 1 and body["user_up"] == 1
        assert body["judge_avg"] == 4.0
        assert body["experiences_active"] == 1
