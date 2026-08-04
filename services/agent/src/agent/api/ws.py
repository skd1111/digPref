"""/ws endpoint —— 双向 WebSocket，为未来实时特性打底。

设计灵感来自 VSCode extension host protocol：客户端与 Agent 之间
通过全双工通道交换 JSON 消息，连接保持直到客户端主动断开。

当前阶段：基础回声 + 时间戳，验证双向通道可用。后续版本将
  1. 复用 SSE 适配器，把 agent:// 事件流推送到 WebSocket；
  2. 支持 WebSocket 通道接收 approval 决策（替代 HTTP POST /approval）。
"""
from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

router = APIRouter(tags=["ws"])


@router.websocket("/ws")
async def ws_endpoint(websocket: WebSocket) -> None:
    await websocket.accept()

    # 握手：告知客户端连接已建立
    await websocket.send_json({
        "event": "ready",
        "timestamp": _utc_now_iso(),
        "protocol_version": "0.1.0",
    })

    # 进入双向消息循环，直到客户端断开
    try:
        async for raw in websocket.iter_text():
            try:
                payload = json.loads(raw)
            except json.JSONDecodeError:
                await websocket.send_json({
                    "event": "error",
                    "message": "消息必须是合法 JSON",
                    "timestamp": _utc_now_iso(),
                })
                continue

            # 当前阶段：回声 + 时间戳，验证通道可用
            msg_type = payload.get("type", "echo")
            if msg_type == "ping":
                await websocket.send_json({
                    "event": "pong",
                    "timestamp": _utc_now_iso(),
                })
            else:
                await websocket.send_json({
                    "event": "echo",
                    "original": payload,
                    "timestamp": _utc_now_iso(),
                })
    except WebSocketDisconnect:
        # 客户端正常断开，无需额外清理
        pass


def _utc_now_iso() -> str:
    """返回 UTC 时间的 ISO 8601 字符串，统一全站时间格式。"""
    return datetime.now(timezone.utc).isoformat()
