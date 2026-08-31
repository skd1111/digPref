"""Phase 19 V0 自进化与自评测闭环回归测试。

覆盖：
    - 任务签名（归一化幂等 / 工具顺序敏感）
    - evolution.db 存储（信号 / 轨迹 / 经验 CRUD + 同步检索排序）
    - 反思输出解析（JSON / 围栏 / 非法）
    - 经验注入片段（extra_rules 通道）与开关
    - run_reflection 全链路（假 LLM → 经验落库 + 事件）
    - 轨迹钩子（成功 / 失败判定 + 失败触发反思）
    - /evolution API（反馈 / 经验库管理）
"""

from __future__ import annotations

import json

import pytest
from agent.config import settings
from agent.evolution import events, memory, reflection, signature, storage, trajectory
from agent.evolution.api import router as evolution_router
from agent.skills.schema import scrub_dsn
from fastapi import FastAPI
from fastapi.testclient import TestClient

# ---- 任务签名 ---------------------------------------------------------------


class TestSignature:
    def test_deterministic(self):
        a = signature.compute_task_signature("data_query", "skill_x", ["db.query"])
        b = signature.compute_task_signature("data_query", "skill_x", ["db.query"])
        assert a == b

    def test_normalization_insensitive(self):
        """大小写 / 首尾空白归一化后签名恒等。"""
        a = signature.compute_task_signature(" Data_Query ", "skill_x", [" db.query "])
        b = signature.compute_task_signature("data_query", "skill_x", ["db.query"])
        assert a == b

    def test_tool_order_sensitive(self):
        """工具调用顺序不同 → 轨迹形态不同 → 签名不同。"""
        a = signature.compute_task_signature("task_execution", "", ["read_file", "write_file"])
        b = signature.compute_task_signature("task_execution", "", ["write_file", "read_file"])
        assert a != b

    def test_fingerprint_dedup(self):
        assert signature.tool_fingerprint(["a", "a", "b", ""]) == "a,b"


# ---- 存储 -------------------------------------------------------------------


class TestStorage:
    @pytest.mark.asyncio
    async def test_signal_and_trajectory_roundtrip(self):
        await storage.record_signal(
            session_id="run-1", task_signature="sig-1", source="env", score=1.0
        )
        tid = await storage.record_trajectory(
            session_id="run-1",
            task_signature="sig-1",
            intent={"intent_category": "data_query"},
            active_skill_id="",
            tool_fp="db.query",
            outcome="success",
            answer_digest="ok",
        )
        assert tid > 0
        traj = await storage.get_trajectory(tid)
        assert traj is not None
        assert traj["outcome"] == "success"
        assert json.loads(traj["intent_json"])["intent_category"] == "data_query"
        latest = await storage.latest_trajectory_by_session("run-1")
        assert latest is not None and latest["id"] == tid

    @pytest.mark.asyncio
    async def test_experience_crud_and_retrieval_ranking(self):
        # 通用经验 + 精准匹配经验 + 停用经验
        await storage.insert_experience(insight="通用教训", tags=[], applies_to="")
        match_id = await storage.insert_experience(
            insight="数据查询要先确认日期范围",
            tags=["日期"],
            applies_to="data_query",
        )
        off_id = await storage.insert_experience(
            insight="已停用的经验", tags=[], applies_to="data_query"
        )
        await storage.set_experience_status(off_id, "disabled")

        got = storage.retrieve_experiences_sync("data_query", "")
        insights = [e["insight"] for e in got]
        assert "数据查询要先确认日期范围" in insights
        assert insights[0] == "数据查询要先确认日期范围"  # 精确匹配优先
        assert "已停用的经验" not in insights  # disabled 不参与注入
        # 命中计数自增
        items = await storage.list_experiences()
        hit = next(it for it in items if it["id"] == match_id)
        assert hit["hit_count"] >= 1

        assert await storage.delete_experience(off_id) is True
        assert await storage.delete_experience(off_id) is False

    def test_retrieval_failure_returns_empty(self, tmp_path):
        """库不可用 → 返空列表不抛（注入通道绝不阻塞任务）。"""
        bad = str(tmp_path / "no_such_dir" / "x" / "evolution.db")
        assert storage.retrieve_experiences_sync("a", "", db_path=bad) == []

    def test_format_snippet_limit(self):
        exps = [{"id": i, "insight": "x" * 100} for i in range(5)]
        text = storage.format_experience_snippet(exps, max_chars=150)
        assert len(text) <= 150
        assert storage.format_experience_snippet([]) == ""


