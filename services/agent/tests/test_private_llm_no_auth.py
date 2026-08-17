"""免鉴权内网端点测试（2026-08-17，BUGFIX #109）。

背景：内网部署的模型很多不需要 api_key，但 PrivateLLMClient 此前无条件拼
`Authorization: Bearer {api_key}` —— key 为空时 httpx 直接拒发非法头
`b'Bearer '`（LocalProtocolError），本来可用的免鉴权内网后端被误判不可用，
意图/分解等任务全部降级云端或 mock。

修复：api_key 为空时不发 Authorization 头（与 engine_api / codenav 的
「key 非空才带头」约定对齐）。
"""

from __future__ import annotations

from agent.llm.private_llm import PrivateLLMClient


class TestAuthHeaders:
    def test_empty_api_key_omits_authorization(self):
        cli = PrivateLLMClient(base_url="http://172.1.0.134:8000/v1", api_key="", model="m")
        headers = cli._auth_headers()
        assert "Authorization" not in headers
        assert headers["Content-Type"] == "application/json"

    def test_non_empty_api_key_sends_bearer(self):
        cli = PrivateLLMClient(base_url="http://x/v1", api_key="sk-test", model="m")
        headers = cli._auth_headers()
        assert headers["Authorization"] == "Bearer sk-test"

    def test_shared_client_builds_without_key(self):
        """空 key 构造共享 httpx 客户端不再抛 Illegal header value。"""
        cli = PrivateLLMClient(base_url="http://172.1.0.134:8000/v1", api_key="", model="m")
        client = cli.client  # 此前这里直接抛 LocalProtocolError
        assert "Authorization" not in client.headers
        assert client.headers.get("Content-Type") == "application/json"

    def test_shared_client_with_key(self):
        cli = PrivateLLMClient(base_url="http://x/v1", api_key="sk-abc", model="m")
        assert cli.client.headers.get("Authorization") == "Bearer sk-abc"
