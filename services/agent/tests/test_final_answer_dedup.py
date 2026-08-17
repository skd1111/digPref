"""values 快照 final_answer 去重测试（BUGFIX #115，2026-08-17）。

背景：stream_mode=["values"] 下，工具循环（tool_orchestrator）写入
final_answer 后产生一次快照，responder 透传同一 final_answer 又产生一次
快照；此前每个带 final_answer 的快照都 emit 一条 message（各带新 uuid），
前端按 id 判重 → 同一回答在对话里出现两次（用户两次实测复现）。

覆盖：
    - 同一 final_answer 内容的连续快照只发一条 message
    - final_answer 内容变化（后续节点覆写为新终答）仍会发送
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
