"""工具结果剪枝与 spill 落盘（借鉴 dsh 的 toolResultPruner + spill seam）。

问题：工具产出超大文本（read_file 大文件 / http_get / grep 命中洪流）时，
工具循环在上下文注入层只做 `[:tool_loop_max_result_chars]` 头部截断——
尾部信息丢失，模型也无从取回全文。

策略（对齐 dsh，裁剪适配 EAIDE 单机形态）：
    1. 结果超阈值 → 全文持久化到 spill 目录（0600 私有），内联替换为
       「头预览 + 尾预览 + 定位符（read_file / grep 可取回）」；
    2. 落盘失败 → best-effort 退化为纯头尾剪枝（绝不让成功调用变失败）；
    3. 替换后内联长度固定 ≤ ~3400 字符，处于 loop 层注入预算之内，
       头尾与定位符不会被二次切掉。

边界与红线：
    - 仅处理 builtin 只读成功结果（ok=True 且 risk_level='read'）；
      写工具 / 待审批 / 失败结果一律不动；
    - MCP 工具不处理（副作用未知，与 L3 tool_cache 同保守立场）；
    - spill 文件可能含敏感数据（查询结果等），本地 0600 私有保存，
      不进任何 cache key。
"""

from __future__ import annotations

import logging
import os
import re
import time
from pathlib import Path

from agent.config import settings

logger = logging.getLogger(__name__)

#: 头 / 尾预览预算（合计 + 标记 ≈ 3400，处于 tool_loop_max_result_chars 预算内）
_HEAD_CHARS = 2200
_TAIL_CHARS = 900


def _spill_dir() -> Path:
    """每次读 settings —— 测试 chdir / monkeypatch 后立即生效。"""
    return Path(settings.tool_spill_dir)


def _safe_tool_label(tool_name: str) -> str:
    """工具名 → 文件名片段（仅保留安全字符，防路径注入）。"""
    return re.sub(r"[^A-Za-z0-9_-]", "_", tool_name)[:40] or "tool"


def spill_text(text: str, *, tool_name: str) -> str | None:
    """全文落盘（0600 私有）；返回文件路径，任何失败返回 None。"""
    try:
        directory = _spill_dir()
        directory.mkdir(parents=True, exist_ok=True)
        label = _safe_tool_label(tool_name)
        # O_EXCL 防预置符号链接重定向写入（对齐 dsh spill-local 语义）
        for attempt in range(3):
            name = f"{int(time.time() * 1000)}-{label}-{attempt}.txt"
            path = directory / name
            try:
                fd = os.open(str(path), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            except FileExistsError:
                continue
            try:
                os.write(fd, text.encode("utf-8", errors="replace"))
            finally:
                os.close(fd)
            return str(path)
        return None
    except Exception as exc:
        logger.debug("result_spill write failed: %s", exc)
        return None


def _build_replacement(text: str, spill_path: str | None) -> str:
    """头尾预览 + 中间省略标记（带/不带定位符两种形态）。"""
    omitted = len(text) - _HEAD_CHARS - _TAIL_CHARS
    if spill_path:
        marker = (
            f"\n...[中间省略 {omitted} 字符；全文已保存至 {spill_path}，"
            f"可用 read_file / grep 按需取回]...\n"
        )
    else:
        marker = f"\n...[中间省略 {omitted} 字符]...\n"
    return text[:_HEAD_CHARS] + marker + text[-_TAIL_CHARS:]


def apply_result_limits(result: dict, *, tool_name: str) -> dict:
    """对超大只读工具结果做 spill/剪枝；不适用时原样返回。

    幂等安全：已带 spill_path meta 的结果不再处理。
    """
    if not settings.tool_spill_enabled:
        return result
    if not result.get("ok") or result.get("awaiting_approval"):
        return result
    if result.get("risk_level", "read") != "read":
        return result
    content = result.get("result")
    if not isinstance(content, str) or len(content) <= settings.tool_spill_threshold_chars:
        return result
    meta = dict(result.get("meta") or {})
    if meta.get("spill_path") or meta.get("pruned"):
        return result

    spill_path = spill_text(content, tool_name=tool_name)
    replacement = _build_replacement(content, spill_path)
    new_meta = {
        **meta,
        "spilled_chars": len(content),
        "inline_chars": len(replacement),
    }
    if spill_path:
        new_meta["spill_path"] = spill_path
    else:
        new_meta["pruned"] = True  # 落盘失败的降级标记
    logger.info(
        "result_spill_applied tool=%s original=%d inline=%d spilled=%s",
        tool_name,
        len(content),
        len(replacement),
        bool(spill_path),
    )
    return {**result, "result": replacement, "meta": new_meta}
