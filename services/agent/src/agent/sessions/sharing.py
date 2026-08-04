"""sessions.sharing —— Phase 6 V1.5 共享权限矩阵。

设计（来自 phase-6-session-mgmt.md §9.1 / §13 验收）：
    - 权限字面量：read / write（owner 字段隐含 owner 全权）
    - share_token：UUID4 hex 字符串 + 过期时间 + 创建时间；用于跨用户分享会话
    - 共享字典：sessions.permissions_json 内嵌 {"actor": "read"|"write"}
    - 与 Phase 10 IAM 严格独立可降级：
        * V0/V1.5：actor 是字符串 user_id（来自 Keyring / 系统登录）
        * V2 接 IAM：actor 是 IAM user_id，但接口不变

CLAUDE.md §1 HITL 不可绕过：本模块的 write 操作本身不触发 HITL
（分享只是元数据），但会话内的写操作仍走 hitl_gate。
CLAUDE.md §5 凭证保险箱：share_token 不放 Keyring（仅会话级标识），
解密用对称密钥走 Keyring 一次性取。
"""
from __future__ import annotations

from typing import TYPE_CHECKING

from .models import SharePermission, ShareToken

if TYPE_CHECKING:
    from .storage import SessionStorage


class SessionAccessDenied(PermissionError):
    """会话权限被拒（HTTP 403 语义）。"""

    def __init__(self, session_id: str, actor: str, required: SharePermission):
        self.session_id = session_id
        self.actor = actor
        self.required = required
        super().__init__(
            f"actor {actor!r} lacks {required!r} permission on session {session_id}"
        )


def check_session_access(
    storage: "SessionStorage",
    session_id: str,
    actor: str,
    required: SharePermission = "read",
) -> bool:
    """便捷函数：检查 actor 对 session 的权限（V0/V1.5 字符串 actor）。

    Returns:
        True 允许访问；False 拒绝（不抛异常）
    """
    try:
        return storage.check_access(session_id, actor, required)
    except Exception:
        return False


class ShareManager:
    """共享权限矩阵的高层管理器。

    包装 SessionStorage 的 4 个 V1.5 共享方法 + 业务校验。
    不持有状态（每次调用走 storage）。
    """

    def __init__(self, storage: "SessionStorage"):
        self.storage = storage

    # ---- share_token CRUD -----------------------------------------------

    def create_share_token(
        self,
        session_id: str,
        permission: SharePermission = "read",
        expires_in_ms: int | None = None,
        actor: str = "system",
    ) -> ShareToken:
        """创建分享令牌（返回 token 字符串给调用方，调用方负责转发给被分享者）。

        Args:
            session_id: 目标会话 UUID
            permission: 'read' (只读) / 'write' (可追加消息)
            expires_in_ms: 过期时间（相对当前时刻的毫秒数）；None = 永不过期
            actor: 操作者用户名（必须为 session.owner，否则拒绝）

        Returns:
            ShareToken（含 token 字符串）

        Raises:
            PermissionError: actor 非 session.owner
            ValueError: session_id 不存在
        """
        sess = self.storage.get_session(session_id)
        if sess is None:
            raise ValueError(f"session {session_id} not found")
        if sess.owner != actor:
            raise SessionAccessDenied(session_id, actor, "write")
        expires_at = None
        if expires_in_ms and int(expires_in_ms) > 0:
            from .storage import now_ms
            expires_at = now_ms() + int(expires_in_ms)
        return self.storage.add_share_token(
            session_id, permission, expires_at, actor=actor,
        )

    def revoke_share_token(
        self,
        session_id: str,
        token: str,
        actor: str = "system",
    ) -> bool:
        """撤销分享令牌（只有 session.owner 能撤销）。"""
        sess = self.storage.get_session(session_id)
        if sess is None:
            return False
        if sess.owner != actor:
            raise SessionAccessDenied(session_id, actor, "write")
        return self.storage.revoke_share_token(session_id, token, actor=actor)

    # ---- 权限授予（owner 才能操作）-------------------------------------

    def grant(
        self,
        session_id: str,
        target_actor: str,
        permission: SharePermission,
        granter: str,
    ) -> bool:
        """授予 target_actor 对 session 的 permission。"""
        return self.storage.grant_permission(
            session_id, target_actor, permission, granter=granter,
        )

    # ---- 权限检查 + 装饰器辅助 ----------------------------------------

    def require_access(
        self,
        session_id: str,
        actor: str,
        required: SharePermission = "read",
    ) -> None:
        """校验权限，失败抛 SessionAccessDenied。"""
        if not self.storage.check_access(session_id, actor, required):
            raise SessionAccessDenied(session_id, actor, required)

    # ---- 查询辅助 ----------------------------------------------------

    def list_share_tokens(self, session_id: str, actor: str = "system") -> list[dict]:
        """列出会话的全部 share_token（仅 owner 可见，token 完整保留）。

        非 owner 调用 → 返回空列表（不泄露）。
        """
        sess = self.storage.get_session(session_id)
        if sess is None or sess.owner != actor:
            return []
        return list(sess.share_tokens)

    def list_permissions(self, session_id: str, actor: str = "system") -> dict[str, str]:
        """列出 permissions_json（仅 owner 可见）。"""
        sess = self.storage.get_session(session_id)
        if sess is None or sess.owner != actor:
            return {}
        return dict(sess.permissions)


__all__ = [
    "ShareManager",
    "SessionAccessDenied",
    "check_session_access",
]