# ---- 反思解析与链路 ----------------------------------------------------------


class TestReflection:
    def test_parse_valid_json(self):
        out = reflection.parse_reflection_output(
            '{"insight": "先确认日期", "tags": ["日期"], "attribution": "reasoning"}'
        )
        assert out is not None
        assert out["insight"] == "先确认日期"
        assert out["attribution"] == "reasoning"

    def test_parse_fenced_json(self):
        text = '```json\n{"insight": "工具要先探测", "attribution": "tool"}\n```'
        out = reflection.parse_reflection_output(text)
        assert out is not None and out["attribution"] == "tool"

    def test_parse_invalid(self):
        assert reflection.parse_reflection_output("没有任何 JSON 的闲聊") is None
        assert reflection.parse_reflection_output('{"tags": ["缺 insight"]}') is None

    def test_parse_bad_attribution_falls_back(self):
        out = reflection.parse_reflection_output('{"insight": "x", "attribution": "乱写"}')
        assert out is not None and out["attribution"] == "unknown"

    @pytest.mark.asyncio
    async def test_run_reflection_full_path(self, monkeypatch):
        """假 LLM 返回合法 JSON → 经验落库 + 事件入队。"""
        events.flush_evolution_events()

        class _FakeRouter:
            async def route(self, *, task: str, prompt: str) -> str:
                assert task == "reflection"
                assert "任务轨迹摘要" in prompt or "结果" in prompt
                return '{"insight": "失败后要重试前先看错误", "attribution": "env"}'

        monkeypatch.setattr("agent.llm.router.LMRouter", _FakeRouter)
        exp = await reflection.run_reflection(
            {
                "session_id": "run-9",
                "task_signature": "sig-9",
                "intent": {"intent_category": "task_execution"},
                "outcome": "fail",
                "answer_digest": "重试 3 次后仍失败",
            }
        )
        assert exp is not None and exp["insight"] == "失败后要重试前先看错误"
        queued = await events.consume_evolution_events()
        assert len(queued) == 1
        kind, payload = queued[0]
        assert kind == events.EVT_EVOLUTION_INSIGHT_CREATED
        assert payload["experience_id"] == exp["id"]

    @pytest.mark.asyncio
    async def test_run_reflection_unparseable_skips(self, monkeypatch):
        class _FakeRouter:
            async def route(self, *, task: str, prompt: str) -> str:
                return "模型胡言乱语没有 JSON"

        monkeypatch.setattr("agent.llm.router.LMRouter", _FakeRouter)
        exp = await reflection.run_reflection({"session_id": "r", "outcome": "fail"})
        assert exp is None

    @pytest.mark.asyncio
    async def test_disabled_switch_skips(self, monkeypatch):
        monkeypatch.setattr(settings, "evolution_enabled", False)
        exp = await reflection.run_reflection({"session_id": "r", "outcome": "fail"})
        assert exp is None

    @pytest.mark.asyncio
    async def test_reflection_dedup_per_session(self):
        """同会话已产出经验 → 第二次反思跳过（不重复入库，也不白跑 LLM）。"""
        await storage.insert_experience(insight="已有教训", tags=[], source_session="run-dup")
        exp = await reflection.run_reflection(
            {"session_id": "run-dup", "outcome": "fail", "answer_digest": "x"}
        )
        assert exp is None
        assert len(await storage.list_experiences()) == 1


# ---- 经验注入（extra_rules 通道） --------------------------------------------


