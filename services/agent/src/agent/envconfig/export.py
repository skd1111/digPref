"""envconfig.export —— 多环境配置的导入/导出。

威胁模型：
    - 导出的文件可能流传到企业内其他机器 / 知识库
    - 严禁在导出文件里包含明文密钥
    - 文件本身用 Fernet 对称加密（key 由用户在导出时输入的 passphrase 派生）

流程：
    export_config(env, passphrase) →
        scrub 出脱敏 dict → 转 YAML → Fernet(passphrase-derived-key).encrypt → 写文件

    import_config(path, passphrase) →
        读文件 → Fernet.decrypt → 解析 YAML → 校验每个占位符 → 返回带占位符的 EnvConfig
        （调用方拿到 EnvConfig 后负责 restore_secrets + keychain 注入）

文件格式：
    - 加密：YAML 文本先序列化 → UTF-8 字节 → Fernet 加密 → bytes
    - 文件以 magic prefix `EAIDE-ENC-V1:` 开头 + base64 密文
    - 未加密：明文 YAML 文本（不推荐，但保留以方便开发者查看）
"""
from __future__ import annotations

import base64
import hashlib
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml
from cryptography.fernet import Fernet, InvalidToken

from .models import EnvConfig
from .scrub import PlaceholderMissing, is_placeholder, scrub


MAGIC_PREFIX = "EAIDE-ENC-V1:"


# ---- 加密辅助 ------------------------------------------------------------


def _derive_key(passphrase: str, salt: bytes) -> bytes:
    """从 passphrase 派生 Fernet key（32 字节 base64）。"""
    hkdf = hashlib.pbkdf2_hmac(
        "sha256", passphrase.encode("utf-8"), salt, 200_000
    )
    return base64.urlsafe_b64encode(hkdf[:32])


def _fernet_from_passphrase(passphrase: str, salt: bytes) -> Fernet:
    return Fernet(_derive_key(passphrase, salt))


# ---- 导出 ----------------------------------------------------------------


@dataclass
class ExportResult:
    path: Path
    plaintext_bytes: int  # 加密前明文 YAML 字节数
    ciphertext_bytes: int
    env_count: int  # 包含多少个环境
    placeholder_count: int  # 占位符总数（确认所有密钥都已 scrub）


def _config_to_yaml(configs: list[EnvConfig]) -> tuple[str, int, int]:
    """把 EnvConfig 列表 → YAML 文本，返回 (yaml_text, placeholder_count, env_count)。

    严格走 scrub —— 任何在 dumped 里的明文都视为 bug，立即报错。
    """
    dumped: list[dict[str, Any]] = []
    placeholder_count = 0
    for cfg in configs:
        scrubbed = scrub(cfg)
        dumped.append(
            {
                "environment": str(cfg.environment),
                "label": cfg.label,
                "description": cfg.description,
                "config": scrubbed,
            }
        )
    yaml_text = yaml.safe_dump(
        {"schema_version": 1, "environments": dumped},
        allow_unicode=True,
        sort_keys=False,
    )

    # 二次校验：YAML 文本里不能出现任何占位符之外的明文 secret 形态
    placeholder_count = sum(yaml_text.count(p) for p in _iter_placeholder_strings())
    if _looks_like_plaintext_secret(yaml_text):
        raise ValueError(
            "检测到 YAML 文本中可能包含明文密钥（违反安全红线）。已阻止导出。"
        )
    return yaml_text, placeholder_count, len(configs)


def _iter_placeholder_strings() -> list[str]:
    """所有可能的占位符样式，用于统计。"""
    return ["__KEYRING_REF:"]


def _looks_like_plaintext_secret(text: str) -> bool:
    """启发式：YAML 里出现 "password: realvalue" / "api_key: realvalue" 视为泄漏。"""
    needles = ("password:", "api_key:", "ssh_key:", "ssh_passphrase:", "token:")
    for line in text.splitlines():
        stripped = line.lstrip()
        if not stripped or stripped.startswith("#"):
            continue
        for n in needles:
            if stripped.lower().startswith(n):
                value = stripped[len(n):].strip()
                # 占位符或空值不算泄漏
                if not value or is_placeholder(value) or value in ('""', "''", "null", "~"):
                    continue
                return True
    return False


