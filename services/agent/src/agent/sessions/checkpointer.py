"""sessions.checkpointer —— Phase 6 V0 LangGraph Checkpoint 封装。

设计（来自 phase-6-session-mgmt.md §3.2）：
    - V0 用 LangGraph `MemorySaver`（langgraph.checkpoint.memory）—— 进程内
      内存版，**重启丢失**，但能跑通 API + 框架链路。
    - V1 替换为 `SqliteSaver`（需要装 `langgraph-checkpoint-sqlite`），持久化到
      `sessions.db`，与 SessionStorage 同库（或独立 sessions_checkpoints.db）。

CLAUDE.md §2 红线：
    - checkpoint 内容可能含敏感上下文（PII / SQL 错误信息）—— V1 写盘前
      必须经 PII 脱敏；V0 MemorySaver 进程退出即清空，零泄漏
    - thread_id = session_id（一对一），保证会话隔离
    - checkpoint 操作失败不抛错（best-effort，agent 不应被 checkpoint 阻塞）

不在 V0 内（V1 补）：
    - SqliteSaver 持久化
    - checkpoint 摘要 / 清理老旧 checkpoint
    - 时间旅行（从历史 checkpoint 派生分支）
"""
from __future__ import annotations

import logging
from typing import Optional

from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.checkpoint.memory import MemorySaver

from .storage import SessionStorage

logger = logging.getLogger(__name__)


class SessionCheckpointer:
    """V0 MemorySaver wrapper —— 注入 LangGraph compiled graph。

    流程：
        cp = SessionCheckpointer(storage)
        checkpointer = cp.saver  # 透传给 graph.compile(checkpointer=cp.saver)
        config = {"configurable": {"thread_id": session_id}}
        await graph.ainvoke(inputs, config=config)
        # 自动 record_checkpoint 引用
    """

    def __init__(self, storage: SessionStorage, saver: Optional[BaseCheckpointSaver] = None):
        self._storage = storage
        # V0 默认 MemorySaver；V1 替换 SqliteSaver
        self._saver: BaseCheckpointSaver = saver or MemorySaver()

    @property
    def saver(self) -> BaseCheckpointSaver:
        """透传给 LangGraph compiled graph。"""
        return self._saver

    def save_reference(
        self,
        session_id: str,
        thread_id: str,
        checkpoint_id: str,
        label: str = "",
        description: str = "",
    ) -> int:
        """在 storage.session_checkpoints 表记录一次 checkpoint 引用。

        实际 checkpoint 数据存在 MemorySaver / SqliteSaver 里（langgraph 自管）；
        本表只存 user-friendly metadata（label / description）。
        """
        try:
            cp = self._storage.record_checkpoint(
                session_id=session_id,
                thread_id=thread_id,
                checkpoint_id=checkpoint_id,
                label=label,
                description=description,
            )
            logger.info(
                "[session.checkpointer] saved ref session=%s thread=%s cp=%s label=%s",
                session_id, thread_id, checkpoint_id, label,
            )
            return cp.id
        except Exception as e:  # noqa: BLE001 —— best-effort
            logger.warning("[session.checkpointer] save_reference failed: %s", e)
            return -1

    def list_checkpoints(self, session_id: str) -> list:
        """列出会话的所有 checkpoint 引用（按时间倒序）。"""
        return self._storage.list_checkpoints(session_id)

    def get_tuple(self, thread_id: str, checkpoint_id: str | None = None):
        """从 underlying saver 拉历史 checkpoint tuple（V1 时间旅行用）。

        V0：MemorySaver.get_tuple() 直接返回。
        V1：SqliteSaver 同接口。
        """
        config = {"configurable": {"thread_id": thread_id}}
        if checkpoint_id:
            config["configurable"]["checkpoint_id"] = checkpoint_id
        return self._saver.get_tuple(config)