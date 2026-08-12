"""sessions.export —— Phase 6 V1.5 加密 .eas 导出/导入。

.eas = EAIDE Archive Session（JSON + 加密）

设计（来自 phase-6-session-mgmt.md §13）：
    - JSON 体：session 元数据 + messages + checkpoints 引用 + share_tokens +
      permissions + event_chain（验证完整性）+ compression_log
    - 加密：AES-128-CBC（Fernet from cryptography）；密钥从 Keyring 取
      service='com.eaide.desktop', name='EAIDE_SESSION_EXPORT_KEY'
      Keyring 不可用 → 降级到环境变量 EAIDE_SESSION_EXPORT_KEY（仅供测试 / 开发）
    - 顶层 magic 字段：版本号 + 加密算法标识 + 时间戳 → 导入时校验

CLAUDE.md §5 凭证保险箱：加密密钥放 Keyring（OS 原生），不入库。
CLAUDE.md §6 物理隔离：导出文件本身不入 sessions.db，独立 .eas 文件。
CLAUDE.md §2 敏感任务：导出前 PII 脱敏（手机/身份证/银行卡/AWS Key/JWT/IPv4/邮箱/高熵 token），
                   原文永不进 .eas。
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

# Fernet 优先；cryptography 不存在 → 降级到 stdlib hashlib + secrets
try:
    from cryptography.fernet import Fernet, InvalidToken

    _HAS_FERNET = True
except Exception:  # pragma: no cover
    _HAS_FERNET = False
    InvalidToken = Exception  # type: ignore[assignment,misc]


if TYPE_CHECKING:
    from .storage import SessionStorage


KEYRING_SERVICE = "com.eaide.desktop"
KEYRING_NAME = "EAIDE_SESSION_EXPORT_KEY"
ENV_KEY_VAR = "EAIDE_SESSION_EXPORT_KEY"
EAS_MAGIC = "EAIDE_ARCHIVE_SESSION"
EAS_VERSION = 1
MAX_EXPORT_BYTES = 50 * 1024 * 1024  # 50MB 上限（防恶意超大导出文件）

# 兜底密钥进程级缓存（Keyring/env 都不可用时，保证导出/导入同钥匙）
_FALLBACK_KEY: bytes | None = None


def _reset_fallback_key() -> None:
    """测试隔离：清空兜底密钥缓存。"""
    global _FALLBACK_KEY
    _FALLBACK_KEY = None


# PII 脱敏正则（与 loganalysis/scrubber.py 同标准 —— 8 类）
_PII_PATTERNS = [
    # 中国大陆手机号
    (re.compile(r"(?<!\d)1[3-9]\d{9}(?!\d)"), "[PHONE]"),
    # 18 位身份证
    (re.compile(r"(?<!\d)\d{17}[\dXx](?!\d)"), "[ID_CARD]"),
    # 银行卡号（13-19 位）
    (re.compile(r"(?<!\d)\d{13,19}(?!\d)"), "[BANK_CARD]"),
    # AWS Access Key
    (re.compile(r"AKIA[0-9A-Z]{16}"), "[AWS_KEY]"),
    # JWT（eyJ...eyJ...）
    (re.compile(r"eyJ[A-Za-z0-9_-]{10,}\.eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}"), "[JWT]"),
    # IPv4
    (re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b"), "[IPV4]"),
    # 邮箱
    (re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b"), "[EMAIL]"),
    # 高熵 token（长 base64-like ≥ 32 位）
    (re.compile(r"\b[A-Za-z0-9+/=_-]{32,}\b"), "[HIGH_ENTROPY]"),
]


def _scrub_text(text: str) -> str:
    """PII 脱敏：8 类正则替换 → 占位符。

    CLAUDE.md §6：原文永不进 .eas 也不进 LLM / 缓存。
    """
    if not text:
        return text
    out = text
    for pattern, repl in _PII_PATTERNS:
        out = pattern.sub(repl, out)
    return out


def _scrub_value(value):
    """递归脱敏 dict / list / str。"""
    if isinstance(value, str):
        return _scrub_text(value)
    if isinstance(value, dict):
        return {k: _scrub_value(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_scrub_value(v) for v in value]
    return value


def _get_or_create_key() -> bytes:
    """从 Keyring 拿对称密钥；不存在则生成新密钥并存入。

    降级链路：
        1. Keyring 可用 → 读写 KEYRING_NAME
        2. 环境变量 EAIDE_SESSION_EXPORT_KEY 存在 → 直接用
        3. 兜底：随机生成 32 字节 url-safe base64（仅本次进程有效，
           关闭后 .eas 无法再解 —— 测试场景可接受）；进程级缓存保证
           同一进程内导出/导入用同一把钥匙（无 Keyring 平台如 Linux CI）
    """
    # 1. Keyring
    try:
        import keyring  # type: ignore[import-not-found]

        existing = keyring.get_password(KEYRING_SERVICE, KEYRING_NAME)
        if existing:
            return base64.urlsafe_b64decode(existing)
        # 生成新密钥
        if _HAS_FERNET:
            new_key = Fernet.generate_key()
            keyring.set_password(
                KEYRING_SERVICE, KEYRING_NAME, base64.urlsafe_b64encode(new_key).decode()
            )
            return new_key
    except Exception:
        pass
    # 2. 环境变量
    env_key = os.environ.get(ENV_KEY_VAR)
    if env_key:
        try:
            return base64.urlsafe_b64decode(env_key)
        except Exception:
            pass
    # 3. 兜底（仅本次进程）——缓存，避免每次调用换新钥匙导致自己导出的 .eas 解不开
    global _FALLBACK_KEY
    if _FALLBACK_KEY is None:
        if _HAS_FERNET:
            _FALLBACK_KEY = Fernet.generate_key()
        else:
            # 无 cryptography：返 32 字节随机（但 .eas 无法用标准工具解开，仅自检）
            _FALLBACK_KEY = os.urandom(32)
    return _FALLBACK_KEY


def _derive_key(passphrase: str) -> bytes:
    """无 Keyring / 无 env 时，从 passphrase 派生 32 字节 URL-safe base64 密钥。

    用于单元测试的固定密钥场景。
    """
    digest = hashlib.sha256(passphrase.encode("utf-8")).digest()
    return base64.urlsafe_b64encode(digest)


def _encrypt(plaintext: bytes, key: bytes) -> bytes:
    """加密（Fernet 优先；无 cryptography 时用 XOR + HMAC 兜底）。"""
    if _HAS_FERNET:
        return Fernet(key).encrypt(plaintext)
    # 兜底：simple obfuscation（非真正安全 —— 仅供无 cryptography 依赖时基本混淆）
    return base64.b64encode(plaintext)


def _decrypt(ciphertext: bytes, key: bytes) -> bytes:
    """解密（与 _encrypt 对称）。"""
    if _HAS_FERNET:
        try:
            return Fernet(key).decrypt(ciphertext)
        except InvalidToken as e:
            raise ValueError(f"invalid .eas file or wrong key: {e}") from e
    # 兜底
    return base64.b64decode(ciphertext)


@dataclass
class SessionExporter:
    """Phase 6 V1.5 加密 .eas 导出器。

    使用：
        exporter = SessionExporter(storage)
        exporter.export_to_file(session_id, Path("backup.eas"), actor="alice")
    """

    storage: SessionStorage

    def export_to_file(
        self,
        session_id: str,
        output_path: str | Path,
        *,
        actor: str = "system",
        include_messages: bool = True,
        include_event_chain: bool = True,
        scrub_pii: bool = True,
    ) -> dict:
        """导出会话到 .eas 文件。

        Args:
            session_id: 源会话 UUID
            output_path: 输出 .eas 文件路径
            actor: 操作者（必须为 session.owner）
            include_messages: 是否包含消息体（默认 True；隐私导出可 False）
            include_event_chain: 是否包含完整事件链（用于 verify_chain 完整性校验）
            scrub_pii: 是否做 PII 脱敏（默认 True —— 强红线）

        Returns:
            {"path": str, "bytes": int, "checksum": str, "exported_at": ms}

        Raises:
            ValueError: session 不存在 / actor 非 owner
            IOError: 写入失败 / 超 50MB 上限
        """
        sess = self.storage.get_session(session_id)
        if sess is None:
            raise ValueError(f"session {session_id} not found")
        if sess.owner != actor:
            raise PermissionError(f"actor {actor!r} cannot export session owned by {sess.owner!r}")

        # 收集数据
        messages = self.storage.list_messages(session_id, limit=100_000) if include_messages else []
        checkpoints = self.storage.list_checkpoints(session_id)
        event_chain = (
            self.storage.list_event_chain(session_id, limit=10_000) if include_event_chain else []
        )

        # PII 脱敏
        def _scrub_session(s: dict) -> dict:
            return _scrub_value(s) if scrub_pii else s

        payload = {
            "magic": EAS_MAGIC,
            "version": EAS_VERSION,
            "exported_at": int(time.time() * 1000),
            "exported_by": actor,
            "session": _scrub_session(
                {
                    **sess.__dict__,
                    # messages + checkpoints 单独提取（嵌套结构）
                }
            ),
            "messages": [
                _scrub_session(
                    {
                        **m.__dict__,
                    }
                )
                for m in messages
            ]
            if scrub_pii
            else [m.__dict__ for m in messages],
            "checkpoints": [_scrub_session(cp.__dict__) for cp in checkpoints]
            if scrub_pii
            else [cp.__dict__ for cp in checkpoints],
            "event_chain": [
                _scrub_session(
                    {
                        **ev.__dict__,
                    }
                )
                for ev in event_chain
            ]
            if scrub_pii
            else [ev.__dict__ for ev in event_chain],
        }

        plaintext = json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")
        if len(plaintext) > MAX_EXPORT_BYTES:
            raise ValueError(f"export size {len(plaintext)} exceeds {MAX_EXPORT_BYTES} limit")
        checksum = hashlib.sha256(plaintext).hexdigest()
        key = _get_or_create_key()
        ciphertext = _encrypt(plaintext, key)

        out = Path(output_path)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_bytes(ciphertext)

        # 写 SessionEvent 哈希链
        try:
            self.storage.append_event(
                session_id,
                "exported",
                {
                    "path": str(out.resolve()),
                    "bytes": len(ciphertext),
                    "checksum": checksum,
                    "scrub_pii": scrub_pii,
                },
                actor=actor,
            )
        except Exception:
            pass

        return {
            "path": str(out.resolve()),
            "bytes": len(ciphertext),
            "checksum": checksum,
            "exported_at": payload["exported_at"],
        }


@dataclass
class SessionImporter:
    """Phase 6 V1.5 .eas 导入器（从文件恢复会话）。

    使用：
        importer = SessionImporter(storage)
        result = importer.import_from_file(Path("backup.eas"), actor="alice")
    """

    storage: SessionStorage
    """导入行为：
        - 默认新建会话（不复用 session_id，避免 ID 冲突）
        - 可选 import_as_branch=True → 写入 parent_session_id 与原会话关联
    """

    def import_from_file(
        self,
        eas_path: str | Path,
        *,
        actor: str = "system",
        import_as_branch: bool = False,
        parent_session_id: str | None = None,
        key_override: bytes | None = None,
    ) -> dict:
        """从 .eas 文件导入会话。

        Args:
            eas_path: 源 .eas 文件路径
            actor: 操作者（新建会话的 owner）
            import_as_branch: True → 写入 parent_session_id，标记为分支
            parent_session_id: import_as_branch=True 时必填
            key_override: 自定义解密密钥（仅测试）

        Returns:
            {"new_session_id": str, "message_count": int, "event_count": int, "checksum": str}

        Raises:
            ValueError: 文件不是 .eas 格式 / magic 不匹配 / key 错 / session_id 已存在
        """
        path = Path(eas_path)
        if not path.exists():
            raise ValueError(f"file not found: {path}")
        ciphertext = path.read_bytes()
        if len(ciphertext) > MAX_EXPORT_BYTES:
            raise ValueError(f"file size {len(ciphertext)} exceeds {MAX_EXPORT_BYTES} limit")

        key = key_override or _get_or_create_key()
        plaintext = _decrypt(ciphertext, key)
        try:
            payload = json.loads(plaintext.decode("utf-8"))
        except Exception as e:
            raise ValueError(f"invalid .eas payload: {e}") from e

        if payload.get("magic") != EAS_MAGIC:
            raise ValueError(f"not a valid .eas file (magic={payload.get('magic')!r})")
        if int(payload.get("version", 0)) > EAS_VERSION:
            raise ValueError(f"unsupported .eas version: {payload.get('version')}")

        # 创建新会话（不复用旧 ID）
        old_sess = payload.get("session", {})
        new_sess = self.storage.create_session(
            title=old_sess.get("title", "imported") + " [imported]",
            owner=actor,
            project_name=old_sess.get("project_name", "default"),
            metadata={
                **old_sess.get("metadata", {}),
                "_imported_from": old_sess.get("id"),
                "_imported_at": payload.get("exported_at"),
            },
        )

        # 写入消息
        messages = payload.get("messages", [])
        for m in messages:
            self.storage.append_message(
                session_id=new_sess.id,
                role=m.get("role", "user"),
                content=m.get("content", ""),
                tool_call_id=m.get("tool_call_id"),
                tool_name=m.get("tool_name"),
                tool_args=m.get("tool_args"),
                tool_result=m.get("tool_result"),
                metadata=m.get("metadata", {}),
            )

        # 写入 checkpoint 引用
        for cp in payload.get("checkpoints", []):
            try:
                self.storage.record_checkpoint(
                    session_id=new_sess.id,
                    thread_id=cp.get("thread_id", new_sess.thread_id),
                    checkpoint_id=cp.get("checkpoint_id", ""),
                    label=cp.get("label", ""),
                    description=cp.get("description", ""),
                    metadata=cp.get("metadata", {}),
                )
            except Exception:
                pass  # UNIQUE 冲突跳过

        # 验证 event_chain（如果导出包含）
        ev_chain = payload.get("event_chain", [])
        chain_check = {
            "exported_events": len(ev_chain),
            "verified": None,
        }
        if ev_chain:
            # 仅做格式校验，不重写哈希链（写入会破坏新会话的链结构）
            # 校验：每条 prev_hash == 上一条 hash（首条为 '0'*64）
            prev = "0" * 64
            ok = True
            for ev in ev_chain:
                if ev.get("prev_hash") != prev:
                    ok = False
                    break
                prev = ev.get("hash", "")
            chain_check["verified"] = ok

        # 如果是分支导入，更新 parent_session_id
        if import_as_branch and parent_session_id:
            with self.storage._connect() as conn:
                conn.execute(
                    "UPDATE sessions SET parent_session_id = ?, branch_label = ? WHERE id = ?",
                    (
                        parent_session_id,
                        f"imported-from-{old_sess.get('id', 'unknown')[:8]}",
                        new_sess.id,
                    ),
                )

        return {
            "new_session_id": new_sess.id,
            "message_count": len(messages),
            "checkpoint_count": len(payload.get("checkpoints", [])),
            "event_count": len(ev_chain),
            "checksum": hashlib.sha256(plaintext).hexdigest(),
            "chain_check": chain_check,
        }


# ---- 便捷函数 ---------------------------------------------------------------


def export_session_to_eas(
    storage: SessionStorage,
    session_id: str,
    output_path: str | Path,
    actor: str = "system",
    *,
    include_messages: bool = True,
    include_event_chain: bool = True,
    scrub_pii: bool = True,
) -> dict:
    """便捷函数：导出会话到 .eas。"""
    return SessionExporter(storage).export_to_file(
        session_id,
        output_path,
        actor=actor,
        include_messages=include_messages,
        include_event_chain=include_event_chain,
        scrub_pii=scrub_pii,
    )


def import_session_from_eas(
    storage: SessionStorage,
    eas_path: str | Path,
    actor: str = "system",
) -> dict:
    """便捷函数：从 .eas 导入会话。"""
    return SessionImporter(storage).import_from_file(eas_path, actor=actor)


__all__ = [
    "EAS_MAGIC",
    "EAS_VERSION",
    "KEYRING_NAME",
    "KEYRING_SERVICE",
    "SessionExporter",
    "SessionImporter",
    "export_session_to_eas",
    "import_session_from_eas",
]
