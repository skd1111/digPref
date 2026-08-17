"""LLM 管理工具（2026-08-14）—— 给编排层一双「模型接入」的手。

背景：用户说「帮我连接内网模型 X http://...」时，分解层此前手里没有
写模型配置 / HTTP 探测的工具，只能退化到 ASK_USER 连发多个开放式问题。
本模块补两个 builtin 工具：

    - model_config_upsert   写 router.db.llm_backends（= 设置→模型管理同一真源），
                            risk=high → dispatcher 强制走 HITL 审批卡（参数摘要 +
                            批准/拒绝），符合「写操作绝不绕过 HITL」红线。
    - probe_chat_endpoint   对 OpenAI 兼容 chat/completions 端点发最小探测请求，
                            返回可达性 / 状态码 / 耗时，risk=read。

安全红线：
    - 凭证绝不进工具参数 / 日志 / 返回体（api_key 只认 keyring 引用名）。
    - probe 不带 Authorization 头；需要鉴权的端点以 status_code=401/403 汇报。
"""

from __future__ import annotations

import time
from typing import Any

import httpx

from agent.builtin.models import ToolResult

_VALID_BACKEND_TYPES = ("local", "private", "cloud")
# type → 默认数据驻留（与模型管理面板语义一致）
_TYPE_TO_RESIDENCY = {"local": "local", "private": "private", "cloud": "cloud"}


async def builtin_model_config_upsert(
    *,
    name: str,
    type: str,
    base_url: str,
    model_name: str,
    api_key_ref: str | None = None,
    enabled: bool = True,
    role: str = "execution",
    max_context: int = 32768,
    timeout_seconds: int = 30,
    capabilities: list[str] | None = None,
) -> ToolResult:
    """新增 / 更新一个模型后端配置（router.db.llm_backends，按 name upsert）。

    与「设置 → 模型管理」同一持久化真源；保存成功后热重载运行中的路由器。
    risk=high：dispatcher 会先弹 HITL 审批卡确认参数，未批准不执行。
    api_key_ref 只接受 keyring 引用名（如 llm.<name>.api_key），严禁传明文。
    """
    name = (name or "").strip()
    model_name = (model_name or "").strip()
    base_url = (base_url or "").strip().rstrip("/")
    if not name or not model_name or not base_url:
        return ToolResult(
            ok=False,
            error="missing_required_field",
            hint="name / model_name / base_url 均为必填",
            risk_level="high",
        )
    if type not in _VALID_BACKEND_TYPES:
        return ToolResult(
            ok=False,
            error="invalid_type",
            hint=f"type 必须是 {list(_VALID_BACKEND_TYPES)} 之一",
            risk_level="high",
        )
    if not base_url.lower().startswith(("http://", "https://")):
        return ToolResult(
            ok=False,
            error="invalid_url",
            hint="base_url 仅支持 http:// 或 https://",
            risk_level="high",
        )
    try:
        from agent.llm.models import LLMBackend
        from agent.llm.storage import upsert_backend

        backend = LLMBackend(
            name=name,
            type=type,
            base_url=base_url,
            model_name=model_name,
            api_key_ref=(api_key_ref or "").strip() or None,
            capabilities=list(capabilities or []),
            max_context=int(max_context),
            timeout_seconds=int(timeout_seconds),
            data_residency=_TYPE_TO_RESIDENCY[type],
            enabled=bool(enabled),
            role=role or "execution",
        )
        disabled = await upsert_backend(backend)
    except Exception as exc:
        return ToolResult.from_exception(exc, risk_level="high")

    # 热生效（best-effort）：运行中的 LMRouter 重新读 router.db，无需重启 Agent。
    # 与 engine_api.py 的 active 切换同款做法；失败不影响配置已落库的事实。
    try:
        from agent import main as agent_main

        runtime = getattr(agent_main, "_runtime", None)
        llm = getattr(runtime, "llm", None)
        if llm is not None and hasattr(llm, "reload_max_context"):
            llm.reload_max_context()
    except Exception:
        pass

    content: dict[str, Any] = {
        "name": name,
        "type": type,
        "base_url": base_url,
        "model_name": model_name,
        "enabled": bool(enabled),
        "role": role,
    }
    if disabled:
        content["disabled_same_residency"] = list(disabled)
    return ToolResult(
        ok=True,
        content=content,
        meta={"disabled_count": len(disabled)},
        risk_level="high",
    )


async def builtin_probe_chat_endpoint(
    *,
    url: str,
    model: str,
    timeout_s: float = 5.0,
) -> ToolResult:
    """探测 OpenAI 兼容 chat/completions 端点是否可用（只读，最小请求）。

    发 messages=[{role:user, content:"ping"}]、max_tokens=1 的最小请求，
    返回 reachable / status_code / latency_ms。url 不带 /chat/completions
    后缀时自动补齐。不带 Authorization —— 需要鉴权的端点以 401/403 汇报。
    """
    url = (url or "").strip()
    model = (model or "").strip()
    if not url.lower().startswith(("http://", "https://")):
        return ToolResult(
            ok=False,
            error="invalid_url",
            hint="仅支持 http:// 或 https:// 地址",
            risk_level="read",
        )
    if not url.endswith("/chat/completions"):
        url = url.rstrip("/") + "/chat/completions"
    if not model:
        return ToolResult(
            ok=False,
            error="missing_model",
            hint="探测需要模型名（model 参数）",
            risk_level="read",
        )
    timeout = max(1.0, min(float(timeout_s or 5.0), 30.0))
    payload = {
        "model": model,
        "messages": [{"role": "user", "content": "ping"}],
        "max_tokens": 1,
        "stream": False,
    }
    started = time.monotonic()
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            resp = await client.post(url, json=payload)
        latency_ms = int((time.monotonic() - started) * 1000)
        reachable = resp.status_code < 400
        content: dict[str, Any] = {
            "reachable": reachable,
            "status_code": resp.status_code,
            "latency_ms": latency_ms,
            "endpoint": url,
            "model": model,
        }
        if resp.status_code in (401, 403):
            content["auth_required"] = True
        hint = None
        if not reachable:
            hint = (
                "端点需要鉴权（请配置 API Key）"
                if resp.status_code in (401, 403)
                else f"端点返回 HTTP {resp.status_code}"
            )
        return ToolResult(
            ok=reachable,
            content=content,
            hint=hint,
            meta={"latency_ms": latency_ms, "status_code": resp.status_code},
            risk_level="read",
        )
    except httpx.TimeoutException:
        return ToolResult(
            ok=False,
            content={"reachable": False, "endpoint": url, "model": model},
            error="timeout",
            hint=f"探测超时（{timeout:.0f}s），端点可能不可达或响应过慢",
            meta={"timeout_s": timeout},
            risk_level="read",
        )
    except Exception as exc:
        return ToolResult(
            ok=False,
            content={"reachable": False, "endpoint": url, "model": model},
            error=f"{type(exc).__name__}: {exc}",
            hint="无法建立连接，检查地址 / 端口 / 网络可达性",
            risk_level="read",
        )
