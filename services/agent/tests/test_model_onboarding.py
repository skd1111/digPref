"""模型接入三层修复测试（2026-08-14）。

覆盖五层改动：
    1. 意图层：model_onboard / conn_test 细分类型解析与四分类映射
    2. 页面上下文：format_page_context 格式化 + decompose prompt 占位符
    3. 提问策略：ASK_USER 单问题护栏（_cap_clarifying_questions）
    4. 能力层：model_config_upsert / probe_chat_endpoint 两个新 builtin 工具
    5. 确认卡：responder 确认门槛输出 clarify 围栏（确认/修改）

router.db 隔离靠 conftest.py 的 autouse _isolate fixture（chdir tmp_path，
settings.llm_router_db_path 相对路径自动落临时目录）。
"""

from __future__ import annotations

import json
import re
import sqlite3

import httpx
from agent.builtin.llm_admin import (
    builtin_model_config_upsert,
    builtin_probe_chat_endpoint,
)
from agent.graph.nodes.decompose import _cap_clarifying_questions
from agent.graph.nodes.responder import _confirmation_body, responder_node
from agent.graph.state import format_page_context
from agent.llm.prompts import load_prompt
from agent.llm.types import _CATEGORY_TO_INTENT, IntentAnalysis

# ---- 1. 意图层：操作类细分类型 ------------------------------------------------


class TestModelOnboardIntentCategory:
    def test_model_onboard_category_kept(self):
        """model_onboard 是合法细分类型（此前会被归一成 knowledge_qa）。"""
        a = IntentAnalysis.from_raw(
            {
                "intent": "mutate",
                "intent_category": "model_onboard",
                "entities": {
                    "model_name": "DeepSeek-RD-Llama-70B-Int8",
                    "endpoint": "http://172.1.0.134:8000/v1/chat/completions",
                },
                "need_tool": True,
                "need_clarification": False,
            },
            fallback_text="帮我连接内网模型",
        )
        assert a.intent == "mutate"
        assert a.intent_category == "model_onboard"
        assert a.entities["model_name"] == "DeepSeek-RD-Llama-70B-Int8"
        assert a.need_clarification is False

    def test_conn_test_category_kept(self):
        a = IntentAnalysis.from_raw(
            {"intent": "query", "intent_category": "conn_test"},
            fallback_text="测一下 172.1.0.134:8000 通不通",
        )
        assert a.intent_category == "conn_test"

    def test_category_to_intent_mapping(self):
        assert _CATEGORY_TO_INTENT["model_onboard"] == "mutate"
        assert _CATEGORY_TO_INTENT["conn_test"] == "query"

    def test_unknown_category_still_falls_back(self):
        a = IntentAnalysis.from_raw(
            {"intent": "query", "intent_category": "not_a_category"},
            fallback_text="x",
        )
        assert a.intent_category == "knowledge_qa"

    def test_intent_router_prompt_has_operational_few_shot(self):
        prompt = load_prompt("intent_router")
        assert "model_onboard" in prompt
        assert "conn_test" in prompt
        # 槽位规则：必填槽齐全禁止追问
        assert "model_name" in prompt and "endpoint" in prompt


# ---- 2. 页面上下文 -------------------------------------------------------------


class TestPageContext:
    def test_format_page_context_full(self):
        ctx = {"page": {"workMode": "operator", "tabTitle": "内网模型接入配置"}}
        text = format_page_context(ctx)
        assert "内网模型接入配置" in text
        assert "operator" in text

    def test_format_page_context_flat_dict(self):
        assert "模型管理" in format_page_context({"tabTitle": "模型管理"})

    def test_format_page_context_invalid_returns_empty(self):
        assert format_page_context(None) == ""
        assert format_page_context("not a dict") == ""
        assert format_page_context({}) == ""

    def test_decompose_prompt_has_page_context_placeholder(self):
        assert "{{PAGE_CONTEXT}}" in load_prompt("decompose")


# ---- 3. 提问策略护栏 -----------------------------------------------------------


def _ask_decision(questions: list[str]) -> dict:
    return {
        "decision": {
            "mode": "ASK_USER",
            "clarifying_questions": questions,
            "reason": "test",
        },
        "scoring": {},
    }


