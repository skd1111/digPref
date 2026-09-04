"""stream_utils —— LLM 流式输出增量工具（2026-09-03，回答逐字流式输出）。

ThinkBlockFilter：推理模型（内网 / Ollama 思考型）会把内心独白塞进输出流，
非流式链路靠 json_discipline.strip_think_blocks 在全文上剥离；流式链路的
delta 一旦发出就无法撤回，必须在增量层面抑制——本过滤器跨 delta 边界识别
`<think>…</think>` 与 ```think … ``` 两类块，块内内容一律吞掉，块外文本
带小缓冲（holdback）延迟放行，防止半截标记漏给前端。

设计取舍：
- 只做「宁可晚发、不可错发」：疑似标记前缀的尾巴先扣住，流结束 flush() 时
  未成块的扣留文本原样放行（不吞用户正文）；
- 未闭合的 think 块（流中断）→ 块内已吞内容不恢复，与 strip_think_blocks
  对「未闭合尾巴」的语义一致。
"""

from __future__ import annotations

import re

from agent.llm.json_discipline import strip_think_blocks

# 完整标记（与 json_discipline._THINK_BLOCK_RE 语义对齐：允许标签内空白）
_OPEN_TAG_RE = re.compile(r"<\s*think\s*>", re.IGNORECASE)
_CLOSE_TAG_RE = re.compile(r"</\s*think\s*>", re.IGNORECASE)
_OPEN_FENCE_RE = re.compile(r"```\s*think[^\n]*\n?", re.IGNORECASE)
_FENCE_CLOSE = "```"

# 疑似标记前缀的扣留上限（够覆盖 "```think" / "</think" 的半截形态）
_MAX_HOLDBACK = 12

# 各状态下需要防漏的标记字面量（模糊前缀比对用：忽略空白 + 大小写）
_TEXT_MARKERS = ("<think>", "```think")
_TAG_CLOSE_MARKERS = ("</think>",)
_FENCE_CLOSE_MARKERS = ("```",)


def _normalize(text: str) -> str:
    return re.sub(r"\s+", "", text).lower()


def _partial_marker_len(buf: str, markers: tuple[str, ...]) -> int:
    """buf 尾部若是某标记的（模糊）前缀，返回该尾巴长度；否则 0。"""
    limit = min(len(buf), _MAX_HOLDBACK)
    for k in range(limit, 0, -1):
        tail = _normalize(buf[-k:])
        if not tail:
            continue
        for marker in markers:
            if _normalize(marker).startswith(tail):
                return k
    return 0


class ThinkBlockFilter:
    """增量 think 块过滤器：feed(delta) 返回可外发文本，流结束调 flush()。"""

    def __init__(self) -> None:
        self._buf = ""
        # text = 正文；think_tag = <think> 块内；think_fence = ```think 块内
        self._mode = "text"

    def feed(self, delta: str) -> str:
        """喂入一段增量，返回当前可安全外发的文本（可能为空串）。"""
        if not delta:
            return ""
        self._buf += delta
        out: list[str] = []
        while self._buf:
            before = (self._mode, len(self._buf))
            if self._mode == "text":
                out.append(self._consume_text())
            elif self._mode == "think_tag":
                self._consume_think_tag()
            else:
                self._consume_think_fence()
            if (self._mode, len(self._buf)) == before:
                break  # 无进展（全部被扣留/吞掉）→ 本轮结束，防死循环
        return "".join(out)

    def flush(self) -> str:
        """流结束：正文模式下放行扣留尾巴；think 块内（未闭合）丢弃。"""
        buf, self._buf = self._buf, ""
        return buf if self._mode == "text" else ""

    # ---- 各状态消费 ---------------------------------------------------------

    def _consume_text(self) -> str:
        """正文态：切掉已确认的 think 起始块，其余扣除疑似标记尾巴后放行。"""
        emittable = ""
        while True:
            tag = _OPEN_TAG_RE.search(self._buf)
            fence = _OPEN_FENCE_RE.search(self._buf)
            match, mode = (
                (tag, "think_tag")
                if tag and (not fence or tag.start() <= fence.start())
                else ((fence, "think_fence") if fence else (None, ""))
            )
            if match is None:
                hold = _partial_marker_len(self._buf, _TEXT_MARKERS)
                cut = len(self._buf) - hold
                emittable += self._buf[:cut]
                self._buf = self._buf[cut:]
                return emittable
            emittable += self._buf[: match.start()]
            self._buf = self._buf[match.end() :]
            self._mode = mode
            # 进入块内态后交回主循环继续消费（块可能在本 delta 内就闭合）
            if self._mode == "think_tag":
                self._consume_think_tag()
            else:
                self._consume_think_fence()
            if self._mode == "text":
                continue
            return emittable

    def _consume_think_tag(self) -> None:
        """<think> 块内：找到闭合标签则回正文，否则整段吞掉（留疑似尾巴）。"""
        m = _CLOSE_TAG_RE.search(self._buf)
        if m:
            self._buf = self._buf[m.end() :]
            self._mode = "text"
            return
        hold = _partial_marker_len(self._buf, _TAG_CLOSE_MARKERS)
        self._buf = self._buf[len(self._buf) - hold :] if hold else ""

    def _consume_think_fence(self) -> None:
        """```think 块内：找到闭合围栏则回正文，否则整段吞掉（留疑似尾巴）。"""
        idx = self._buf.find(_FENCE_CLOSE)
        if idx >= 0:
            self._buf = self._buf[idx + len(_FENCE_CLOSE) :]
            self._mode = "text"
            return
        hold = _partial_marker_len(self._buf, _FENCE_CLOSE_MARKERS)
        self._buf = self._buf[len(self._buf) - hold :] if hold else ""


def strip_think(full_text: str) -> str:
    """累积全文兜底剥离（复用既有非流式语义，含未闭合尾巴处理）。"""
    return strip_think_blocks(full_text)
