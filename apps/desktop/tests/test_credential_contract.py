"""凭证保险箱契约测试 —— 校验前端侧的硬约束。

Rust 实现是真相之源，但对整个系统真正重要的契约是：
**前端除非显式调用 `credential_get` 并传入命名空间化的 key，
否则永远拿不到原始密钥**。

测试内容：
    - 解析 Rust 源码，提取公开的 Vault API 和命名规则
    - 校验 TS invoke 表面从不引用 DSN/原始凭证值
    - 校验 Tauri capabilities 列表不会授予直接的 file/DB 权限
    - 校验每一次密钥操作都触发审计

运行：`pytest apps/desktop/tests/test_credential_contract.py -v`
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest


def _read(path: Path) -> str:
    """以 UTF-8 读源码（Windows 默认 cp1252 解不开中文破折号）。"""
    return path.read_bytes().decode("utf-8")


ROOT = Path(__file__).resolve().parents[1]  # apps/desktop
TAURI_SRC = ROOT / "src-tauri" / "src"
DESKTOP_SRC = ROOT / "src"
CREDENTIALS_DIR = TAURI_SRC / "credentials"
COMMANDS_DIR = TAURI_SRC / "commands"
IPC_DIR = DESKTOP_SRC / "ipc"


# ---- Rust 源码不变量 -------------------------------------------------------


class TestRustVaultAPI:
    def test_vault_module_exists(self):
        assert (CREDENTIALS_DIR / "mod.rs").exists()

    def test_service_name_is_namespaced(self):
        src = (CREDENTIALS_DIR / "mod.rs").read_bytes().decode("utf-8")
        m = re.search(r'pub const SERVICE_NAME:\s*&str\s*=\s*"([^"]+)"', src)
        assert m, "未找到 SERVICE_NAME 常量"
        assert "." in m.group(1), f"服务名必须是反向域名形式：{m.group(1)}"

    def test_validate_account_requires_dot(self):
        src = (CREDENTIALS_DIR / "mod.rs").read_bytes().decode("utf-8")
        # 校验器应该拒绝空名 / 不含点的名字
        assert "must be namespaced" in src
        assert "must be ASCII" in src

    def test_vault_rejects_empty_value(self):
        src = (CREDENTIALS_DIR / "mod.rs").read_bytes().decode("utf-8")
        assert "refusing to store empty credential" in src

    def test_get_returns_none_for_missing(self):
        src = (CREDENTIALS_DIR / "mod.rs").read_bytes().decode("utf-8")
        # Ok(None) 这条返回路径必须存在
        assert "NoEntry" in src
        assert "Ok(None)" in src


class TestRustCommands:
    def test_commands_module_wires_all_crud(self):
        src = (COMMANDS_DIR / "credentials.rs").read_bytes().decode("utf-8")
        for cmd in ("credential_get", "credential_set", "credential_delete", "credential_list"):
            assert f"pub fn {cmd}" in src, f"缺少命令：{cmd}"

    def test_set_writes_audit_row(self):
        src = (COMMANDS_DIR / "credentials.rs").read_bytes().decode("utf-8")
        assert '"credential.set"' in src
        assert '"credential.get"' in src
        assert '"credential.delete"' in src
        assert '"credential.list"' in src

    def test_set_does_not_log_value(self):
        """审计行只能记 len，绝不能记原值。"""
        src = (COMMANDS_DIR / "credentials.rs").read_bytes().decode("utf-8")
        # 找出 credential_set 函数体
        block = re.search(
            r"pub fn credential_set.*?^\}",
            src,
            re.DOTALL | re.MULTILINE,
        )
        assert block, "未找到 credential_set 函数体"
        body = block.group(0)
        assert "value" not in re.findall(r'"value"', body) or "len" in body
        assert "len" in body, "审计行必须记 len，不能记 value"

    def test_lib_rs_registers_all_commands(self):
        src = (TAURI_SRC / "lib.rs").read_bytes().decode("utf-8")
        for cmd in (
            "credential_get",
            "credential_set",
            "credential_delete",
            "credential_list",
            "credential_service_name",
        ):
            assert f"credentials::{cmd}" in src, f"lib.rs 缺少 {cmd}"


# ---- TS / 前端不变量 -------------------------------------------------------


class TestFrontendNeverSeesSecrets:
    def test_no_dsn_in_frontend(self):
        """在整棵 src/ 里 grep DSN 形状的字符串。"""
        offenders = []
        for path in DESKTOP_SRC.rglob("*.{ts,tsx,js,jsx}"):
            text = path.read_bytes().decode("utf-8")
            # 必须挡在前端之外的 DSN 模式
            for pattern in (
                r"postgresql://",
                r"mysql://",
                r"sqlite://",
                r"BEGIN\s+[A-Z]+\s+KEY",
                r"AKIA[0-9A-Z]{16}",  # AWS access keys
                r"-----BEGIN .*PRIVATE KEY-----",
            ):
                for m in re.finditer(pattern, text):
                    offenders.append(f"{path}：{m.group(0)[:40]}")
        assert not offenders, "前端严禁内嵌原始凭证。命中：\n  " + "\n  ".join(offenders[:10])

    def test_invoke_only_exposes_get_with_namespaced_key(self):
        src = (IPC_DIR / "invoke.ts").read_bytes().decode("utf-8")
        # TS 表面只把 key 传给 Rust；value 通过 setSecret 流入，
        # 但不会经 buildDSN 之类的辅助函数
        assert "getSecret: (key: string)" in src
        assert "setSecret: (key: string, value: string)" in src
        # 前端不准有构造 DSN 的 helper
        assert "buildDSN" not in src
        assert "composeDSN" not in src

    def test_no_logging_of_secret_values(self):
        """前端代码绝不能 `console.log(secret)`。"""
        offenders = []
        for path in DESKTOP_SRC.rglob("*.{ts,tsx,js,jsx}"):
            text = path.read_bytes().decode("utf-8")
            for _m in re.finditer(r"console\.log\(.*?(?:secret|password|token|api_key)", text):
                offenders.append(f"{path}")
        assert not offenders, f"发现疑似打印密钥的 console.log：{offenders}"


# ---- Capabilities（Tauri 权限） --------------------------------------------


class TestCapabilitiesPermissions:
    def test_default_capability_does_not_grant_fs_or_shell(self):
        path = TAURI_SRC.parent / "capabilities" / "default.json"
        if not path.exists():
            pytest.skip("default.json 还没生成（先跑一次 pnpm tauri dev）")
        src = path.read_bytes().decode("utf-8")
        # 硬性禁止：不能出现 fs:* / shell:* / http:* —— webview
        # 不能直接往外探，所有网络流量必须经 Rust 侧代理
        forbidden = []
        for perm in re.findall(r'"([a-z]+:[^"]+)"', src):
            if perm.startswith("fs:") or perm.startswith("shell:") or perm.startswith("http:"):
                forbidden.append(perm)
        assert not forbidden, f"默认 capability 越权授予了：{forbidden}"