class TestCapClarifyingQuestions:
    def test_multiple_questions_capped_to_one(self):
        capped = _cap_clarifying_questions(_ask_decision(["问题一？", "问题二？", "问题三？"]))
        qs = capped["decision"]["clarifying_questions"]
        assert len(qs) == 1
        assert qs[0].startswith("问题一？")

    def test_single_question_untouched(self):
        d = _ask_decision(["目标环境是测试还是生产？"])
        capped = _cap_clarifying_questions(d)
        assert capped["decision"]["clarifying_questions"] == ["目标环境是测试还是生产？"]

    def test_non_ask_user_untouched(self):
        d = {
            "decision": {"mode": "TOOL_ONLY", "clarifying_questions": ["a", "b"]},
            "scoring": {},
        }
        assert _cap_clarifying_questions(d) is d

    def test_empty_questions_untouched(self):
        capped = _cap_clarifying_questions(_ask_decision([]))
        assert capped["decision"]["clarifying_questions"] == []


# ---- 4. 能力层：新 builtin 工具 -------------------------------------------------


class TestModelConfigUpsert:
    async def test_upsert_writes_router_db(self):
        result = await builtin_model_config_upsert(
            name="deepseek-rd",
            type="private",
            base_url="http://172.1.0.134:8000/v1/",
            model_name="DeepSeek-RD-Llama-70B-Int8",
        )
        assert result.ok is True
        assert result.risk_level == "high"
        assert result.content["base_url"] == "http://172.1.0.134:8000/v1"  # 尾斜杠清掉

        conn = sqlite3.connect("router.db", timeout=5)
        try:
            row = conn.execute(
                "SELECT type, base_url, model_name, enabled FROM llm_backends WHERE name=?",
                ("deepseek-rd",),
            ).fetchone()
        finally:
            conn.close()
        assert row is not None
        assert row[0] == "private"
        assert row[1] == "http://172.1.0.134:8000/v1"
        assert row[2] == "DeepSeek-RD-Llama-70B-Int8"
        assert row[3] == 1

    async def test_upsert_disables_same_residency_on_enable(self):
        await builtin_model_config_upsert(
            name="first", type="private", base_url="http://a:1/v1", model_name="m1"
        )
        second = await builtin_model_config_upsert(
            name="second", type="private", base_url="http://b:2/v1", model_name="m2"
        )
        assert second.ok is True
        assert second.content.get("disabled_same_residency") == ["first"]

    async def test_api_key_ref_never_in_result(self):
        result = await builtin_model_config_upsert(
            name="with-key",
            type="private",
            base_url="http://c:3/v1",
            model_name="m3",
            api_key_ref="llm.with-key.api_key",
        )
        assert result.ok is True
        assert "llm.with-key.api_key" not in json.dumps(result.to_dict(), ensure_ascii=False)

    async def test_invalid_type_rejected(self):
        result = await builtin_model_config_upsert(
            name="bad", type="gpu", base_url="http://x:1/v1", model_name="m"
        )
        assert result.ok is False
        assert result.error == "invalid_type"

    async def test_missing_required_rejected(self):
        result = await builtin_model_config_upsert(
            name="", type="private", base_url="http://x:1/v1", model_name="m"
        )
        assert result.ok is False
        assert result.error == "missing_required_field"

    async def test_non_http_url_rejected(self):
        result = await builtin_model_config_upsert(
            name="ftp", type="private", base_url="ftp://x:1", model_name="m"
        )
        assert result.ok is False
        assert result.error == "invalid_url"


class _FakeResponse:
    def __init__(self, status_code: int):
        self.status_code = status_code


class _FakeAsyncClient:
    """httpx.AsyncClient 替身：resp / exc 二选一。"""

    def __init__(self, resp: _FakeResponse | None = None, exc: Exception | None = None):
        self.resp = resp
        self.exc = exc
        self.posted_url = ""
        self.posted_json: dict | None = None

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    async def post(self, url: str, json: dict | None = None):
        self.posted_url = url
        self.posted_json = json
        if self.exc is not None:
            raise self.exc
        assert self.resp is not None
        return self.resp


