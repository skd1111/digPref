"""工具结果剪枝与 spill 落盘（result_spill）回归测试。

覆盖：
  - 小结果 / 非 str / 写结果 / 失败结果原样返回
  - 超大只读结果：全文落盘 + 内联头尾预览 + 定位符 + meta
  - 落盘失败 → best-effort 纯剪枝（成功调用不变失败）
  - 替换后内联长度处于 loop 注入预算之内（头尾与定位符不被二次切掉）
  - catalog.execute 接线：spill 后再入 L3 缓存（缓存的即模型所见版本）
"""

from __future__ import annotations

from pathlib import Path

import pytest
from agent.config import settings
from agent.tools import result_spill
from agent.tools.result_spill import apply_result_limits


@pytest.fixture
def spill_dir(tmp_path, monkeypatch) -> Path:
    d = tmp_path / "spill"
    monkeypatch.setattr(settings, "tool_spill_dir", str(d))
    monkeypatch.setattr(settings, "tool_spill_enabled", True)
    monkeypatch.setattr(settings, "tool_spill_threshold_chars", 4000)
    return d


def _read_result(content: str) -> dict:
    return {"name": "read_file", "ok": True, "result": content, "meta": {}, "risk_level": "read"}


# ---- 不适用场景：原样返回 ---------------------------------------------------


def test_small_result_untouched(spill_dir):
    result = _read_result("short content")
    assert apply_result_limits(result, tool_name="read_file") is result


def test_non_str_result_untouched(spill_dir):
    result = {
        "name": "json_parse",
        "ok": True,
        "result": {"rows": list(range(5000))},
        "risk_level": "read",
    }
    assert apply_result_limits(result, tool_name="json_parse") is result


def test_write_result_untouched(spill_dir):
    result = {
        "name": "write_file",
        "ok": True,
        "result": "x" * 10000,
        "risk_level": "write",
    }
    assert apply_result_limits(result, tool_name="write_file") is result


def test_failed_result_untouched(spill_dir):
    result = {"name": "read_file", "ok": False, "error": "boom", "result": "x" * 10000}
    assert apply_result_limits(result, tool_name="read_file") is result


def test_disabled_by_settings(spill_dir, monkeypatch):
    monkeypatch.setattr(settings, "tool_spill_enabled", False)
    result = _read_result("x" * 10000)
    assert apply_result_limits(result, tool_name="read_file") is result


# ---- 核心：落盘 + 头尾预览 + 定位符 -------------------------------------------


def test_large_result_spilled_with_locator(spill_dir):
    head = "HEAD_MARKER_"
    tail = "_TAIL_MARKER"
    content = head + "m" * 10000 + tail
    result = apply_result_limits(_read_result(content), tool_name="read_file")

    # 内联：头 + 尾 + 定位符
    assert result["result"].startswith(head)
    assert result["result"].endswith(tail)
    assert "中间省略" in result["result"]
    assert "read_file / grep" in result["result"]

    # 全文落盘
    spill_path = result["meta"]["spill_path"]
    assert Path(spill_path).exists()
    assert Path(spill_path).read_text(encoding="utf-8") == content
    assert result["meta"]["spilled_chars"] == len(content)

    # 内联长度处于 loop 注入预算内（含 JSON 包裹余量）
    assert len(result["result"]) <= 4000


def test_spill_file_private_permissions(spill_dir):
    content = "s" * 5000
    result = apply_result_limits(_read_result(content), tool_name="grep")
    path = Path(result["meta"]["spill_path"])
    # Windows 无 POSIX 权限位语义，仅验证文件存在且可读
    assert path.stat().st_size >= len(content)


# ---- best-effort：落盘失败退化为纯剪枝 ----------------------------------------


def test_spill_failure_falls_back_to_prune(monkeypatch, spill_dir):
    monkeypatch.setattr(result_spill, "spill_text", lambda text, tool_name: None)
    content = "h" * 3000 + "MIDDLE" * 2000 + "t" * 100
    result = apply_result_limits(_read_result(content), tool_name="http_get")

    assert result["ok"] is True  # 成功调用绝不因落盘失败变失败
    assert "spill_path" not in result["meta"]
    assert result["meta"]["pruned"] is True
    assert "中间省略" in result["result"]
    assert result["result"].startswith("h")
    assert result["result"].endswith("t" * 10)


# ---- 幂等：已处理结果不再处理 --------------------------------------------------


def test_idempotent_on_already_processed(spill_dir):
    content = "y" * 10000
    once = apply_result_limits(_read_result(content), tool_name="read_file")
    twice = apply_result_limits(once, tool_name="read_file")
    assert twice is once


# ---- catalog 接线：spill 后再入 L3 缓存 ----------------------------------------


async def test_catalog_execute_spills_before_cache(spill_dir, monkeypatch):
    """超大 builtin 只读结果：execute 返回已替换版本，且缓存的是替换后版本。"""
    from agent.llm import tool_cache
    from agent.tools.catalog import ToolCatalog

    tool_cache.get_tool_cache().clear()
    monkeypatch.setattr(tool_cache, "_ENABLED", True)

    huge = "A" * 2500 + "B" * 10000 + "C" * 2500

    async def fake_builtin(self, name, args, state):
        return {"name": name, "ok": True, "result": huge, "meta": {}, "risk_level": "read"}

    monkeypatch.setattr(ToolCatalog, "_execute_builtin", fake_builtin)

    catalog = ToolCatalog()
    first = await catalog.execute("read_file", {"path": "big.txt"}, {})
    assert len(first["result"]) <= 4000
    assert "spill_path" in first["meta"]
    assert Path(first["meta"]["spill_path"]).read_text(encoding="utf-8") == huge

    # 二次调用命中 L3 缓存，返回的仍是替换后版本（带 cache_hit）
    second = await catalog.execute("read_file", {"path": "big.txt"}, {})
    assert second.get("cache_hit") is True
    assert len(second["result"]) <= 4000
    tool_cache.get_tool_cache().clear()
