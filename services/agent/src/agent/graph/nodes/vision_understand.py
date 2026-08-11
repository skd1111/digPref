"""vision_understand_node —— Phase 4 V0 截图理解节点。

当用户附带截图时，调用本地视觉模型（local_vision）生成文字描述，
注入到对话上下文中供后续 intent/planner 节点使用。
"""

from __future__ import annotations

import logging

from agent.graph.state import AgentState, record_trace

logger = logging.getLogger(__name__)


async def vision_understand_node(state: AgentState, llm) -> dict:
    """处理截图：调 local_vision 模型 → 返回文字描述。

    仅在 state.screenshot 有值时运行；否则跳过（no-op）。
    """
    screenshot = state.get("screenshot")
    if not screenshot:
        return {
            "vision_result": None,
            "trace": [record_trace("vision_understand", "skipped", reason="no screenshot")],
        }

    question = state.get("vision_question") or "请描述这张截图中的关键信息、报错内容和 UI 元素。"

    try:
        # 通过 LMRouter 获取 local_vision client
        # local_vision 客户端在 router 上作为属性存在
        from agent.config import settings
        from agent.llm.local_vision import LocalVisionClient, LocalVisionUnavailableError

        client = LocalVisionClient(
            base_url=settings.local_vision_base_url or "http://127.0.0.1:8082/v1",
            model=settings.local_vision_model or "moondream2",
        )

        # 将 base64 转回 bytes 传给客户端
        import base64

        try:
            image_bytes = base64.b64decode(screenshot)
        except Exception:
            image_bytes = screenshot.encode("utf-8") if isinstance(screenshot, str) else screenshot

        result = await client.understand_screenshot(image_bytes, prompt=question)

        return {
            "vision_result": result or "",
            "trace": [
                record_trace(
                    "vision_understand",
                    "ok" if result else "fail",
                    result_len=len(result) if result else 0,
                    backend="local_vision",
                )
            ],
        }
    except LocalVisionUnavailableError:
        logger.debug("vision_understand: local_vision unavailable, skipping")
        return {
            "vision_result": None,
            "trace": [record_trace("vision_understand", "skipped", reason="vision_unavailable")],
        }
    except Exception as e:
        logger.warning("vision_understand: unexpected error: %s", e)
        return {
            "vision_result": None,
            "trace": [record_trace("vision_understand", "fail", error=str(e))],
        }
