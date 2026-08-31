"""结构化槽位校验测试（2026-08-31）：Pydantic Schema + intent_slots 拦截。"""

from __future__ import annotations

from agent.llm.intent_slots import validate_slots
from agent.llm.types import IntentAnalysis, IntentAnalysisSchema

# ---- Pydantic Schema 硬校验 ----------------------------------------------------


class TestIntentAnalysisSchema:
    def test_valid_payload_passes(self):
        raw = {
            "rewritten_query": "查询订单表",
            "intent": "query",
            "intent_category": "data_query",
            "confidence": 0.8,
            "entities": {"target_table": "order_main"},
            "need_tool": True,
        }
        data = IntentAnalysisSchema.model_validate(raw).model_dump()
        assert data["confidence"] == 0.8
        assert data["entities"] == {"target_table": "order_main"}
        # 缺省字段补齐安全默认值
        assert data["need_clarification"] is False
        assert data["risk_level"] == "low"

    def test_unknown_fields_ignored(self):
        data = IntentAnalysisSchema.model_validate(
            {"intent": "query", "evil_injection": "DROP TABLE x"}
        ).model_dump()
        assert "evil_injection" not in data

    def test_confidence_str_coerced(self):
        data = IntentAnalysisSchema.model_validate({"confidence": "0.9"}).model_dump()
        assert data["confidence"] == 0.9

    def test_from_raw_schema_path(self):
        result = IntentAnalysis.from_raw(
            {"intent": "mutate", "confidence": 0.7, "risk_level": "high"},
            fallback_text="删掉订单表",
            backend="ollama",
        )
        assert result.intent == "mutate"
        assert result.confidence == 0.7
        assert result.risk_level == "high"
        assert result.rewritten_query == "删掉订单表"

    def test_from_raw_invalid_confidence_falls_back_lenient(self):
        """confidence 非法字符串 → Schema 校验失败 → 宽容兜底 0.5（行为兼容）。"""
        result = IntentAnalysis.from_raw(
            {"intent": "query", "confidence": "很高"}, fallback_text="q"
        )
        assert result.confidence == 0.5
        assert result.intent == "query"

    def test_from_raw_category_mapping_unchanged(self):
        """细分类型 → 四分类映射规则不受 Schema 影响。"""
        result = IntentAnalysis.from_raw({"intent_category": "model_onboard"}, fallback_text="x")
        assert result.intent == "mutate"
        assert result.intent_category == "model_onboard"


# ---- 槽位规则拦截 ----------------------------------------------------------------


class TestValidateSlots:
    def test_model_onboard_missing_endpoint_high_risk_blocks(self):
        analysis = {
            "intent": "mutate",
            "intent_category": "model_onboard",
            "risk_level": "high",
            "entities": {"model_name": "Qwen-RD"},
            "need_clarification": False,
        }
        out = validate_slots(analysis)
        assert out["need_clarification"] is True
        assert "endpoint" in out["missing_fields"]
        assert out["clarification_message"]
        # 原 dict 不被改写（返回新对象）
        assert analysis["need_clarification"] is False

    def test_model_onboard_slots_complete_no_block(self):
        analysis = {
            "intent": "mutate",
            "intent_category": "model_onboard",
            "risk_level": "high",
            "entities": {"model_name": "Qwen-RD", "endpoint": "http://172.1.0.9/v1"},
            "need_clarification": False,
        }
        out = validate_slots(analysis)
        assert out["need_clarification"] is False
        assert not out.get("missing_fields")

    def test_conn_test_endpoint_present_no_block(self):
        analysis = {
            "intent": "query",
            "intent_category": "conn_test",
            "risk_level": "medium",
            "entities": {"endpoint": "172.1.0.134:8000"},
            "need_clarification": False,
        }
        out = validate_slots(analysis)
        assert out["need_clarification"] is False

    def test_task_execution_critical_without_target_blocks(self):
        """高风险写操作无任何目标实体 → 拦截追问。"""
        analysis = {
            "intent": "mutate",
            "intent_category": "task_execution",
            "risk_level": "critical",
            "entities": {},
            "need_clarification": False,
        }
        out = validate_slots(analysis)
        assert out["need_clarification"] is True
        assert "target_table" in out["missing_fields"]

    def test_task_execution_critical_with_any_of_slots_passes(self):
        analysis = {
            "intent": "mutate",
            "intent_category": "task_execution",
            "risk_level": "critical",
            "entities": {"data_source": "ds_credit"},
            "need_clarification": False,
        }
        out = validate_slots(analysis)
        assert out["need_clarification"] is False

    def test_low_risk_missing_only_records_not_blocks(self):
        """低风险缺失：登记 missing_fields，不触发追问。"""
        analysis = {
            "intent": "query",
            "intent_category": "conn_test",
            "risk_level": "low",
            "entities": {},
            "need_clarification": False,
        }
        out = validate_slots(analysis)
        assert out["need_clarification"] is False
        assert "endpoint" in out["missing_fields"]

    def test_existing_clarification_message_preserved(self):
        analysis = {
            "intent": "mutate",
            "intent_category": "model_onboard",
            "risk_level": "high",
            "entities": {},
            "need_clarification": True,
            "clarification_message": "请提供模型接入地址",
        }
        out = validate_slots(analysis)
        assert out["clarification_message"] == "请提供模型接入地址"
        assert "model_name" in out["missing_fields"]

    def test_non_dict_passthrough(self):
        assert validate_slots(None) is None  # type: ignore[arg-type]