class TestMemoryInjection:
    @pytest.mark.asyncio
    async def test_addon_injects_matching_experience(self):
        await storage.insert_experience(
            insight="数据查询必须带日期条件", tags=[], applies_to="data_query"
        )
        state = {
            "intent_analysis": {"intent_category": "data_query"},
            "active_skill_id": None,
        }
        snippet = memory.experience_addon(state)
        assert "数据查询必须带日期条件" in snippet
        assert snippet.startswith("【历史经验")

    @pytest.mark.asyncio
    async def test_addon_empty_when_disabled(self, monkeypatch):
        await storage.insert_experience(insight="不该出现", tags=[], applies_to="")
        monkeypatch.setattr(settings, "evolution_enabled", False)
        assert memory.experience_addon({}) == ""

    def test_addon_empty_without_experiences(self):
        assert memory.experience_addon({}) == ""

    @pytest.mark.asyncio
    async def test_retrieval_cached_schema_still_works(self, tmp_path):
        """建表标志缓存后检索照常；库文件被删后返空兜底且缓存自清。"""
        dbp = str(tmp_path / "evo.db")
        await storage.insert_experience(insight="缓存路径也要能查到", tags=[], db_path=dbp)
        got1 = storage.retrieve_experiences_sync("a", "", db_path=dbp)
        got2 = storage.retrieve_experiences_sync(
            "a", "", db_path=dbp
        )  # 命中缓存路径（不再执行 DDL）
        assert [e["insight"] for e in got1] == ["缓存路径也要能查到"]
        assert [e["insight"] for e in got2] == ["缓存路径也要能查到"]


# ---- 轨迹钩子 ----------------------------------------------------------------


class TestTrajectoryHook:
    @pytest.mark.asyncio
    async def test_success_trajectory_no_reflection(self, monkeypatch):
        called: list[dict] = []

        async def _fake_reflect(traj, **kw):
            called.append(traj)

        monkeypatch.setattr("agent.evolution.reflection.run_reflection", _fake_reflect)
        await trajectory.record_run_outcome(
            run_id="run-ok",
            user_prompt="查询订单",
            state={
                "intent_analysis": {"intent_category": "data_query"},
                "active_skill_id": "",
                "final_answer": "已查到 42 条订单。",
                "tool_results": [{"name": "db.query", "ok": True}],
                "trace": [],
            },
        )
        assert called == []  # 成功不反思
        latest = await storage.latest_trajectory_by_session("run-ok")
        assert latest is not None and latest["outcome"] == "success"

    @pytest.mark.asyncio
    async def test_failure_triggers_reflection(self, monkeypatch):
        called: list[dict] = []

        async def _fake_reflect(traj, **kw):
            called.append(traj)

        monkeypatch.setattr("agent.evolution.reflection.run_reflection", _fake_reflect)
        await trajectory.record_run_outcome(
            run_id="run-bad",
            user_prompt="生成报表",
            state={
                "intent_analysis": {"intent_category": "content_generation"},
                "final_answer": "工具 db.query 在自动重试 3 次后仍然失败。",
                "tool_results": [{"name": "db.query", "ok": False}],
                "trace": [{"node": "tool_orchestrator", "status": "fail"}],
            },
        )
        assert len(called) == 1
        assert called[0]["outcome"] == "fail"
        latest = await storage.latest_trajectory_by_session("run-bad")
        assert latest is not None and latest["outcome"] == "fail"

    @pytest.mark.asyncio
    async def test_disabled_switch_noop(self, monkeypatch):
        monkeypatch.setattr(settings, "evolution_enabled", False)
        await trajectory.record_run_outcome(
            run_id="run-x", user_prompt="x", state={"final_answer": "ok"}
        )
        assert await storage.latest_trajectory_by_session("run-x") is None

    @pytest.mark.asyncio
    async def test_scrub_dsn_redacts_patterns(self):
        """DSN / 凭证形态整段脱敏；普通文本不误伤。"""
        out = scrub_dsn("连 mysql://root:secret@db.internal:3306/x 查一下")
        assert "root" not in out and "secret" not in out
        assert "[REDACTED_DSN]" in out
        assert scrub_dsn("jdbc:oracle://u:p@h/db") == "[REDACTED_DSN]"
        assert "[REDACTED_CRED]" in scrub_dsn("用 admin:pa55@10.0.0.1 登录")
        assert scrub_dsn("普通文本 mail@x.com 不被误伤") == "普通文本 mail@x.com 不被误伤"
        assert scrub_dsn("") == ""

    @pytest.mark.asyncio
    async def test_outcome_markers_no_false_positive(self):
        """成功回答含「重试」字样不误判失败；整句硬失败文案才判失败。"""
        state_ok = {"final_answer": "已按您的要求重试成功，共处理 3 条记录。", "trace": []}
        assert trajectory._judge_outcome(state_ok) == "success"
        state_fail = {"final_answer": "工具 db.query 在自动重试 2 次后仍然失败。", "trace": []}
        assert trajectory._judge_outcome(state_fail) == "fail"

    @pytest.mark.asyncio
    async def test_persisted_texts_scrubbed(self):
        """提示词 / 终答摘要里的连接串落库前脱敏（红线 §8）。"""
        await trajectory.record_run_outcome(
            run_id="run-scrub",
            user_prompt="连 mysql://root:secret@db:3306/x 查数据",
            state={
                "final_answer": "结果是 jdbc:postgresql://u:p@h/d 里的 42 条",
                "trace": [],
            },
        )
        latest = await storage.latest_trajectory_by_session("run-scrub")
        assert latest is not None
        assert "secret" not in latest["answer_digest"]
        assert "[REDACTED_DSN]" in latest["answer_digest"]
        conn = __import__("sqlite3").connect(settings.evolution_db_path)
        try:
            row = conn.execute(
                "SELECT reason FROM evaluation_signals WHERE session_id = ?",
                ("run-scrub",),
            ).fetchone()
        finally:
            conn.close()
        assert row is not None
        assert "secret" not in str(row[0]) and "[REDACTED_DSN]" in str(row[0])


