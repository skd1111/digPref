"""工具循环「发散刹车」回归测试（Fix 3，根因修复 2026-09-04）。

现象：知识库已命中，但模型进工具循环后用 shell 跨盘符 dir C:/D:/E:/F:、glob、
find 到处翻找，每轮命令都不同、都 ok=True → 现有「连续完全相同」的重复熔断与
「零成功」的停滞熔断都拦不住，24 轮预算慢慢烧、看上去卡死。修复：统计只读文件
探测（dir/ls/glob/find/list_dir/grep）次数与不同目标数，超软阈注入强制收敛指令、
超硬阈直接停并给出「去知识库/告知位置」的可操作终答；另加单 run 墙钟超时。
"""

from __future__ import annotations

from agent.config import settings
from agent.tools.loop import (
    DynamicToolLoop,
    _fs_divergence_msg,
    _fs_probe_target,
    _is_fs_probe,
)


class TestIsFsProbe:
    def test_shell_dir_commands(self):
        assert _is_fs_probe("shell", {"command": "dir C:\\"})
        assert _is_fs_probe("shell", {"command": "Get-ChildItem D:\\git"})
        assert _is_fs_probe("shell", {"command": "ls -la ~"})
        assert _is_fs_probe("shell", {"argv": ["ls", "-la", "~"]})

    def test_shell_non_probe(self):
        assert not _is_fs_probe("shell", {"command": "echo hello"})
        assert not _is_fs_probe("shell", {"command": "python build.py"})

    def test_dedicated_probe_tools(self):
        assert _is_fs_probe("list_dir", {"path": "/x"})
        assert _is_fs_probe("glob", {"pattern": "**/*.md"})
        assert _is_fs_probe("find", {"path": "/x", "pattern": "*"})

    def test_read_and_write_not_probe(self):
        # 读文件内容是 coding 任务的合法动作，不算发散扫描
        assert not _is_fs_probe("read_file", {"path": "/x.py"})
        assert not _is_fs_probe("write_file", {"path": "/x.py"})
        assert not _is_fs_probe("datetime_now", {})

    def test_probe_target(self):
        assert _fs_probe_target("list_dir", {"path": "C:\\Users"}) == "C:\\Users"
        assert _fs_probe_target("glob", {"pattern": "**/*.md"}) == "**/*.md"


class _OkCatalog:
    """任何工具都返回 ok（只关心发散计数，不关心真实执行）。"""

    async def definitions(self, names=None):
        return [
            {
                "name": "list_dir",
                "description": "list",
                "parameters": {"type": "object", "properties": {}},
            }
        ]

    async def execute(self, name, args, state):
        return {"id": f"r-{name}", "name": name, "ok": True, "result": {"entries": []}}


class _FakeBackend:
    def __init__(self, script):
        self.script = list(script)

    async def chat_with_tools(self, messages, tools, **kw):
        if not self.script:
            return {"content": "脚本耗尽", "tool_calls": []}
        return self.script.pop(0)


class _FakeRouter:
    def __init__(self, backend):
        self._backend = backend

    async def resolve_native_backend(self):
        return ("cloud", self._backend)


def _state(**over) -> dict:
    st = {
        "user_prompt": "找一下对公转账汇兑的规章制度",
        "messages": [],
        "tool_calling_mode": "native",
        "tool_results": [],
        "tool_turn_count": 0,
        "full_toolset_loaded": False,
        "native_turn_context": None,
        "dual_rules_addon": "",
    }
    st.update(over)
    return st


def _call(i: int) -> dict:
    return {
        "id": f"c{i}",
        "name": "list_dir",
        "arguments": {"path": f"C:\\dir{i}"},
    }


async def test_fs_divergence_hard_stop(monkeypatch):
    """跨多个不同路径的只读探测累计达硬阈 → 直接停，给出可操作终答。"""
    monkeypatch.setattr(settings, "tool_loop_fs_probe_soft_limit", 2)
    monkeypatch.setattr(settings, "tool_loop_fs_probe_hard_limit", 3)
    monkeypatch.setattr(settings, "tool_loop_wall_clock_sec", 0.0)  # 关墙钟，专测发散
    backend = _FakeBackend(
        [
            {"content": None, "tool_calls": [_call(0)]},
            {"content": None, "tool_calls": [_call(1)]},
            {"content": None, "tool_calls": [_call(2)]},
            {"content": None, "tool_calls": [_call(3)]},
        ]
    )
    loop = DynamicToolLoop(_FakeRouter(backend), _OkCatalog())
    out = await loop.run(_state())
    assert out["tool_loop_active"] is False
    assert "发散式翻找" in out["final_answer"]
    assert any(t.get("reason") == "fs_divergence" for t in out["trace"])


async def test_fs_divergence_soft_directive_injected(monkeypatch):
    """达软阈先注入强制收敛指令（不直接停），模型收敛后即可正常终答。"""
    monkeypatch.setattr(settings, "tool_loop_fs_probe_soft_limit", 2)
    monkeypatch.setattr(settings, "tool_loop_fs_probe_hard_limit", 100)
    monkeypatch.setattr(settings, "tool_loop_wall_clock_sec", 0.0)
    backend = _FakeBackend(
        [
            {"content": None, "tool_calls": [_call(0)]},
            {"content": None, "tool_calls": [_call(1)]},
            # 收到强制收敛指令后，模型改为直接作答
            {"content": "根据知识库，对公转账单笔超 50 万需复核。", "tool_calls": []},
        ]
    )
    loop = DynamicToolLoop(_FakeRouter(backend), _OkCatalog())
    out = await loop.run(_state())
    assert out["final_answer"].startswith("根据知识库")
    assert any(t.get("reason") == "fs_divergence_force_converge" for t in out["trace"])


def test_fs_divergence_msg_mentions_knowledge_base():
    msg = _fs_divergence_msg(12, 8)
    assert "知识库" in msg
    assert "12" in msg
