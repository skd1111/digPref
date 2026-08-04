"""LocalVisionClient —— 本地视觉模型客户端（Moondream2 / OpenAI 多模态兼容）。

Phase 4 V0：截图理解能力，端侧独有（不降级到云端）。
"""
from __future__ import annotations

import base64
import logging
from typing import Any

import httpx

logger = logging.getLogger("agent.llm.local_vision")


class LocalVisionUnavailableError(Exception):
    """本地视觉模型不可达。"""


class LocalVisionClient:
    """本地视觉模型客户端 — OpenAI 多模态 Chat Completions 格式。"""

    def __init__(
        self,
        *,
        base_url: str = "http://127.0.0.1:8082/v1",
        model: str = "moondream2",
        max_context: int | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.max_context = max_context

    async def _chat(
        self,
        messages: list[dict],
        *,
        max_tokens: int = 256,
        temperature: float = 0.1,
        timeout: float = 10.0,
    ) -> str:
        payload: dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "max_tokens": max_tokens,
            "temperature": temperature,
            "stream": False,
        }
        try:
            async with httpx.AsyncClient(timeout=timeout) as c:
                r = await c.post(f"{self.base_url}/chat/completions", json=payload)
                r.raise_for_status()
                body = r.json()
                return body["choices"][0]["message"]["content"]
        except httpx.ConnectError as exc:
            logger.debug("local_vision: connection refused at %s", self.base_url)
            raise LocalVisionUnavailableError(
                f"Local vision model not reachable at {self.base_url}"
            ) from exc
        except httpx.TimeoutException as exc:
            logger.debug("local_vision: request timed out")
            raise LocalVisionUnavailableError(
                "Local vision model request timed out"
            ) from exc
        except (KeyError, IndexError) as exc:
            logger.debug("local_vision: unexpected response shape: %s", exc)
            raise LocalVisionUnavailableError(
                "Local vision model returned unexpected response"
            ) from exc

    # ---- Public API -------------------------------------------------------

    async def understand_screenshot(
        self,
        image_bytes: bytes,
        prompt: str = "请描述这张截图中的报错信息和关键 UI 元素。",
    ) -> str:
        """识别截图内容（报错信息、UI 元素等）。

        Args:
            image_bytes: PNG/JPEG 图片原始字节
            prompt: 理解提示词

        Returns:
            模型描述文本；失败时返回空字符串
        """
        try:
            b64 = base64.b64encode(image_bytes).decode("ascii")
            data_url = f"data:image/png;base64,{b64}"
            msg = await self._chat(
                [
                    {
                        "role": "user",
                        "content": [
                            {"type": "image_url", "image_url": {"url": data_url}},
                            {"type": "text", "text": prompt},
                        ],
                    }
                ],
                timeout=10.0,
            )
            return msg
        except (LocalVisionUnavailableError, Exception) as e:
            logger.debug("local_vision: understand_screenshot failed: %s", e)
            return ""

    async def extract_text_from_image(self, image_bytes: bytes) -> str:
        """从图片中提取文字（OCR 场景）。

        Returns:
            提取的文字；失败时返回空字符串
        """
        try:
            b64 = base64.b64encode(image_bytes).decode("ascii")
            data_url = f"data:image/png;base64,{b64}"
            msg = await self._chat(
                [
                    {
                        "role": "user",
                        "content": [
                            {"type": "image_url", "image_url": {"url": data_url}},
                            {"type": "text", "text": "请提取这张图片中的所有文字。只输出文字，不要添加任何解释。"},
                        ],
                    }
                ],
                max_tokens=512,
                timeout=10.0,
            )
            return msg
        except (LocalVisionUnavailableError, Exception) as e:
            logger.debug("local_vision: extract_text failed: %s", e)
            return ""

    # ---- Health -----------------------------------------------------------

    async def health_check(self) -> bool:
        """探测视觉模型服务是否在线。"""
        try:
            async with httpx.AsyncClient(timeout=3.0) as c:
                r = await c.get(f"{self.base_url}/models")
                return r.status_code == 200
        except Exception:
            return False
