"""Token 用量端点 —— 前端状态栏「Agent: 就绪」旁的实时速率 + 当日总量 + 调用次数 + 费用。

GET /llm/token-usage 返回：
    {
        "day": "2026-08-07",
        "window_seconds": 30,
        "rate_upload_per_s": 12.3,      # 上传（prompt）速率 tokens/s（近 30s 均值）
        "rate_download_per_s": 5.1,     # 下载（completion）速率 tokens/s
        "rate_calls_per_s": 0.2,        # 模型调用速率 次/s
        "today_upload_tokens": 12345,   # 当日上传总量（跨重启保留）
        "today_download_tokens": 6789,  # 当日下载总量
        "today_total_tokens": 19134,
        "today_call_count": 42,         # 当日模型调用次数（跨重启保留）
        "today_cost_total": 0.0192,     # 当日总费用（按模型管理 cost_per_1k_tokens 计，跨重启保留）
        "cost_by_model": {"gpt-4o": 0.0192}  # 当日按模型费用明细（进程内）
    }

前端轮询（2s）即可实时展示；不引入新 SSE 事件（避免三处同步负担）。
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter

from agent.llm.token_usage import get_token_usage_tracker

router = APIRouter(tags=["llm-usage"])


@router.get("/llm/token-usage")
async def token_usage() -> dict[str, Any]:
    """实时速率（区分上传/下载）+ 当日总量 + 调用次数 + 费用快照。"""
    return get_token_usage_tracker().snapshot()
