"""Phase 5 V1 · TOTP MFA —— 时间型一次性密码（RFC 6238）。

V1 简化（生产 V1.5 接真 TOTP）：
  - 共享密钥存储 OS Keyring（每个用户名一个）
  - 当前时间窗口 ±1（30s 步长，3 窗口容忍）
  - 测试用固定密钥 `JBSWY3DPEHPK3PXP`（"Hello!123" base32）
  - 不读取用户密码 / 邮箱等敏感信息

CLAUDE.md §5 凭证保险箱红线：
  - 共享密钥存 OS Keyring 服务名 `com.eaide.desktop.audit.totp`
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import logging
import time
from typing import Optional


logger = logging.getLogger(__name__)


KEYRING_SERVICE_TOTP = "com.eaide.desktop.audit.totp"


# 测试用默认密钥（RFC 6238 示例 base32）
_DEFAULT_TEST_SECRET_B32 = "JBSWY3DPEHPK3PXP"


def _base32_decode(secret_b32: str) -> bytes:
    """Base32 解码（容错 padding）。"""
    s = secret_b32.strip().upper().replace(" ", "")
    # 补齐 padding
    pad_len = (-len(s)) % 8
    s = s + "=" * pad_len
    return base64.b32decode(s)


def _hotp(secret_b32: str, counter: int, digits: int = 6) -> str:
    """HOTP 算法（RFC 4226）—— TOTP 的基础。"""
    key = _base32_decode(secret_b32)
    counter_bytes = counter.to_bytes(8, byteorder="big")
    hmac_digest = hmac.new(key, counter_bytes, hashlib.sha1).digest()
    # 动态截断（RFC 4226 §5.3）
    offset = hmac_digest[-1] & 0x0F
    code_int = (
        (hmac_digest[offset] & 0x7F) << 24
        | (hmac_digest[offset + 1] & 0xFF) << 16
        | (hmac_digest[offset + 2] & 0xFF) << 8
        | (hmac_digest[offset + 3] & 0xFF)
    ) % (10 ** digits)
    return str(code_int).zfill(digits)


def generate_totp(secret_b32: str, timestamp: Optional[float] = None, digits: int = 6) -> str:
    """生成当前时间 TOTP。

    Args:
        secret_b32: Base32 编码的共享密钥。
        timestamp: Unix 时间戳（None = 当前时间）。
        digits: TOTP 位数（默认 6）。

    Returns:
        6 位数字字符串。
    """
    if timestamp is None:
        timestamp = time.time()
    counter = int(timestamp // 30)
    return _hotp(secret_b32, counter, digits)


def verify_totp(
    secret_b32: str,
    code: str,
    timestamp: Optional[float] = None,
    window: int = 1,
) -> bool:
    """验证 TOTP（允许 ±1 窗口）。

    Args:
        secret_b32: Base32 编码的共享密钥。
        code: 用户输入的 6 位数字。
        timestamp: Unix 时间戳（None = 当前时间）。
        window: 容忍窗口数（±1 即 90 秒容忍）。

    Returns:
        True if valid, False otherwise.
    """
    if not code or not code.isdigit():
        return False
    if timestamp is None:
        timestamp = time.time()
    base_counter = int(timestamp // 30)
    for offset in range(-window, window + 1):
        counter = base_counter + offset
        expected = _hotp(secret_b32, counter, len(code))
        if hmac.compare_digest(code, expected):
            return True
    return False


def get_or_create_user_secret(username: str) -> str:
    """从 Keyring 加载用户的 TOTP 共享密钥；不存在则创建并存储测试密钥。

    Args:
        username: 用户名（按用户名独立密钥）。

    Returns:
        Base32 编码的共享密钥。
    """
    try:
        import keyring  # type: ignore
        secret = keyring.get_password(KEYRING_SERVICE_TOTP, username)
    except Exception:
        secret = None

    if not secret:
        secret = _DEFAULT_TEST_SECRET_B32
        try:
            import keyring  # type: ignore
            keyring.set_password(KEYRING_SERVICE_TOTP, username, secret)
        except Exception as exc:
            logger.warning("audit_totp_secret_save_failed user=%s: %s", username, exc)

    return secret


def get_current_totp_for_user(username: str) -> str:
    """获取当前用户的 TOTP（仅用于 demo / 文档展示；生产 V1.5 不应暴露）。"""
    secret = get_or_create_user_secret(username)
    return generate_totp(secret)