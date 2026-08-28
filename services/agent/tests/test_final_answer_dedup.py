"""values 快照 final_answer 去重测试（BUGFIX #115 / #142）。

背景：stream_mode=["values"] 下，工具循环（tool_orchestrator）写入
final_answer 后产生一次快照，responder 精修（补 clarify 选项块 / 中文重写）
又产生一次快照。
  - #115：同一内容只发一条（内容去重）。
  - #142：内容被精修后逃过内容去重，此前每次都用新 uuid → 前端按 id 追加
    出两条几乎相同的 assistant 消息（光标停在第一条上）。现在同一 run 内
    复用同一 message id，前端按 id update 原地覆盖 → 对话里始终只有一条终答。

覆盖：
    - 同一 final_answer 内容的连续快照只发一条 message（#115）
    - 内容被精修时仍会发送，但复用同一 message id（#142）
    - 未传去重集合时退化为旧行为（兼容既有调用）
"""

from __future__ import annotations

from agent.graph.stream import _convert_chunk


def _messages(events: list[dict]) -> list[dict]:
    return [e for e in events if e["event"] == "message"]


class TestFinalAnswerDedup:
    def test_same_content_emitted_once(self):
        """工具循环快照 + responder 透传快照（内容相同）→ 只发一条。"""
        dedup: set[str] = set()
        snap_loop = {"final_answer": "内网模型已成功连接。"}
        snap_responder = {"final_answer": "内网模型已成功连接。"}

        events1 = _convert_chunk("values", snap_loop, "run-1", set(), None, dedup)
        events2 = _convert_chunk("values", snap_responder, "run-1", set(), None, dedup)

        assert len(_messages(events1)) == 1
        assert _messages(events1)[0]["data"]["message"]["content"] == "内网模型已成功连接。"
        assert _messages(events2) == []  # 第二次同内容快照被去重

    def test_changed_content_still_emitted(self):
        """后续节点覆写出不同终答时照常发送（不误伤合法变更）。"""
        dedup: set[str] = set()
        events1 = _convert_chunk("values", {"final_answer": "第一版回答"}, "r", set(), None, dedup)
        events2 = _convert_chunk(
            "values", {"final_answer": "被覆写后的新终答"}, "r", set(), None, dedup
        )

        assert len(_messages(events1)) == 1
        assert len(_messages(events2)) == 1
        assert _messages(events2)[0]["data"]["message"]["content"] == "被覆写后的新终答"

    def test_refined_answer_reuses_message_id(self):
        """#142：工具循环先出原文，responder 补 clarify 块 → 内容变了但 id 不变。

        前端按 id 判重：id 相同 → update 原地覆盖；若 id 不同会 append 出第二条。
        """
        dedup: set[str] = set()
        msg_id: list[str] = []
        raw = "请选择风格：\n1. 简洁实用\n2. 视觉设计"
        refined = raw + "\n\n```clarify\n[{\"question\": \"请选择风格\"}]\n```"

        events1 = _convert_chunk("values", {"final_answer": raw}, "r", set(), None, dedup, msg_id)
        events2 = _convert_chunk("values", {"final_answer": refined}, "r", set(), None, dedup, msg_id)

        m1 = _messages(events1)
        m2 = _messages(events2)
        assert len(m1) == 1 and len(m2) == 1
        # 内容确实变了（逃过 #115 内容去重）……
        assert m1[0]["data"]["message"]["content"] == raw
        assert m2[0]["data"]["message"]["content"] == refined
        # ……但 message id 复用，前端据此 update 而非 append → 只有一条终答
        assert m1[0]["data"]["message"]["id"] == m2[0]["data"]["message"]["id"]
        assert len(msg_id) == 1  # 整个 run 只分配过一次 id

    def test_no_dedup_set_keeps_legacy_behavior(self):
        """未传去重集合（既有调用签名）→ 行为与旧版一致，每次快照都发。"""
        snap = {"final_answer": "回答"}
        events1 = _convert_chunk("values", snap, "r", set())
        events2 = _convert_chunk("values", snap, "r", set())
        assert len(_messages(events1)) == 1
        assert len(_messages(events2)) == 1

    def test_empty_final_answer_not_emitted(self):
        dedup: set[str] = set()
        events = _convert_chunk("values", {"final_answer": ""}, "r", set(), None, dedup)
        assert _messages(events) == []
