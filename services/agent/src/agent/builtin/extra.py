"""Phase 1B V3 · 新增常用工具（纯 Python，低风险，离线 / 受控 I/O）。

工具清单：
    - datetime_now  当前时间 / 日期（默认本地时区，含农历 / 星期）
    - date_parse    相对时间表达 → 绝对日期（今天/明天/下周一/最近三天/本月底）
    - uuid4         生成 v4 UUID
    - http_get      HTTP GET（仅 http/https，限超时与响应大小）
    - http_post     HTTP POST（medium 风险，受写操作 HITL 治理）
    - csv_parse     CSV 文本解析（可带表头 / 自定义分隔符 / 行数上限）
    - text_split    长文本按字符数 / 分隔符切分（配合大文件分段处理）
"""

from __future__ import annotations

import calendar as _calendar
import csv as _csv
import io
import re
import uuid
from datetime import date, datetime, timedelta, timezone
from typing import Any

import httpx

from agent.builtin.models import ToolResult
from agent.config import settings

_WEEKDAY_CN = ("星期一", "星期二", "星期三", "星期四", "星期五", "星期六", "星期日")


def _lunar_text(dt: datetime) -> str | None:
    """公历 → 农历中文描述（如「二零二六年正月初一」）；zhdate 缺失返 None。"""
    try:
        from zhdate import ZhDate  # type: ignore[import-untyped]  # 可选依赖，延迟导入
    except ImportError:
        return None
    try:
        return str(ZhDate.from_datetime(dt).chinese())
    except Exception:
        return None


def builtin_datetime_now(
    *,
    iso: bool = True,
    tz_offset_hours: float | None = None,
    include_lunar: bool = True,
) -> ToolResult:
    """当前时间 / 日期。默认用系统本地时区；tz_offset_hours 可指定 UTC 偏移（东八区 = 8）。

    返回内容含星期与农历（农历依赖可选包 zhdate，缺失时自动省略）。
    """
    try:
        tz = (
            timezone(timedelta(hours=tz_offset_hours))
            if tz_offset_hours is not None
            else None  # None → 系统本地时区（修复：旧版默认 UTC 与用户认知不符）
        )
        now = datetime.now(tz) if tz is not None else datetime.now().astimezone()
        content: dict[str, Any] = {
            "datetime": now.isoformat() if iso else now.strftime("%Y-%m-%d %H:%M:%S"),
            "date": now.strftime("%Y-%m-%d"),
            "weekday": _WEEKDAY_CN[now.weekday()],
        }
        meta: dict[str, Any] = {
            "iso": iso,
            "tz_offset_hours": tz_offset_hours,
            "timestamp": now.timestamp(),
            "utc_offset": now.strftime("%z"),
        }
        if include_lunar:
            lunar = _lunar_text(now.replace(tzinfo=None))
            if lunar:
                content["lunar"] = lunar
                meta["lunar_available"] = True
            else:
                meta["lunar_available"] = False
        return ToolResult(
            ok=True,
            content=content,
            meta=meta,
            risk_level="low",
        )
    except Exception as exc:
        return ToolResult.from_exception(exc, risk_level="low")


_CN_NUM = {
    "一": 1,
    "两": 2,
    "二": 2,
    "三": 3,
    "四": 4,
    "五": 5,
    "六": 6,
    "七": 7,
    "八": 8,
    "九": 9,
    "十": 10,
}
_WEEKDAY_SHORT = {0: "周一", 1: "周二", 2: "周三", 3: "周四", 4: "周五", 5: "周六", 6: "周日"}
_WEEKDAY_NAME = {"一": 0, "二": 1, "三": 2, "四": 3, "五": 4, "六": 5, "日": 6, "天": 6}
_REL_DAY_OFFSET = {
    "今天": 0,
    "今日": 0,
    "明天": 1,
    "明日": 1,
    "后天": 2,
    "大后天": 3,
    "昨天": -1,
    "昨日": -1,
    "前天": -2,
    "大前天": -3,
}


def _parse_cn_number(text: str) -> int | None:
    """解析 1~30 内的中文 / 阿拉伯数字（三 / 3 / 十五）。"""
    if text.isdigit():
        return int(text)
    if text in _CN_NUM:
        return _CN_NUM[text]
    m = re.fullmatch(r"十([一二三四五六七八九])?", text)
    if m:
        return 10 + (_CN_NUM.get(m.group(1) or "", 0))
    m = re.fullmatch(r"([一二三四五六七八九])十", text)
    if m:
        return _CN_NUM[m.group(1)] * 10
    m = re.fullmatch(r"([一二三四五六七八九])十([一二三四五六七八九])", text)
    if m:
        return _CN_NUM[m.group(1)] * 10 + _CN_NUM[m.group(2)]
    return None


def _date_result(expr: str, d: date, *, note: str = "") -> dict[str, Any]:
    out: dict[str, Any] = {
        "expression": expr,
        "type": "date",
        "date": d.isoformat(),
        "weekday": _WEEKDAY_SHORT[d.weekday()],
    }
    if note:
        out["note"] = note
    return out


