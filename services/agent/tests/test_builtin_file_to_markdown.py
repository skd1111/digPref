"""Phase 1B V5 · file_to_markdown 工具测试（markitdown 文件转 Markdown）。

转换类测试依赖 markitdown（进程内库或外部 CLI），两者皆不可用时自动 skip，
保证 CI（无外部 venv）不失败。
"""

from __future__ import annotations

from pathlib import Path

import pytest
from agent.config import settings


def _markitdown_backend_available() -> bool:
    try:
        import markitdown  # noqa: F401

        return True
    except ImportError:
        pass
    exe = settings.builtin_markitdown_executable
    return bool(exe) and Path(exe).exists()


needs_markitdown = pytest.mark.skipif(
    not _markitdown_backend_available(),
    reason="markitdown 库与外部 CLI 均不可用",
)


class TestRegistration:
    """工具注册 / schema / 风险等级（无外部依赖，CI 必过）。"""

    def test_in_builtin_tool_names(self):
        from agent.builtin.models import BUILTIN_TOOL_NAMES

        assert "file_to_markdown" in BUILTIN_TOOL_NAMES

    def test_schema_defined(self):
        from agent.builtin.schemas import get_builtin_schema

        schema = get_builtin_schema("file_to_markdown")
        assert schema is not None
        assert schema["required"] == ["path"]
        assert "path" in schema["properties"]

    def test_risk_level_read(self):
        from agent.builtin.registry import TOOL_RISK_LEVEL

        assert TOOL_RISK_LEVEL["file_to_markdown"] == "read"

    def test_registry_has_tool_and_description(self):
        from agent.builtin.registry import TOOL_DESCRIPTIONS, get_default_registry

        reg = get_default_registry()
        assert reg.has("file_to_markdown")
        assert callable(reg.get("file_to_markdown"))
        assert "file_to_markdown" in TOOL_DESCRIPTIONS

    def test_package_export(self):
        from agent.builtin import builtin_file_to_markdown

        assert callable(builtin_file_to_markdown)


class TestErrors:
    """参数与文件校验（不触达 markitdown）。"""

    def test_empty_path(self):
        from agent.builtin.markdown_convert import builtin_file_to_markdown

        result = builtin_file_to_markdown(path="")
        assert result.ok is False
        assert result.error == "empty_path"

    def test_not_found(self):
        from agent.builtin.markdown_convert import builtin_file_to_markdown

        result = builtin_file_to_markdown(path="/nonexistent/definitely_missing.docx")
        assert result.ok is False
        assert result.error == "not_found"

    def test_library_missing_and_cli_unconfigured(self, tmp_path, monkeypatch):
        """库不可用且未配置外部 CLI → 明确报错而非隐式失败。"""
        import agent.builtin.markdown_convert as mc

        monkeypatch.setattr(settings, "builtin_markitdown_executable", "")

        def _no_library(_path: str) -> str:
            raise ImportError("markitdown")

        monkeypatch.setattr(mc, "_library_convert_impl", _no_library)
        sample = tmp_path / "a.html"
        sample.write_text("<p>x</p>", encoding="utf-8")
        result = mc.builtin_file_to_markdown(path=str(sample))
        assert result.ok is False
        assert result.error == "markitdown_unavailable"
        assert "uv sync" in (result.hint or "")

    def test_library_hang_does_not_block(self, tmp_path, monkeypatch):
        """库转换卡死时超时强制返回 timed_out，不阻塞主流程。"""
        import time

        import agent.builtin.markdown_convert as mc

        monkeypatch.setattr(settings, "builtin_markitdown_executable", "")

        def _hang(_path: str) -> str:
            time.sleep(5)
            return "unreachable"

        monkeypatch.setattr(mc, "_library_convert_impl", _hang)
        sample = tmp_path / "b.html"
        sample.write_text("<p>x</p>", encoding="utf-8")
        started = time.monotonic()
        result = mc.builtin_file_to_markdown(path=str(sample), timeout_sec=0.3)
        elapsed = time.monotonic() - started
        assert result.ok is False
        assert result.error == "timed_out"
        assert elapsed < 3, f"工具卡死阻塞了主流程：{elapsed:.1f}s"


@needs_markitdown
class TestConvert:
    """真实转换（HTML 样本；依赖进程内库或外部 CLI）。"""

    def test_convert_html_file(self, tmp_path: Path):
        from agent.builtin.markdown_convert import builtin_file_to_markdown

        html = tmp_path / "sample.html"
        html.write_text(
            "<html><body><h1>标题</h1><p>这是正文内容。</p></body></html>",
            encoding="utf-8",
        )
        result = builtin_file_to_markdown(path=str(html))
        assert result.ok is True, result.error
        assert "标题" in result.content
        assert "这是正文内容。" in result.content
        assert result.meta["backend"] in ("library", "cli")
        assert result.meta["chars"] == len(result.content)

    @pytest.mark.asyncio
    async def test_dispatch_integration(self, tmp_path: Path, monkeypatch):
        """经 dispatcher 调度（含审计写入 tmp_path）。"""
        from agent.builtin.dispatcher import dispatcher
        from agent.builtin.registry import reset_default_registry

        reset_default_registry()
        monkeypatch.setattr(settings, "audit_db_path", str(tmp_path / "audit.sqlite"))

        html = tmp_path / "doc.html"
        html.write_text(
            "<html><body><h2>合同要点</h2><p>付款周期 30 天。</p></body></html>", encoding="utf-8"
        )
        result = await dispatcher().dispatch(
            {
                "server": "builtin",
                "name": "file_to_markdown",
                "args": {"path": str(html)},
            },
            {"run_id": "test-file-to-markdown"},
        )
        assert result["tool_result"]["ok"] is True
        assert "合同要点" in result["tool_result"]["content"]
        assert result["tool_error"] is None