def export_configs(
    configs: list[EnvConfig],
    dest_path: str | Path,
    passphrase: str,
) -> ExportResult:
    """把多个环境的配置导出到一个加密文件。"""
    if not passphrase:
        raise ValueError("passphrase 不能为空")

    yaml_text, placeholder_count, env_count = _config_to_yaml(configs)
    plaintext_bytes = len(yaml_text.encode("utf-8"))

    # 加密：salt 16 字节随机
    salt = os.urandom(16)
    f = _fernet_from_passphrase(passphrase, salt)
    ciphertext = f.encrypt(yaml_text.encode("utf-8"))
    blob = MAGIC_PREFIX.encode("ascii") + salt + ciphertext

    dest = Path(dest_path)
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(blob)
    return ExportResult(
        path=dest,
        plaintext_bytes=plaintext_bytes,
        ciphertext_bytes=len(blob),
        env_count=env_count,
        placeholder_count=placeholder_count,
    )


# ---- 导入 ----------------------------------------------------------------


@dataclass
class ImportResult:
    configs: list[EnvConfig]
    placeholders: list[str]  # 所有占位符 account 列表（去重）—— 给 UI 提示
    env_count: int


def import_configs(
    src_path: str | Path,
    passphrase: str,
    *,
    plaintext_ok: bool = False,
) -> ImportResult:
    """从加密文件导入配置。

    Args:
        src_path: 文件路径
        passphrase: 用户提供的 passphrase
        plaintext_ok: 允许读取非加密的明文 YAML（仅用于开发；生产不推荐）

    Returns:
        ImportResult，包含解析出的 EnvConfig 列表（SecretStr 字段还是占位符，
        调用方需调 restore_secrets() + 从 keychain 注入明文）。

    Raises:
        ValueError: 文件格式错 / passphrase 错 / 解析失败
        PlaceholderMissing: —— 不会在此抛（这里只校验占位符格式合法性；
            真正缺值是 restore_secrets 时才会查出来）
    """
    src = Path(src_path)
    if not src.exists():
        raise FileNotFoundError(f"文件不存在: {src}")
    blob = src.read_bytes()

    if blob.startswith(MAGIC_PREFIX.encode("ascii")):
        if not passphrase:
            raise ValueError("加密文件需要 passphrase")
        salt = blob[len(MAGIC_PREFIX.encode("ascii")) : len(MAGIC_PREFIX.encode("ascii")) + 16]
        ciphertext = blob[len(MAGIC_PREFIX.encode("ascii")) + 16 :]
        try:
            f = _fernet_from_passphrase(passphrase, salt)
            plaintext = f.decrypt(ciphertext)
        except InvalidToken:
            raise ValueError("passphrase 错误或文件已损坏") from None
        yaml_text = plaintext.decode("utf-8")
    elif plaintext_ok:
        # 开发模式：接受明文 YAML
        yaml_text = blob.decode("utf-8")
    else:
        raise ValueError(
            "文件不是 EAIDE 加密格式，且未启用 plaintext_ok。"
            "请确认 passphrase 正确或这是 EAIDE 导出的文件。"
        )

    # 解析 YAML
    try:
        data = yaml.safe_load(yaml_text)
    except yaml.YAMLError as e:
        raise ValueError(f"YAML 解析失败: {e}") from e

    if not isinstance(data, dict) or "environments" not in data:
        raise ValueError("导出文件结构不合法（缺 environments 字段）")

    # 校验：所有 secrets 字段都必须是占位符
    configs: list[EnvConfig] = []
    placeholders: set[str] = set()
    for entry in data["environments"]:
        # 扫描所有 string 字段，把占位符收集起来
        _scan_placeholders(entry, placeholders)
        try:
            cfg = EnvConfig.model_validate(entry["config"])
        except Exception as e:
            raise ValueError(
                f"环境 {entry.get('environment', '?')} 配置解析失败: {e}"
            ) from e
        configs.append(cfg)

    return ImportResult(
        configs=configs,
        placeholders=sorted(placeholders),
        env_count=len(configs),
    )


def _scan_placeholders(obj: Any, out: set[str]) -> None:
    """递归扫描 dict / list / str，把所有占位符加入 out。"""
    if isinstance(obj, dict):
        for v in obj.values():
            _scan_placeholders(v, out)
    elif isinstance(obj, list):
        for v in obj:
            _scan_placeholders(v, out)
    elif isinstance(obj, str) and is_placeholder(obj):
        # 提取 account 名
        from .scrub import parse_placeholder

        out.add(parse_placeholder(obj))
