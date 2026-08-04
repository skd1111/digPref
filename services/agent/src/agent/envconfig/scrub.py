"""envconfig.scrub —— SecretStr ↔ Keyring 占位符 转换。

工作流：
    scrub(config)    →  把 SecretStr 替换为 `__KEYRING_REF:<account>__`，
                       永远不再含明文。
    restore(config, keyring_lookup)
                    →  把占位符按 account 还原成 SecretStr；
                       找不到对应 keyring 记录 → 抛 PlaceholderMissing。
"""
from __future__ import annotations

import re
from typing import Any

from pydantic import SecretStr

from .models import EnvConfig


# ---- 占位符格式 -----------------------------------------------------------

PLACEHOLDER_PREFIX = "__KEYRING_REF:"
PLACEHOLDER_SUFFIX = "__"

# `__KEYRING_REF:db.orders_pg.password__` —— 内部不允许含 : / 空格
_PLACEHOLDER_RE = re.compile(
    r"^" + re.escape(PLACEHOLDER_PREFIX) + r"([A-Za-z0-9._\-]+)" + re.escape(PLACEHOLDER_SUFFIX) + r"$"
)


def is_placeholder(value: str) -> bool:
    """判断一个字符串是否是合法的 Keyring 占位符。"""
    if not isinstance(value, str):
        return False
    return _PLACEHOLDER_RE.match(value) is not None


def parse_placeholder(value: str) -> str:
    """从占位符中提取 `account` 部分（如 `db.orders_pg.password`）。

    非占位符会抛 ValueError。
    """
    m = _PLACEHOLDER_RE.match(value)
    if not m:
        raise ValueError(f"非占位符: {value!r}")
    return m.group(1)


def make_placeholder(account: str) -> str:
    """生成占位符。account 必须符合 `[A-Za-z0-9._-]+`。"""
    if not re.fullmatch(r"[A-Za-z0-9._\-]+", account):
        raise ValueError(
            f"account 非法（只允许字母数字 . _ -）：{account!r}"
        )
    return f"{PLACEHOLDER_PREFIX}{account}{PLACEHOLDER_SUFFIX}"


# ---- scrub / restore ------------------------------------------------------


class PlaceholderMissing(KeyError):
    """restore 时发现占位符在 keyring_lookup 里没有对应值。"""

    def __init__(self, account: str, placeholder: str) -> None:
        super().__init__(account)
        self.account = account
        self.placeholder = placeholder

    def __str__(self) -> str:
        return (
            f"占位符 {self.placeholder!r} 找不到 keyring 里的对应值 "
            f"（account={self.account!r}）。请在系统 Keychain / Credential Manager "
            f"中先绑定后再导入。"
        )


def _walk_secret_paths(obj: Any, parents: tuple[str, ...]) -> list[tuple[tuple[str, ...], Any, str]]:
    """遍历 `EnvConfig` 对象，返回所有 SecretStr 字段的位置和值。

    返回 [(path_tuple, parent_obj, attr_name), ...]
    其中 `path_tuple` 是从 root 到 parent 的字段名序列。
    """
    out: list[tuple[tuple[str, ...], Any, str]] = []
    for parent_name, attr in parents:
        items = getattr(obj, parent_name, None)
        if items is None:
            continue
        # items 是 list（databases / api_gateways），每条都可能有 password/api_key
        if isinstance(items, list):
            for i, item in enumerate(items):
                # EnvConfig.secret_field_paths() 决定哪些 attr 是 SecretStr
                for sec_parent, sec_attr in EnvConfig.secret_field_paths():
                    if sec_parent == parent_name:
                        sec_value = getattr(item, sec_attr, None)
                        if isinstance(sec_value, SecretStr):
                            out.append(
                                (parents + (parent_name, str(i)), item, sec_attr)
                            )
    return out


def scrub(config: EnvConfig) -> dict[str, Any]:
    """把 EnvConfig 序列化成 dict，敏感字段全部替换为占位符。

    永远不返回明文。可直接 json.dumps / yaml.safe_dump 写到磁盘。

    model_dump(mode="json") 对 SecretStr 的行为：
        - 字段为 None  → 输出 None
        - 字段有值    → 输出 "**********"
    """
    dumped = config.model_dump(mode="json")
    for parent_name, attr in EnvConfig.secret_field_paths():
        items = dumped.get(parent_name) or []
        for item in items:
            secret = item.get(attr)
            if secret is None:
                # 字段未设置，保持 None
                continue
            # Pydantic 把 SecretStr 转成 '**********' —— 视为"该字段需要替换"
            name = item.get("name") or item.get("server_name")
            if not name:
                # 没名字的条目无法生成占位符，跳过（防御）
                continue
            account = f"{parent_name}.{name}.{attr}"
            item[attr] = make_placeholder(account)
    return dumped


def restore_secrets(
    config: EnvConfig,
    keyring_lookup: dict[str, str],
) -> EnvConfig:
    """把 EnvConfig 里的占位符还原成 SecretStr。

    Args:
        config: 从磁盘反序列化得到的 EnvConfig（SecretStr 字段是空值或 "**********"）
        keyring_lookup: account -> 明文密钥 的字典（调用方从 OS keychain 注入）

    Raises:
        PlaceholderMissing: 任一占位符在 lookup 里没有对应值
    """
    # 先用 model_dump 拿到底层结构
    for parent_name, attr in EnvConfig.secret_field_paths():
        items = getattr(config, parent_name, None) or []
        for item in items:
            current = getattr(item, attr, None)
            if current is None:
                continue
            # SecretStr 内部值
            try:
                val = current.get_secret_value()
            except Exception:
                val = ""
            if not val or val.startswith("***"):
                # 字段为空或 model_dump 占位 → 跳过
                continue
            if not is_placeholder(val):
                # 已是明文（不可能，但防御）
                continue
            account = parse_placeholder(val)
            if account not in keyring_lookup:
                raise PlaceholderMissing(account, val)
            setattr(item, attr, SecretStr(keyring_lookup[account]))
    return config
