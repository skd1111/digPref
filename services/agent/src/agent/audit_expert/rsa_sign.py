"""Phase 5 V1 · RSA 数字签名 —— 替换 V0 SHA-256 链式 hash。

V0 SHA-256 链式 hash 仅防偶发篡改（攻击者同时改 prev_hash + signature_hash 才能破解）。
V1 RSA 签名（2048 bit）才满足金融审计要求（不可伪造 + 公网可验证）。

密钥管理（CLAUDE.md §5 红线）：
  - 签名私钥（signing_key）：存 OS Keyring 服务名 `com.eaide.desktop.audit`
  - 验证公钥（verification_key）：存 `audit_expert.db.signing_public_key`
  - Keychain 防作弊：tier 3 监控用户对 keyring 的写入

依赖：cryptography >= 41
"""

from __future__ import annotations

import base64
import logging

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa
from cryptography.hazmat.primitives.asymmetric.rsa import (
    RSAPrivateKey,
    RSAPublicKey,
)

logger = logging.getLogger(__name__)


# Keyring 服务名（与 credentials.rs 共享命名空间）
KEYRING_SERVICE = "com.eaide.desktop.audit"
KEYRING_USER_SIGNING = "audit_signing_private_key"


# 缓存（避免每次签名/验签都从 keyring 加载）
_signing_key_cache: RSAPrivateKey | None = None
_verification_key_cache: RSAPublicKey | None = None


def _load_or_generate_signing_key() -> RSAPrivateKey:
    """从 OS Keyring 加载签名私钥；不存在则生成 2048 bit RSA 密钥并存入。

    Returns:
        RSAPrivateKey 实例。
    """
    global _signing_key_cache
    if _signing_key_cache is not None:
        return _signing_key_cache

    try:
        import keyring  # type: ignore

        pem_str = keyring.get_password(KEYRING_SERVICE, KEYRING_USER_SIGNING)
    except Exception:
        pem_str = None

    if pem_str:
        try:
            private_key = serialization.load_pem_private_key(pem_str.encode("utf-8"), password=None)
            if isinstance(private_key, RSAPrivateKey):
                _signing_key_cache = private_key
                return private_key
        except Exception as exc:
            logger.warning("audit_signing_key_load_failed: %s; regenerating", exc)

    # 生成新密钥
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    pem = private_key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    ).decode("utf-8")
    try:
        import keyring  # type: ignore

        keyring.set_password(KEYRING_SERVICE, KEYRING_USER_SIGNING, pem)
    except Exception as exc:
        logger.warning("audit_signing_key_save_failed: %s", exc)
    _signing_key_cache = private_key
    return private_key


def get_verification_public_key_pem() -> str:
    """获取签名公钥 PEM（存入 audit_expert.db 让前端可下载验证）。"""
    private_key = _load_or_generate_signing_key()
    public_key = private_key.public_key()
    return public_key.public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    ).decode("utf-8")


def sign_payload(payload: str) -> str:
    """对 payload 字符串签名（V1 替换 V0 SHA-256 链式 hash）。

    Args:
        payload: 待签名字符串（建议 `<action_id>|<task_id>|<prev_hash>|<timestamp>`）。

    Returns:
        base64-encoded 签名（公钥可解）。
    """
    private_key = _load_or_generate_signing_key()
    signature = private_key.sign(
        payload.encode("utf-8"),
        padding.PSS(
            mgf=padding.MGF1(hashes.SHA256()),
            salt_length=padding.PSS.MAX_LENGTH,
        ),
        hashes.SHA256(),
    )
    return base64.b64encode(signature).decode("ascii")


def verify_payload_signature(payload: str, signature_b64: str) -> bool:
    """验证签名。

    Args:
        payload: 待验证字符串。
        signature_b64: base64-encoded 签名。

    Returns:
        True if valid, False otherwise.
    """
    try:
        signature = base64.b64decode(signature_b64.encode("ascii"))
        public_key_pem = get_verification_public_key_pem()
        public_key = serialization.load_pem_public_key(public_key_pem.encode("utf-8"))
        if not isinstance(public_key, RSAPublicKey):
            return False
        public_key.verify(
            signature,
            payload.encode("utf-8"),
            padding.PSS(
                mgf=padding.MGF1(hashes.SHA256()),
                salt_length=padding.PSS.MAX_LENGTH,
            ),
            hashes.SHA256(),
        )
        return True
    except Exception:
        return False


def reset_signing_key_cache() -> None:
    """测试 hook：清除缓存。"""
    global _signing_key_cache, _verification_key_cache
    _signing_key_cache = None
    _verification_key_cache = None
