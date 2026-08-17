"""ASK_USER 终答选项卡化测试（2026-08-14）。

根因：编排决策器 ASK_USER 分支的 clarifying_questions 是自由文本（常自带
a/b/c 枚举），此前 responder 只拼正文 bullet list → 前端 ClarifyCard 无
```clarify 块可渲染，用户只能读纯文本。
修复：responder 确定性解析题干 + 选项并输出 clarify 块（不调 LLM、不改 prompt）。
"""

from __future__ import annotations

import json
import re

from agent.graph.nodes.responder import (
    _ask_user_body,
    _split_lettered_options,
    responder_node,
)

# 截图同款：环境缺工具追问，问题文本内自带 a/b/c 选项枚举
SCREENSHOT_QUESTION = (
    "当前环境缺少 ping/curl/HTTP 等网络工具，无法直接测试 "
    "172.16.10.139:8099 的连通性。需要补充以下信息后再继续："
    "a．您先在本地手动 ping / telnet 该地址，把结果告诉我；"
    "b．改用其他验证方式（如数据库连接探测）；"
    "c．跳过连通性测试，直接进行后续配置。"
)


def _parse_clarify(body: str) -> list[dict]:
    m = re.search(r"```clarify\s*([\s\S]*?)```", body)
    assert m, f"终答缺少 clarify 选项块：\n{body}"
    return json.loads(m.group(1))


class TestSplitLetteredOptions:
    def test_splits_stem_and_options(self):
        stem, options = _split_lettered_options(SCREENSHOT_QUESTION)
        assert "缺少 ping/curl/HTTP 等网络工具" in stem
        assert "a．" not in stem
        assert len(options) == 3
        assert options[0].startswith("您先在本地手动 ping")
        assert options[2].startswith("跳过连通性测试")
        # 选项文本不残留分隔标点
        assert not options[1].endswith("；")

    def test_plain_question_returns_no_options(self):
        stem, options = _split_lettered_options("目标环境是测试还是生产？")
        assert options == []
        assert stem == "目标环境是测试还是生产？"

    def test_single_letter_mention_not_misparsed(self):
        # 只出现一次 a. 不构成选项枚举（需 ≥2 个标记）
        _, options = _split_lettered_options("请提供 a. 方案的详细说明")
        assert options == []

    def test_options_capped_at_five(self):
        q = "选一个：" + "；".join(f"{c}．选项{c}" for c in "abcdefg")
        _, options = _split_lettered_options(q)
        assert len(options) == 5


class TestAskUserBody:
    def test_lettered_question_renders_clarify_cards(self):
        body = _ask_user_body([SCREENSHOT_QUESTION])
        items = _parse_clarify(body)
        assert len(items) == 1
        assert "缺少 ping/curl/HTTP 等网络工具" in items[0]["question"]
        assert len(items[0]["options"]) == 3
        # 恰好一个推荐项（前端 ClarifyCard 预选用）
        recommended = [o for o in items[0]["options"] if o["recommended"]]
        assert len(recommended) == 1

    def test_plain_question_falls_back_to_single_option(self):
        body = _ask_user_body(["目标环境？", "时间范围？"])
        items = _parse_clarify(body)
        assert len(items) == 2
        assert items[0]["question"] == "目标环境？"
        assert items[0]["options"][0]["text"] == "目标环境？"

    def test_empty_questions_gives_actionable_message(self):
        body = _ask_user_body([])
        assert "补充" in body
        assert "```clarify" not in body

    def test_bullets_kept_for_readability(self):
        body = _ask_user_body(["目标环境？"])
        assert "- 目标环境？" in body


class TestResponderNode:
    async def test_ask_user_terminal_answer_contains_clarify(self):
        state = {
            "user_prompt": "先 ping 一下通不通",
            "decompose_decision": {
                "decision": {
                    "mode": "ASK_USER",
                    "clarifying_questions": [SCREENSHOT_QUESTION],
                    "user_confirmation_required": False,
                }
            },
            "trace": [],
        }
        out = await responder_node(state, llm=None)  # type: ignore[arg-type]
        items = _parse_clarify(out["final_answer"])
        assert len(items[0]["options"]) == 3