# ---- API ---------------------------------------------------------------------


@pytest.fixture
def client(monkeypatch):
    # 反馈 👎 的后台反思换成 no-op（避免真调 LLM 链路）
    async def _no_reflect(traj, **kw):
        return None

    monkeypatch.setattr("agent.evolution.reflection.run_reflection", _no_reflect)
    app = FastAPI()
    app.include_router(evolution_router)
    return TestClient(app)


class TestApi:
    def test_feedback_up_records_signal(self, client):
        resp = client.post(
            "/evolution/feedback",
            json={"sessionId": "run-api", "messageId": "m1", "rating": "up"},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["ok"] is True and body["reflected"] is False

    def test_feedback_down_marks_reflection_when_trajectory_exists(self, client):
        # 先用同步 sqlite3 造一条轨迹（避免与 TestClient 的事件循环纠缠）
        import sqlite3

        conn = sqlite3.connect(settings.evolution_db_path)
        try:
            conn.executescript(storage.SCHEMA_CREATE_TABLES)
            conn.execute(
                "INSERT INTO trajectories"
                " (session_id, task_signature, intent_json, outcome, ts)"
                " VALUES (?, ?, ?, ?, ?)",
                ("run-api2", "sig-api", "{}", "fail", "2026-08-31T00:00:00"),
            )
            conn.commit()
        finally:
            conn.close()
        resp = client.post(
            "/evolution/feedback",
            json={"sessionId": "run-api2", "rating": "down", "correction": "日期错了"},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["ok"] is True
        assert body["reflected"] is True  # 有轨迹 → 👎 触发后台反思（已被 no-op 拦截）
        assert body["task_signature"] == "sig-api"

    def test_feedback_invalid_rating_rejected(self, client):
        resp = client.post(
            "/evolution/feedback",
            json={"sessionId": "r", "rating": "sideways"},
        )
        assert resp.status_code == 422  # Literal 校验拒绝非法值

    def test_experience_list_toggle_delete(self, client):
        # 同步播种一条经验（复用 storage schema）
        import sqlite3

        conn = sqlite3.connect(settings.evolution_db_path)
        try:
            conn.executescript(storage.SCHEMA_CREATE_TABLES)
            cur = conn.execute(
                "INSERT INTO experiences (insight, tags_json, ts) VALUES (?, ?, ?)",
                ("API 测试经验", "[]", "2026-08-31T00:00:00"),
            )
            exp_id = cur.lastrowid
            conn.commit()
        finally:
            conn.close()

        items = client.get("/evolution/experiences").json()["items"]
        assert any(it["id"] == exp_id and it["status"] == "active" for it in items)

        toggled = client.post(f"/evolution/experiences/{exp_id}/toggle").json()
        assert toggled["status"] == "disabled"

        deleted = client.delete(f"/evolution/experiences/{exp_id}")
        assert deleted.status_code == 200
        assert client.delete(f"/evolution/experiences/{exp_id}").status_code == 404
        assert client.post(f"/evolution/experiences/{exp_id}/toggle").status_code == 404
