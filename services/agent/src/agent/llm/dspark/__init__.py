"""DSpark —— Phase 13 推测解码加速引擎（V0 决策层）。

V0 范围（仅决策，不真正调 llama.cpp 的 speculative_model）：
    - DSparkConfig：全局开关 + 草稿模型路径 + 短输出阈值
    - SpeculativePolicy：单任务类别的策略（4 mode × n_draft/draft_p_min）
    - load_speculative_policies()：YAML → Dict[task_category, SpeculativePolicy]
    - decide_dspark()：根据 task_category + max_tokens + 全局开关 + LMRouter 路由结果
      → 注入 4 字段到 RouteDecision（speculative_enabled / n_draft / draft_p_min / draft_model）

V1 才做：
    - 实际调 llama.cpp Llama() 的 speculative_model/n_draft/draft_p_min 参数
    - watchfiles 热加载
    - 草稿模型下载

铁律遵守：
    - `_LOCAL_ONLY_TASKS` 强制关闭 DSpark（即使 YAML 配了也覆盖）
    - 极短输出（max_tokens < short_output_threshold）跳过 DSpark
    - 草稿模型路径缺失 → 静默降级关闭
"""

from __future__ import annotations

from agent.llm.dspark.config import (
    DEFAULT_POLICIES,
    SPECULATIVE_OFF,
    DSparkConfig,
    SpeculativeMode,
    SpeculativePolicy,
)
from agent.llm.dspark.policy import (
    PolicyMap,
    decide_dspark,
    load_speculative_policies,
)

__all__ = [
    "DEFAULT_POLICIES",
    "SPECULATIVE_OFF",
    "DSparkConfig",
    "PolicyMap",
    "SpeculativeMode",
    "SpeculativePolicy",
    "decide_dspark",
    "load_speculative_policies",
]
