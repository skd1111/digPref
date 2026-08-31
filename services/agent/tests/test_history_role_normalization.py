"""根治 BUGFIX #163 的回归测试（历史消息 role 归一化）。

## 为什么这个文件必须用 ``add_messages()`` 造数据

``AgentState.messages`` 带 ``Annotated[list, add_messages]`` reducer：入图时的
``{"role": ..., "content": ...}`` dict 会被 LangGraph 统一转成 ``HumanMessage`` /
``AIMessage`` 对象，而 ``BaseMessage`` **没有 ``.role`` 属性**（只有 ``.type``，
取值 ``human`` / ``ai`` / ``system``）。

此前全仓 10 处消费点各自手写 ``getattr(h, "role", None)``，对象形态一律取不到 →
4 处静默丢弃整条历史、2 处 fallback 成默认 ``"user"``（assistant 回复被误标成
用户提问）。现有测试**全部手写 dict 造数据**，所以 10 个 bug 一个都没抓到。

这里的每个用例都从 ``add_messages()`` 的真实输出出发 —— 这是本次真正的教训。
"""

from __future__ import annotations

import pytest
from agent.llm.prompts import format_history_brief, normalize_message
from agent.llm.router import _compact_messages, _conversation_summary
from langgraph.graph.message import add_messages

# 典型跨轮场景：system 摘要 + 上轮问答 + 本轮「继续」
_RAW_HISTORY = [
    {"role": "system", "content": "【前段对话摘要】用户正在制作介绍 daide 的 PPT"},
    {"role": "user", "content": "做一个介绍你自己的 PPT"},
    {"role": "assistant", "content": "好的，已生成 8 页初稿"},
    {"role": "user", "content": "继续"},
]


@pytest.fixture()
def lc_messages() -> list:
    """LangGraph reducer 处理后的真实 messages（BaseMessage 对象列表）。"""
    return add_messages([], _RAW_HISTORY)


# ---- 前置断言：坐实 BaseMessage 确实没有 .role -----------------------------


def test_basemessage_has_no_role_attribute(lc_messages: list) -> None:
    """这是整个 BUGFIX #163 的物理前提，先把它钉死。

    若某天 langchain 给 BaseMessage 加上了 .role，这条会失败 —— 那时可以
    简化 normalize_message，但在那之前任何 `getattr(m, "role")` 都是错的。
    """
    assert lc_messages, "add_messages 不该返回空"
    for m in lc_messages:
        assert getattr(m, "role", None) is None
        assert getattr(m, "type", None) in {"human", "ai", "system"}


# ---- normalize_message ------------------------------------------------------


def test_normalize_message_maps_basemessage_types(lc_messages: list) -> None:
    assert [normalize_message(m) for m in lc_messages] == [
        ("system", "【前段对话摘要】用户正在制作介绍 daide 的 PPT"),
        ("user", "做一个介绍你自己的 PPT"),
        ("assistant", "好的，已生成 8 页初稿"),
        ("user", "继续"),
    ]


def test_normalize_message_accepts_plain_dict() -> None:
    assert normalize_message({"role": "user", "content": "hi"}) == ("user", "hi")
    assert normalize_message({"role": "assistant", "content": " hi "}) == ("assistant", "hi")


@pytest.mark.parametrize(
    "junk",
    [
        None,
        "",
        123,
        {},
        {"role": "user"},  # 无 content
        {"content": "orphan"},  # 无 role
        {"role": "user", "content": "   "},  # 空白 content
        {"role": "tool", "content": "x"},  # 未知 role
        {"role": "function", "content": "x"},
    ],
)
def test_normalize_message_rejects_junk(junk: object) -> None:
    """不认识的形态必须返回 None，让调用方跳过 —— 绝不能猜一个默认 role。

    猜 "user" 就是 router.py 那两处旧 bug 的成因：assistant 回复被误标成
    用户提问，模型看到一段用户自言自语，比直接丢弃更难排查。
    """
    assert normalize_message(junk) is None


# ---- format_history_brief（终答链路，用户报障的直接来源）--------------------


def test_format_history_brief_survives_langgraph_reducer(lc_messages: list) -> None:
    """回归核心：此前这里返回 ""，导致终答 prompt 里整段历史消失，
    模型如实回答「这是一次新的会话，我没有保留之前的任务状态」。
    """
    brief = format_history_brief(lc_messages)
    assert brief, "BaseMessage 历史不得被静默过滤成空"
    assert "做一个介绍你自己的 PPT" in brief
    assert "已生成 8 页初稿" in brief
    assert "[assistant]" in brief, "assistant 角色必须保留，不能全标成 user"


def test_format_history_brief_keeps_system_context(lc_messages: list) -> None:
    """stream.py 把「前段对话摘要」与「任务台账锚点」以 system 消息注入 messages
    头部 —— 那是跨轮上下文里信息密度最高的两条，旧实现连带丢掉了。
    """
    assert "【前段对话摘要】" in format_history_brief(lc_messages)


def test_format_history_brief_empty_history_returns_empty() -> None:
    """空历史返 ""，调用方据此决定是否注入 prompt 段落（契约不变）。"""
    assert format_history_brief([]) == ""
    assert format_history_brief(None) == ""


def test_format_history_brief_truncates_long_message() -> None:
    long = add_messages([], [{"role": "user", "content": "x" * 900}])
    brief = format_history_brief(long, per_message_chars=400)
    assert "（已截断）" in brief
    assert len(brief) < 900


def test_format_history_brief_respects_max_messages() -> None:
    many = add_messages([], [{"role": "user", "content": f"msg{i}"} for i in range(20)])
    brief = format_history_brief(many, max_messages=3)
    assert len(brief.splitlines()) == 3
    assert "msg19" in brief, "应保留最近的几条而非最早的"


# ---- router 两处静默误标 ----------------------------------------------------


def test_conversation_summary_preserves_assistant_role(lc_messages: list) -> None:
    """旧实现 getattr(m, "role", "user") 把每条都标成 user。"""
    summary = _conversation_summary(lc_messages)
    assert "assistant: 好的，已生成 8 页初稿" in summary
    assert summary.count("user:") == 2, "两条用户消息，不该把 assistant 也算进来"


def test_compact_messages_preserves_roles(lc_messages: list) -> None:
    roles = [m["role"] for m in _compact_messages(lc_messages)]
    assert roles == ["system", "user", "assistant", "user"]


def test_compact_messages_skips_junk_instead_of_defaulting() -> None:
    """混入脏数据时跳过，而不是塞一条 role="user" 的空消息。"""
    out = _compact_messages([{"role": "user", "content": "ok"}, None, 42, {}])
    assert out == [{"role": "user", "content": "ok"}]
