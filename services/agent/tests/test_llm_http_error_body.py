"""BUGFIX #137 —— LLM HTTP 错误必须带服务端响应正文（排障不再黑盒）。

2026-08-25 实测：MiniMax-M3 对 chat/completions 回 400（220 字节 JSON 含具体
原因），但 raise_for_status 只留 'Client error 400 Bad Request'，日志与用户
终答都只见 HTTPStatusError，无法判断是上下文超限还是参数问题；工具循环还因此
在工具成功后整轮硬停。现在异常信息与日志必须携带响应正文摘要。
"""

from __future__ import annotations

import httpx
import pytest
from agent.llm.private_llm import _raise_http_with_body


def _resp(status: int, text: str) -> httpx.Response:
    return httpx.Response(
        status,
        request=httpx.Request("POST", "https://api.minimaxi.com/v1/chat/completions"),
        text=text,
    )


def test_http_error_carries_server_body():
    """400 异常信息里必须带服务端返回的具体原因。"""
    r = _resp(400, '{"base_resp": {"status_msg": "context length exceeded"}}')
    with pytest.raises(httpx.HTTPStatusError) as ei:
        _raise_http_with_body(r)
    assert "context length exceeded" in str(ei.value)
    assert "400" in str(ei.value)


def test_http_error_empty_body_tolerated():
    """空响应体也不裸奔：带占位说明，不抛二级异常。"""
    with pytest.raises(httpx.HTTPStatusError) as ei:
        _raise_http_with_body(_resp(502, ""))
    assert "空响应体" in str(ei.value)


def test_http_2xx_passes():
    """正常响应不抛。"""
    _raise_http_with_body(_resp(200, '{"choices": []}'))
