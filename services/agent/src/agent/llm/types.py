"""Shared LLM types — defined here to avoid circular imports between
router.py and ollama.py / private_llm.py.

Phase 2G V1.1 (2026-07-28): TaskKind Literal 扩展加入 `biznav_extract`，
让 `_LOCAL_ONLY_TASKS` 的 frozenset 类型签名与实际值一致（不出现
`frozenset[TaskKind]` 含未声明字面量的"伪类型错误"）。
"""
from __future__ import annotations

from typing import Literal


Intent = Literal["query", "mutate", "orchestrate", "chitchat"]
TaskKind = Literal[
    "intent",
    "plan",
    # Phase 12 V2: 自动多智能体分解判定（接触用户原始内容 → 本地红线）
    "decompose",
    # 动态工具路由与调用编排（接触用户内容 + 工具结果 → 本地红线）
    "tool_orchestrate",
    "repair",
    "summarise",
    "toolspec",
    # Phase 2D V0: skill 路由（关键词命中 + LLM 兜底意图分类）
    "skill_router",
    # Phase 12 V0: 数据摘要
    "data_summary",
    # Phase 2G V1.1 (2026-07-28): 业务功能点提取
    "biznav_extract",
    # Phase 4 V0: 本地端侧任务
    "local_intent",
    "vision_understand",
    # Phase 2F+ V1 (2026-07-29): 日志级别分类
    "log_level_classify",
    # Phase 1B V1 (2026-07-30): 原生工具产出物汇总
    "builtin_tool_summary",
    "builtin_search_summarize",
    # Phase 14 V0 (2026-07-31): 图像处理 OCR 文本
    "image_processing_summary",
    # Phase 2B V0 (2026-07-31): SSH 命令输出
    "ssh_command_summary",
    # Phase 7 预留 (2026-07-31): 数据专家敏感任务
    "schema_link",
    "chart_reco",
    # 文档风险合规审核（2026-08-04）：文档分类 / 风险分析（路由链可配置，允许云端）
    "doc_classify",
    "doc_analyze",
    # mock 模式标记（非真实任务，不走 LLM 调度）
    "mock_mode",
]    # noqa: E501
