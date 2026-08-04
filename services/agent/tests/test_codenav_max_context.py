"""测试 CodenavLLMClient 的 max_context 截断行为。

覆盖：
    - 未设置 max_context → user 原样返回
    - 设置 max_context → user 超长时被截断（保留 head + 省略标记 + tail）
    - system 超长边界
    - 极小 max_context 兜底（最小保留 256 chars）
"""

from __future__ import annotations


def test_codenav_no_truncate_when_unset():
    """max_context=None 时，user 原样返回。"""
    from agent.codenav.llm_client import CodenavLLMClient

    cli = CodenavLLMClient(base_url="http://x", model="m", api_key="k", max_context=None)
    system = "短 system"
    user = "X" * 10000
    out = cli._truncate_context(system, user, max_tokens=500)
    assert out == user


def test_codenav_truncate_oversized_user():
    """max_context 较小 + user 超长 → 中部被截，保留 head + tail + 省略标记。"""
    from agent.codenav.llm_client import CodenavLLMClient

    # max_context=512 → budget_tokens = max(256, 512-500)=256 → budget_chars = 1024
    cli = CodenavLLMClient(base_url="http://x", model="m", api_key="k", max_context=512)
    system = "短 system"  # 12 chars
    # user 总 4000 chars；预算 = 1024 - 12 = 1012 chars → 大幅截断
    user = "HEAD_" + "A" * 4000 + "_TAIL"
    out = cli._truncate_context(system, user, max_tokens=500)

    # 输出长度应小于原 user
    assert len(out) < len(user)
    # head 至少 MIN_KEEP_CHARS 保留
    assert out.startswith("HEAD_")
    # 含截断标记
    assert "已截断" in out
    # tail 应保留
    assert out.endswith("_TAIL")


def test_codenav_user_within_budget_unchanged():
    """max_context 充足 + user 在预算内 → 原样返回。"""
    from agent.codenav.llm_client import CodenavLLMClient

    # max_context=200000 → budget_tokens = max(256, 200000-500)=199500 → budget_chars=798000
    cli = CodenavLLMClient(base_url="http://x", model="m", api_key="k", max_context=200000)
    system = "system"
    user = "hello world" * 100  # 1100 chars，远小于预算
    out = cli._truncate_context(system, user, max_tokens=500)
    assert out == user


def test_codenav_system_oversized_graceful():
    """system 自己就超长 → user 截到 MIN_KEEP_CHARS。"""
    from agent.codenav.llm_client import CodenavLLMClient

    cli = CodenavLLMClient(base_url="http://x", model="m", api_key="k", max_context=512)
    system = "S" * 5000  # 远超 budget
    user = "X" * 4000
    out = cli._truncate_context(system, user, max_tokens=500)
    # 应当被截到 MIN_KEEP_CHARS（256）
    assert len(out) == 256
    assert out == "X" * 256


def test_codenav_small_max_context_min_keep():
    """极小 max_context（< 256 tokens）→ 走 max(256, ...) 兜底，不会截到 0。"""
    from agent.codenav.llm_client import CodenavLLMClient

    cli = CodenavLLMClient(base_url="http://x", model="m", api_key="k", max_context=10)
    system = "sys"
    user = "X" * 5000
    out = cli._truncate_context(system, user, max_tokens=500)
    # 至少保留 256 chars
    assert len(out) >= 256


def test_build_client_from_config_propagates_max_context():
    """build_client_from_config 应把 cfg['max_context'] 透传给 client。"""
    from agent.codenav.llm_client import build_client_from_config

    cli = build_client_from_config({
        "name": "deepseek", "type": "private",
        "base_url": "http://x", "model": "m", "api_key": "k",
        "max_context": 128000,
    })
    assert cli.max_context == 128000
    assert cli.base_url == "http://x"
    assert cli.model == "m"


def test_build_client_from_config_handles_invalid_max_context():
    """cfg['max_context'] 非法值（负数 / 字符串）→ 走 None（不截断）。"""
    from agent.codenav.llm_client import build_client_from_config

    cli = build_client_from_config({
        "base_url": "http://x", "model": "m", "api_key": "k",
        "max_context": -100,
    })
    assert cli.max_context is None

    cli2 = build_client_from_config({
        "base_url": "http://x", "model": "m", "api_key": "k",
        "max_context": "abc",
    })
    assert cli2.max_context is None

    cli3 = build_client_from_config({
        "base_url": "http://x", "model": "m", "api_key": "k",
        "max_context": 0,
    })
    assert cli3.max_context is None