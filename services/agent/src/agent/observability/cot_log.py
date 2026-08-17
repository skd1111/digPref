"""CoT 专用日志 —— 思维链 / 意图识别全链路汇聚到单个 logs/cot.log。

目的：排查意图识别与思维链问题时只看这一个文件。收录范围：
    - intent 节点（graph/nodes/intent）：原始输入 / 语义路由命中 / 最终意图
    - LMRouter analyze_intent / classify_intent 降级链轨迹
    - Ollama / private / local_small / mock 各后端的原始 LLM 输出与解析结果
    - JSON 解析重试（json_discipline）与降级链（fallback）事件
    - 语义向量路由（semantic_route）打分决策
    - 思维链步骤落库（trace/collector）

落盘位置解析与 agent.log 一致：优先工作目录 logs/（打包后 = 安装目录），
失败回退 ~/.eaide。日志文件只增不截断；best-effort，任何日志失败绝不影响主链路。
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

COT_LOGGER_NAME = "agent.cot"

#: 既有模块 logger —— 同一个 FileHandler 镜像进 cot.log（propagate 保留，agent.log 不受影响）
_ATTACH_LOGGERS: tuple[str, ...] = (
    "agent.graph.nodes.intent",
    "agent.llm.router",
    "agent.llm.ollama",
    "agent.llm.private_llm",
    "agent.llm.local_small",
    "agent.llm.mock",
    "agent.llm.fallback",
    "agent.llm.json_discipline",
    "agent.graph.semantic_route",
    "agent.trace.collector",
)

_LOG_FILE: Path | None = None
_ATTACHED = False


def _resolve_log_file() -> Path:
    global _LOG_FILE
    if _LOG_FILE is None:
        try:
            path = Path("logs") / "cot.log"
            path.parent.mkdir(parents=True, exist_ok=True)
        except OSError:
            path = Path.home() / ".eaide" / "cot.log"
            path.parent.mkdir(parents=True, exist_ok=True)
        _LOG_FILE = path
    return _LOG_FILE


def get_cot_logger() -> logging.Logger:
    """返回 CoT 专用 logger；首次调用惰性创建 FileHandler 并挂载到相关模块。"""
    global _ATTACHED
    lg = logging.getLogger(COT_LOGGER_NAME)
    if _ATTACHED:
        return lg
    try:
        handler = logging.FileHandler(_resolve_log_file(), encoding="utf-8")
        handler.setLevel(logging.DEBUG)
        handler.setFormatter(
            logging.Formatter(
                "%(asctime)s.%(msecs)03d [%(levelname)s] %(name)s: %(message)s",
                datefmt="%Y-%m-%d %H:%M:%S",
            )
        )
        lg.setLevel(logging.DEBUG)
        lg.addHandler(handler)
        for name in _ATTACH_LOGGERS:
            logging.getLogger(name).addHandler(handler)
        _ATTACHED = True
    except OSError:
        pass  # 日志初始化失败绝不影响主链路
    return lg


def cot(stage: str, **fields: Any) -> None:
    """写一条结构化 CoT 日志：`[stage] {"k": v, ...}`（分析用，保留全文）。

    stage: 链路阶段标识（如 "intent.start" / "analyze_intent.result"）。
    fields: 任意键值；dict/list 走 JSON 序列化，其他 str()；超长值不截断，
    意图识别分析需要看 LLM 原始输出全文。
    """
    lg = get_cot_logger()
    try:
        body = json.dumps(fields, ensure_ascii=False, default=str)
    except (TypeError, ValueError):
        body = str(fields)
    lg.info("[%s] %s", stage, body)