class TestProbeChatEndpoint:
    async def test_reachable_endpoint(self, monkeypatch):
        fake = _FakeAsyncClient(resp=_FakeResponse(200))
        monkeypatch.setattr(httpx, "AsyncClient", lambda **kw: fake)
        result = await builtin_probe_chat_endpoint(
            url="http://172.1.0.134:8000/v1", model="DeepSeek-RD-Llama-70B-Int8"
        )
        assert result.ok is True
        assert result.content["reachable"] is True
        assert result.content["status_code"] == 200
        assert result.content["latency_ms"] >= 0
        # URL 自动补 /chat/completions；最小探测请求
        assert fake.posted_url == "http://172.1.0.134:8000/v1/chat/completions"
        assert fake.posted_json is not None
        assert fake.posted_json["max_tokens"] == 1

    async def test_timeout_reports_unreachable(self, monkeypatch):
        fake = _FakeAsyncClient(exc=httpx.ConnectTimeout("timeout"))
        monkeypatch.setattr(httpx, "AsyncClient", lambda **kw: fake)
        result = await builtin_probe_chat_endpoint(
            url="http://10.0.0.1:9999/v1/chat/completions", model="m"
        )
        assert result.ok is False
        assert result.error == "timeout"
        assert result.content["reachable"] is False

    async def test_http_error_reports_status(self, monkeypatch):
        fake = _FakeAsyncClient(resp=_FakeResponse(401))
        monkeypatch.setattr(httpx, "AsyncClient", lambda **kw: fake)
        result = await builtin_probe_chat_endpoint(url="http://x:1/v1", model="m")
        assert result.ok is False
        assert result.content["status_code"] == 401
        assert result.content["auth_required"] is True

    async def test_invalid_url_rejected(self):
        result = await builtin_probe_chat_endpoint(url="not-a-url", model="m")
        assert result.ok is False
        assert result.error == "invalid_url"


class TestToolRegistration:
    def test_tools_have_schema_description_risk(self):
        from agent.builtin.registry import TOOL_DESCRIPTIONS, TOOL_RISK_LEVEL
        from agent.builtin.schemas import get_builtin_schema

        for name in ("model_config_upsert", "probe_chat_endpoint"):
            assert get_builtin_schema(name) is not None
            assert TOOL_DESCRIPTIONS.get(name)
            assert name in TOOL_RISK_LEVEL

    def test_risk_levels_match_policy(self):
        from agent.builtin.registry import TOOL_RISK_LEVEL

        assert TOOL_RISK_LEVEL["model_config_upsert"] == "high"  # 写配置 → HITL
        assert TOOL_RISK_LEVEL["probe_chat_endpoint"] == "read"  # 只读探测

    def test_write_detector_flags_upsert_as_write(self):
        from agent.safety.write_detector import is_write_call

        call = {"name": "model_config_upsert", "args": {}, "risk_level": "high"}
        assert is_write_call(call) is True

    def test_catalog_keywords_present(self):
        from agent.tools.catalog import _BUILTIN_KEYWORDS

        assert "模型接入" in _BUILTIN_KEYWORDS["model_config_upsert"]
        assert "连通性测试" in _BUILTIN_KEYWORDS["probe_chat_endpoint"]


# ---- 5. 确认卡 ------------------------------------------------------------------


class TestConfirmationCard:
    def test_confirmation_body_has_clarify_fence(self):
        body = _confirmation_body(
            "将按以下参数接入 DeepSeek-RD-Llama-70B-Int8：endpoint=http://172.1.0.134:8000。"
        )
        assert "未确认前不会执行" in body
        m = re.search(r"```clarify\s*([\s\S]*?)```", body)
        assert m, "确认门槛必须输出 clarify 围栏"
        items = json.loads(m.group(1))
        assert len(items) == 1
        texts = [o["text"] for o in items[0]["options"]]
        assert "确认执行" in texts and "修改参数" in texts
        # 恰好一个推荐项（前端预选）
        assert sum(1 for o in items[0]["options"] if o["recommended"]) == 1

    async def test_responder_confirmation_decision_emits_card(self):
        state = {
            "user_prompt": "帮我连接内网模型",
            "decompose_decision": {
                "decision": {
                    "mode": "TOOL_ONLY",
                    "user_confirmation_required": True,
                    "confirmation_message": "将按以下参数接入模型 X：…",
                    "clarifying_questions": [],
                }
            },
            "trace": [],
        }
        out = await responder_node(state, llm=None)  # type: ignore[arg-type]
        assert "```clarify" in out["final_answer"]
        assert "确认执行" in out["final_answer"]
