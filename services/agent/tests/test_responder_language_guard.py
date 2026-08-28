"""responder 语言硬兜底测试（BUGFIX #114，2026-08-17）。

背景：内网推理模型默认英文作答，工具循环 FINAL_ANSWER 经 responder 直透用户
不经 summarise；prompt 语言纪律对小模型只是软约束。硬兜底：中文提问 + 终答
几乎纯英文 → 透传前改走 summarise 重写中文；重写失败/无效回退原文。

覆盖：
    - 中文提问 + 英文终答 → 触发重写，返回中文重写稿
    - 中文提问 + 中文终答 → 原样透传，不调 LLM
    - 英文提问 + 英文终答 → 原样透传（英文答英文是合法的）
    - 纯数字/符号终答（无拉丁字母）→ 不重写
    - 终答含 ```clarify 选项卡 → 不重写（防破坏前端卡片 JSON）
    - 重写 LLM 抛异常 → 回退原英文稿，不阻断终答
    - 重写结果仍无中文（模型不听话）→ 回退原英文稿
"""

from __future__ import annotations

from agent.graph.nodes.responder import (
    _needs_chinese_rewrite,
    responder_node,
)

_ENGLISH_DRAFT = (
    "The sum of 12387162831 and 1639169124 is calculated as follows:\n"
    "Step 1: Align both numbers by their place values.\n"
    "Step 2: Perform standard addition from right to left.\n"
    "Final Answer: 14,026,331,955"
)


class _FakeLLM:
    """记录 summarise 调用并返回可配置的重写结果。"""

    def __init__(self, answer: str = "两数之和为 14,026,331,955。", raise_exc: bool = False):
        self._answer = answer
        self._raise = raise_exc
        self.calls: list[dict] = []

    async def summarise(self, *, intent, user_prompt, plan, results, history=None):
        self.calls.append(
            {"intent": intent, "user_prompt": user_prompt, "plan": plan, "results": results}
        )
        if self._raise:
            raise RuntimeError("LLM 不可用")
        return self._answer, []


def _state(prompt: str, final_answer: str) -> dict:
    return {"user_prompt": prompt, "final_answer": final_answer, "trace": []}


class TestNeedsChineseRewrite:
    def test_chinese_prompt_english_answer_triggers(self):
        assert _needs_chinese_rewrite("12387162831+1639169124等于", _ENGLISH_DRAFT)

    def test_chinese_answer_passes(self):
        assert not _needs_chinese_rewrite(
            "帮我算一下", "两数之和为 14,026,331,955，计算过程是逐位相加。"
        )

    def test_english_prompt_passes(self):
        assert not _needs_chinese_rewrite("what is 1+1?", _ENGLISH_DRAFT)

    def test_pure_number_answer_passes(self):
        assert not _needs_chinese_rewrite("等于多少", "14026331955")

    def test_clarify_card_not_touched(self):
        body = _ENGLISH_DRAFT + '\n\n```clarify\n[{"question": "confirm?"}]\n```'
        assert not _needs_chinese_rewrite("算一下", body)

    def test_short_english_snippet_passes(self):
        # 字母数 < 20：短英文片段（如错误码）不改写
        assert not _needs_chinese_rewrite("为什么失败", "HTTP 404 not found")

    def test_chinese_dominant_with_terms_passes(self):
        # 中文为主、夹杂英文术语 → 不误伤
        answer = "已修改配置文件并完成部署，详见 " + "config " * 3 + "相关说明文档。" * 5
        assert not _needs_chinese_rewrite("帮我部署", answer)


class TestResponderLanguageGuard:
    async def test_english_draft_rewritten_to_chinese(self):
        llm = _FakeLLM()
        out = await responder_node(_state("这两个数加起来等于", _ENGLISH_DRAFT), llm)
        assert out["final_answer"] == "两数之和为 14,026,331,955。"
        assert len(llm.calls) == 1
        # 原英文稿作为待重写素材传给 summarise
        assert llm.calls[0]["results"][0]["result"] == _ENGLISH_DRAFT

    async def test_chinese_draft_passthrough_no_llm(self):
        llm = _FakeLLM()
        draft = "两数之和为 14,026,331,955。"
        out = await responder_node(_state("加起来等于", draft), llm)
        assert out["final_answer"] == draft
        assert llm.calls == []

    async def test_rewrite_failure_falls_back_to_draft(self):
        llm = _FakeLLM(raise_exc=True)
        out = await responder_node(_state("加起来等于", _ENGLISH_DRAFT), llm)
        # 重写失败 → 回退原英文稿，终答不阻断
        assert out["final_answer"] == _ENGLISH_DRAFT

    async def test_rewrite_still_english_falls_back(self):
        # 模型不听话：重写结果仍无中文 → 回退原稿，不把更差的结果给用户
        llm = _FakeLLM(answer="Still an English answer without any Chinese characters here.")
        out = await responder_node(_state("加起来等于", _ENGLISH_DRAFT), llm)
        assert out["final_answer"] == _ENGLISH_DRAFT