def _range_result(expr: str, start: date, end: date) -> dict[str, Any]:
    return {
        "expression": expr,
        "type": "range",
        "start": start.isoformat(),
        "end": end.isoformat(),
        "days": (end - start).days + 1,
    }


def builtin_date_parse(*, expression: str, base_date: str | None = None) -> ToolResult:
    """把中文相对时间表达解析为绝对日期（YYYY-MM-DD）。

    支持：今天/明天/后天/大后天/昨天/前天、N 天前/后、周一~周日（本周/下周）、
    周末、最近 N 天（区间）、本月底/月底、显式 YYYY-MM-DD 透传。
    base_date 可选（YYYY-MM-DD），缺省用系统本地今天。
    无法解析时 ok=False + hint（调用方应追问用户具体日期，不得猜测）。
    """
    expr = (expression or "").strip()
    if not expr:
        return ToolResult(ok=False, error="empty_expression", risk_level="low")
    try:
        today = date.fromisoformat(base_date) if base_date else datetime.now().astimezone().date()
    except ValueError:
        return ToolResult(
            ok=False,
            error="invalid_base_date",
            hint="base_date 格式必须为 YYYY-MM-DD",
            risk_level="low",
        )

    # 1) 显式日期透传
    if re.fullmatch(r"\d{4}-\d{1,2}-\d{1,2}", expr):
        try:
            d = date.fromisoformat(expr)
            return ToolResult(ok=True, content=_date_result(expr, d), risk_level="low")
        except ValueError:
            return ToolResult(ok=False, error="invalid_date", risk_level="low")

    # 2) 相对天：今天 / 明天 / 后天 / 大后天 / 昨天 / 前天
    if expr in _REL_DAY_OFFSET:
        d = today + timedelta(days=_REL_DAY_OFFSET[expr])
        return ToolResult(ok=True, content=_date_result(expr, d), risk_level="low")

    # 3) N 天前 / N 天后
    m = re.fullmatch(r"([一二两三四五六七八九十0-9]+)天(前|后)", expr)
    if m:
        n = _parse_cn_number(m.group(1))
        if n:
            offset = -n if m.group(2) == "前" else n
            d = today + timedelta(days=offset)
            return ToolResult(ok=True, content=_date_result(expr, d), risk_level="low")

    # 4) 周几：本周/下周/上周 + 周一~周日
    m = re.fullmatch(r"(本|这|下|上)?(周|星期|礼拜)([一二三四五六日天])", expr)
    if m:
        prefix, target_wd = m.group(1) or "本", _WEEKDAY_NAME[m.group(3)]
        delta = target_wd - today.weekday()
        if prefix == "下":
            delta += 7
        elif prefix == "上":
            delta -= 7
        elif delta < 0:
            # 「周三」而今天已过周三 → 默认指下周三（避免解析出过去日期）
            delta += 7
        d = today + timedelta(days=delta)
        return ToolResult(ok=True, content=_date_result(expr, d), risk_level="low")

    # 5) 周末 → 下一个周六至周日区间
    if expr in ("周末", "这个周末", "本周末"):
        delta_sat = (5 - today.weekday()) % 7
        if today.weekday() >= 6:  # 已在周末 → 指当前周末
            delta_sat = 5 - today.weekday()
        sat = today + timedelta(days=delta_sat)
        return ToolResult(
            ok=True, content=_range_result(expr, sat, sat + timedelta(days=1)), risk_level="low"
        )

    # 6) 最近/近 N 天 → 区间（含今天）
    m = re.fullmatch(r"(最近|近)([一二两三四五六七八九十0-9]+)天", expr)
    if m:
        n = _parse_cn_number(m.group(2))
        if n:
            start = today - timedelta(days=n - 1)
            return ToolResult(ok=True, content=_range_result(expr, start, today), risk_level="low")

    # 7) 本月底 / 月底 / 下月底
    m = re.fullmatch(r"(本|这|下)?月底", expr)
    if m:
        prefix = m.group(1) or "本"
        year, month = (
            (today.year, today.month)
            if prefix != "下"
            else ((today.year + 1, 1) if today.month == 12 else (today.year, today.month + 1))
        )
        d = date(year, month, _calendar.monthrange(year, month)[1])
        return ToolResult(ok=True, content=_date_result(expr, d), risk_level="low")

    # 无法解析 → 返错 + 追问提示（严禁上游猜测日期）
    return ToolResult(
        ok=False,
        error="unparsable_expression",
        hint="无法解析该时间表达，请追问用户提供具体日期（YYYY-MM-DD）",
        risk_level="low",
    )


def builtin_uuid4() -> ToolResult:
    """生成一个 v4 UUID 字符串。"""
    try:
        return ToolResult(
            ok=True,
            content=str(uuid.uuid4()),
            meta={"version": 4},
            risk_level="low",
        )
    except Exception as exc:
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
    except Exception as exc:
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
    except Exception as exc:
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
    except Exception as exc:
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
            chunks = [text[i : i + max_chars] for i in range(0, len(text), max_chars)]
        return ToolResult(
            ok=True,
            content=chunks,
            meta={"chunks": len(chunks), "max_chars": max_chars},
            risk_level="low",
        )
    except Exception as exc:
        return ToolResult.from_exception(exc, risk_level="low")
