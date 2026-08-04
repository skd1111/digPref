"""Phase 1B V3 · 新增常用工具（纯 Python，低风险，离线 / 受控 I/O）。

工具清单：
    - datetime_now  当前时间 / 日期（可选 UTC 偏移）
    - uuid4         生成 v4 UUID
    - http_get      HTTP GET（仅 http/https，限超时与响应大小）
    - http_post     HTTP POST（medium 风险，受写操作 HITL 治理）
    - csv_parse     CSV 文本解析（可带表头 / 自定义分隔符 / 行数上限）
    - text_split    长文本按字符数 / 分隔符切分（配合大文件分段处理）
"""
from __future__ import annotations

import csv as _csv
import io
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

import httpx

from agent.builtin.models import ToolResult
from agent.config import settings


def builtin_datetime_now(
    *,
    iso: bool = True,
    tz_offset_hours: float | None = None,
) -> ToolResult:
    """当前时间 / 日期。tz_offset_hours 为 UTC 偏移（东八区 = 8）。"""
    try:
        tz = (
            timezone(timedelta(hours=tz_offset_hours))
            if tz_offset_hours is not None
            else timezone.utc
        )
        now = datetime.now(tz)
        content = now.isoformat() if iso else now.strftime("%Y-%m-%d %H:%M:%S")
        return ToolResult(
            ok=True,
            content=content,
            meta={
                "iso": iso,
                "tz_offset_hours": tz_offset_hours,
                "timestamp": now.timestamp(),
            },
            risk_level="low",
        )
    except Exception as exc:  # noqa: BLE001
        return ToolResult.from_exception(exc, risk_level="low")


def builtin_uuid4() -> ToolResult:
    """生成一个 v4 UUID 字符串。"""
    try:
        return ToolResult(
            ok=True,
            content=str(uuid.uuid4()),
            meta={"version": 4},
            risk_level="low",
        )
    except Exception as exc:  # noqa: BLE001
        return ToolResult.from_exception(exc, risk_level="low")


async def builtin_http_get(
    *,
    url: str,
    timeout_sec: float | None = None,
    max_bytes: int | None = None,
    headers: dict[str, Any] | None = None,
) -> ToolResult:
    """HTTP GET 请求。仅允许 http/https，超时与响应大小受配置约束。"""
    if not isinstance(url, str) or not url.lower().startswith(("http://", "https://")):
        return ToolResult(
            ok=False,
            error="invalid_url",
            hint="仅支持 http:// 或 https:// URL",
            risk_level="low",
        )
    timeout = timeout_sec or settings.builtin_http_timeout_sec
    limit = max_bytes or settings.builtin_http_max_bytes
    try:
        async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
            resp = await client.get(url, headers=dict(headers or {}))
        body = resp.content
        truncated = len(body) > limit
        text = body[:limit].decode("utf-8", errors="replace")
        return ToolResult(
            ok=resp.status_code < 400,
            content={
                "status_code": resp.status_code,
                "headers": dict(list(resp.headers.items())[:20]),
                "body": text,
                "truncated": truncated,
                "bytes": len(body),
            },
            meta={
                "status_code": resp.status_code,
                "bytes": len(body),
                "truncated": truncated,
            },
            risk_level="low",
        )
    except Exception as exc:  # noqa: BLE001
        return ToolResult.from_exception(exc, risk_level="low")


async def builtin_http_post(
    *,
    url: str,
    json_body: dict[str, Any] | None = None,
    form_data: dict[str, Any] | None = None,
    headers: dict[str, Any] | None = None,
    timeout_sec: float | None = None,
    max_bytes: int | None = None,
) -> ToolResult:
    """HTTP POST 请求（业务 API 写调用）。

    json_body 与 form_data 二选一（同时提供时优先 json_body）。
    风险等级 medium（会改变外部系统状态）→ dispatcher 按
    require_hitl_for_write 走 HITL 审批。
    """
    if not isinstance(url, str) or not url.lower().startswith(("http://", "https://")):
        return ToolResult(
            ok=False,
            error="invalid_url",
            hint="仅支持 http:// 或 https:// URL",
            risk_level="medium",
        )
    if json_body is None and form_data is None:
        return ToolResult(
            ok=False,
            error="missing_body",
            hint="需要 json_body 或 form_data 之一",
            risk_level="medium",
        )
    timeout = timeout_sec or settings.builtin_http_timeout_sec
    limit = max_bytes or settings.builtin_http_max_bytes
    try:
        async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
            if json_body is not None:
                resp = await client.post(url, json=json_body, headers=dict(headers or {}))
            else:
                resp = await client.post(url, data=form_data, headers=dict(headers or {}))
        body = resp.content
        truncated = len(body) > limit
        text = body[:limit].decode("utf-8", errors="replace")
        return ToolResult(
            ok=resp.status_code < 400,
            content={
                "status_code": resp.status_code,
                "headers": dict(list(resp.headers.items())[:20]),
                "body": text,
                "truncated": truncated,
                "bytes": len(body),
            },
            meta={
                "status_code": resp.status_code,
                "bytes": len(body),
                "truncated": truncated,
            },
            risk_level="medium",
        )
    except Exception as exc:  # noqa: BLE001
        return ToolResult.from_exception(exc, risk_level="medium")


def builtin_csv_parse(
    *,
    text: str,
    delimiter: str = ",",
    has_header: bool = False,
    max_rows: int = 1000,
) -> ToolResult:
    """解析 CSV 文本为行数组（可选表头行）。"""
    try:
        rows: list[list[str]] = []
        header: list[str] | None = None
        for i, row in enumerate(_csv.reader(io.StringIO(text), delimiter=delimiter)):
            if has_header and i == 0:
                header = row
                continue
            if max_rows and len(rows) >= max_rows:
                break
            rows.append(row)
        content: dict[str, Any] = {
            "rows": rows,
            "row_count": len(rows),
            "truncated": bool(max_rows) and len(rows) >= max_rows,
        }
        if header is not None:
            content["header"] = header
        return ToolResult(
            ok=True,
            content=content,
            meta={"row_count": len(rows), "has_header": has_header},
            risk_level="low",
        )
    except Exception as exc:  # noqa: BLE001
        return ToolResult.from_exception(exc, risk_level="low")


def builtin_text_split(
    *,
    text: str,
    max_chars: int = 2000,
    separator: str | None = None,
) -> ToolResult:
    """把长文本切分为多段（优先按 separator 分块，保持段不超过 max_chars）。"""
    try:
        if not text:
            return ToolResult(ok=True, content=[], meta={"chunks": 0}, risk_level="low")
        if separator and separator in text:
            chunks: list[str] = []
            current = ""
            for part in text.split(separator):
                piece = (separator if current else "") + part
                if current and len(current) + len(piece) > max_chars:
                    chunks.append(current)
                    current = part
                else:
                    current = piece
            if current:
                chunks.append(current)
        else:
            chunks = [
                text[i : i + max_chars]
                for i in range(0, len(text), max_chars)
            ]
        return ToolResult(
            ok=True,
            content=chunks,
            meta={"chunks": len(chunks), "max_chars": max_chars},
            risk_level="low",
        )
    except Exception as exc:  # noqa: BLE001
        return ToolResult.from_exception(exc, risk_level="low")
