"""Phase 4 V0: Router inference mode tests."""
from __future__ import annotations

import pytest


class TestInferenceMode:
    """推理模式切换测试。"""

    def test_default_mode_is_normal(self):
        from agent.llm.router import LMRouter
        r = LMRouter()
        assert r.inference_mode == "normal"

    def test_set_performance_mode(self):
        from agent.llm.router import LMRouter
        r = LMRouter()
        r.set_inference_mode("performance")
        assert r.inference_mode == "performance"

    def test_set_normal_mode(self):
        from agent.llm.router import LMRouter
        r = LMRouter()
        r.set_inference_mode("performance")
        r.set_inference_mode("normal")
        assert r.inference_mode == "normal"

    def test_invalid_mode_raises(self):
        from agent.llm.router import LMRouter
        r = LMRouter()
        with pytest.raises(ValueError):
            r.set_inference_mode("invalid")  # type: ignore[arg-type]


class TestNormalModeChain:
    """正常模式：端侧优先。"""

    def test_intent_chain_starts_with_local_small(self):
        from agent.llm.router import LMRouter
        r = LMRouter()
        r.set_inference_mode("normal")
        chain = r._chain_for("intent")
        assert chain[0][0] == "local_small"

    def test_plan_chain_starts_with_local_small(self):
        from agent.llm.router import LMRouter
        r = LMRouter()
        r.set_inference_mode("normal")
        chain = r._chain_for("plan")
        assert chain[0][0] == "local_small"

    def test_chain_ends_with_mock(self):
        from agent.llm.router import LMRouter
        r = LMRouter()
        for kind in ("intent", "plan", "repair", "summarise"):
            chain = r._chain_for(kind)
            assert chain[-1][0] == "mock", f"{kind} chain should end with mock"


class TestPerformanceModeChain:
    """性能模式：跳过端侧。"""

    def test_intent_chain_skips_local_small(self):
        from agent.llm.router import LMRouter
        r = LMRouter()
        r.set_inference_mode("performance")
        chain = r._chain_for("intent")
        assert chain[0][0] == "ollama"

    def test_plan_chain_skips_local_small(self):
        from agent.llm.router import LMRouter
        r = LMRouter()
        r.set_inference_mode("performance")
        chain = r._chain_for("plan")
        assert chain[0][0] == "ollama"


class TestLocalOnlyTasks:
    """红线：_LOCAL_ONLY_TASKS 包含 Phase 4 新增任务。"""

    def test_local_intent_is_local_only(self):
        from agent.llm.router import _LOCAL_ONLY_TASKS
        assert "local_intent" in _LOCAL_ONLY_TASKS

    def test_vision_understand_is_local_only(self):
        from agent.llm.router import _LOCAL_ONLY_TASKS
        assert "vision_understand" in _LOCAL_ONLY_TASKS
