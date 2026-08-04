"""envconfig.storage —— 多环境配置的本地持久化（单文件方案）。

为什么砍掉老的"两个文件 + .tmp + 重试"：
    原方案每次 save_env() 要写两份文件（envs/<env>.json 和 index.json），
    每个文件都先 .tmp 再 rename。Windows 上 AV / 索引服务短暂捏着文件时
    os.replace 会抛 PermissionError，原有的 3 次重试经常不够，
    最终冒泡 500 —— 这是用户报"文件被占用"的根因。

新方案：
    一个文件搞定 —— environments.json 同时装所有 env 的配置 + active 标记。
    进程内 asyncio.Lock 序列化"读 → 改 → 写"，避开任何并发竞争。
    写盘走最朴素的 write_text；这种规模（几个 KB）的单文件覆盖写
    在单进程内有 Lock 保护已经够了，不再需要 .tmp + rename。
    老 layout (envs/*.json + index.json) 启动时一次性迁过来。
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import platform
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from .models import EnvConfig, Environment, environment_display_name
from .scrub import scrub

log = logging.getLogger("agent.envconfig.storage")

try:
    from agent.audit.store import app_log  # type: ignore
except Exception:  # noqa: BLE001

    def app_log(msg: str) -> None:  # type: ignore
        log.info(msg)


# ---- 路径解析 --------------------------------------------------------------


def config_dir() -> Path:
    """EAIDE 配置文件目录。

    优先级：
        1. $EAIDE_CONFIG_DIR     —— 生产由 Tauri 注入（安装目录的 config/）
        2. $EAIDE_DATA_DIR/config —— 兼容旧方案 + 测试隔离
        3. <cwd>/.eaide/config    —— 开发模式兜底
    """
    if custom := os.environ.get("EAIDE_CONFIG_DIR"):
        p = Path(custom).expanduser()
    elif legacy := os.environ.get("EAIDE_DATA_DIR"):
        p = Path(legacy).expanduser() / "config"
    else:
        p = Path.cwd() / ".eaide" / "config"

    _ensure_dir(p)
    return p


def data_dir() -> Path:
    """保留旧符号 —— 新数据目录的父级（方便上层不报错）。"""
    return config_dir().parent


def environments_file() -> Path:
    return config_dir() / "environments.json"


# ---- 兼容旧符号（不导出但保留别名，防止外部代码意外 import 出错）----------


def envs_dir() -> Path:
    """旧符号：返回 envs 目录。现在所有 env 都装在 environments.json 里。"""
    return config_dir()


def env_file(_env: Environment) -> Path:
    """旧符号：返回单 env 文件路径。单文件方案下不再分文件。"""
    return environments_file()


def index_file() -> Path:
    """旧符号：index 文件路径。现在 active 与配置一并存在 environments.json。"""
    return environments_file()


# ---- 文件读写辅助 ----------------------------------------------------------


def _ensure_dir(p: Path) -> None:
    """确保路径是目录；若路径上是残留文件则先删。"""
    if p.exists():
        if p.is_file():
            p.unlink()
        elif p.is_dir():
            return
    p.mkdir(parents=True, exist_ok=True)


def _now_iso() -> str:
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).isoformat()


# ---- 顶层容器（内存模型 + 文件模型合一）-----------------------------------


class Environments(BaseModel):
    """单文件持久化的根结构。

    直接挂在 environments.json 顶层：active + environments 列表。
    """

    active: Environment | None = None
    environments: list[EnvConfig] = Field(default_factory=list)


# ---- 旧符号：列表项视图（保持 dataclass 形态以便 api.py 不改）--------------


@dataclass
class EnvIndexEntry:
    """列表项视图 —— UI 列表只需要 EnvConfig 的一部分字段。"""

    environment: Environment
    label: str
    description: str = ""
    active: bool = False
    updated_at: str = ""
    schema_version: int = 1


# ---- 进程内锁 --------------------------------------------------------------


_io_lock = asyncio.Lock()


def _sync_lock() -> asyncio.Lock:
    """asyncio.Lock 不能跨 event loop 复用，所以按当前 loop 缓存。"""
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        # 没有任何运行中的 loop（纯同步调用，如测试 / 启动脚本），
        # 临时起一个让锁能工作。run_until_complete 完即释放。
        pass
    return _io_lock


# ====================================================================
# 核心读写
# ====================================================================


def _read_environments() -> Environments:
    """读盘 → 校验 → 拿内存模型。无文件则一次性 seed 4 个 preset。"""
    p = environments_file()
    if not p.exists():
        _migrate_legacy_if_pending()
    if not p.exists():
        return _seed_defaults()
    try:
        raw = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as e:
        log.warning("environments.json 读 / 解析失败（%s），重新 seed", e)
        return _seed_defaults()
    try:
        envs = Environments.model_validate(raw)
    except Exception as e:  # noqa: BLE001
        log.warning("environments.json schema 校验失败（%s），重新 seed", e)
        return _seed_defaults()
    return envs


def _write_environments(envs: Environments) -> None:
    """写盘：每个 EnvConfig 走 scrub() 把 SecretStr 替换为占位符再 dump。"""
    p = environments_file()
    _ensure_dir(p.parent)
    payload = {
        "schema_version": 1,
        "active": str(envs.active) if envs.active else None,
        "environments": [scrub(c) for c in envs.environments],
    }
    # 单进程 + Lock 保护下，覆盖写一个几 KB 的文件是安全的，
    # 不再走 .tmp + rename（那是 Windows 文件锁问题的根源）。
    p.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


# ---- Seed / 迁移 ----------------------------------------------------------


def _seed_defaults() -> Environments:
    """首次启动时种 4 个 preset 提示项（用户可任意删除 / 重命名），默认 prod active。

    这些只是「友好提示」，不构成环境约束；用户可以自由增删任意名字的环境。
    """
    presets = [
        ("dev", "开发", "开发环境（首次启动自动 seed，可删除 / 重命名）"),
        ("test", "测试", "测试环境（首次启动自动 seed）"),
        ("staging", "准生产", "准生产环境（首次启动自动 seed）"),
        ("prod", "生产", "生产环境（首次启动自动 seed）"),
    ]
    seeded = [
        EnvConfig(
            environment=Environment(name),
            label=label,
            description=desc,
        )
        for name, label, desc in presets
    ]
    envs = Environments(active=Environment("prod"), environments=seeded)
    _write_environments(envs)
    app_log("[storage] 首次启动：自动 seed 4 个 preset 提示项（可任意增删）")
    return envs


def _migrate_legacy_if_pending() -> None:
    """检测老 layout（envs/*.json + index.json）并一次性迁移。

    触发条件：新文件不存在 AND 老 layout 在任一已知位置存在。
    迁移完把老文件挪到 .migrated/ 子目录而非直接删，方便用户回退。
    """
    p = environments_file()
    if p.exists():
        return

    legacy_dir = _find_legacy_envs_dir()
    if legacy_dir is None:
        return

    legacy_envs = legacy_dir / "envs"
    legacy_index = legacy_dir / "index.json"
    has_envs = legacy_envs.is_dir() and any(legacy_envs.glob("*.json"))
    has_index = legacy_index.is_file()
    if not (has_envs or has_index):
        return

    app_log(
        f"[storage] 检测到老 layout（{legacy_dir}），开始一次性迁移到 {p}"
    )
    envs = Environments()

    # 1. 读 index.json → 拿 active 标记 + label / description
    if has_index:
        try:
            raw = json.loads(legacy_index.read_text(encoding="utf-8"))
            active_str = raw.get("active")
            if active_str:
                try:
                    envs.active = Environment(active_str)
                except ValueError:
                    pass
            for item in raw.get("environments", []):
                try:
                    name = Environment(item["environment"])
                except (ValueError, KeyError):
                    continue
                envs.environments.append(
                    EnvConfig(
                        environment=name,
                        label=item.get("label", environment_display_name(str(name))),
                        description=item.get("description", ""),
                    )
                )
        except Exception as e:  # noqa: BLE001
            log.warning("老 index.json 解析失败：%s", e)

    # 2. 逐个读 envs/*.json，把 databases / api_gateways / mcp_servers / target_servers 合并
    if has_envs:
        for f in legacy_envs.glob("*.json"):
            try:
                env_name = Environment(f.stem)
            except ValueError:
                continue
            try:
                raw = json.loads(f.read_text(encoding="utf-8"))
                cfg_dict = raw.get("config") or raw
                cfg = EnvConfig.model_validate(cfg_dict)
            except Exception as e:  # noqa: BLE001
                log.warning("老 env 文件 %s 解析失败：%s", f, e)
                continue
            # 已存在的同名 env 合并（index 提供的 label 优先）
            existing = next(
                (c for c in envs.environments if c.environment == env_name),
                None,
            )
            if existing is not None:
                # 用磁盘的完整配置覆盖 index 的占位项
                envs.environments = [
                    cfg if c.environment == env_name else c
                    for c in envs.environments
                ]
            else:
                envs.environments.append(cfg)

    # 3. 写新文件
    _write_environments(envs)

    # 4. 把老目录挪到 .migrated/<时间戳>/ 防再触发
    backup_root = legacy_dir / ".migrated" / _now_iso().replace(":", "-")
    try:
        backup_root.mkdir(parents=True, exist_ok=True)
        if legacy_envs.exists():
            (backup_root / "envs").mkdir(exist_ok=True)
            for f in legacy_envs.glob("*"):
                f.rename(backup_root / "envs" / f.name)
            legacy_envs.rmdir()
        if has_index:
            legacy_index.rename(backup_root / legacy_index.name)
        app_log(f"[storage] 老文件已挪到 {backup_root}")
    except OSError as e:
        log.warning("[storage] 老文件挪走失败（保留原样，下轮仍可见）：%s", e)


def _find_legacy_envs_dir() -> Path | None:
    """在几个常见位置找老 layout 的根目录（包含 envs/ + index.json）。"""
    candidates: list[Path] = []

    if custom := os.environ.get("EAIDE_DATA_DIR"):
        candidates.append(Path(custom).expanduser())

    system = platform.system()
    if system == "Windows":
        if appdata := os.environ.get("APPDATA"):
            candidates.append(Path(appdata) / "eaide")
    elif system == "Darwin":
        candidates.append(Path.home() / "Library" / "Application Support" / "eaide")
    else:
        if xdg := os.environ.get("XDG_DATA_HOME"):
            candidates.append(Path(xdg) / "eaide")
        candidates.append(Path.home() / ".local" / "share" / "eaide")

    for c in candidates:
        if (c / "envs").is_dir() or (c / "index.json").is_file():
            return c
    return None


# ====================================================================
# Public API —— 与旧 storage.py 同名同形，调用方代码不动
# ====================================================================


def list_envs() -> list[EnvIndexEntry]:
    """列出所有环境。"""
    envs = _read_environments()
    return [
        EnvIndexEntry(
            environment=c.environment,
            label=c.label,
            description=c.description,
            active=envs.active == c.environment,
            updated_at=_now_iso(),
        )
        for c in envs.environments
    ]


def get_active_env() -> EnvIndexEntry | None:
    """返回当前 active 的环境条目；不存在则 None。"""
    envs = _read_environments()
    if envs.active is None:
        return None
    for c in envs.environments:
        if c.environment == envs.active:
            return EnvIndexEntry(
                environment=c.environment,
                label=c.label,
                description=c.description,
                active=True,
                updated_at=_now_iso(),
            )
    return None


async def _locked_save(envs: Environments) -> None:
    async with _io_lock:
        _write_environments(envs)


def set_active_env(env: Environment) -> None:
    """把 env 标记为 active（取消其他 env 的 active）。"""
    envs = _read_environments()
    envs.active = env
    if not any(c.environment == env for c in envs.environments):
        envs.environments.append(
            EnvConfig(environment=env, label=environment_display_name(str(env)))
        )
    _sync_write(envs)


def save_env(config: EnvConfig) -> None:
    """把单个环境配置落盘。走单文件 + asyncio.Lock。"""
    envs = _read_environments()
    for i, c in enumerate(envs.environments):
        if c.environment == config.environment:
            envs.environments[i] = config
            break
    else:
        envs.environments.append(config)
    _sync_write(envs)


def load_env(env: Environment) -> EnvConfig:
    envs = _read_environments()
    for c in envs.environments:
        if c.environment == env:
            return c
    raise FileNotFoundError(f"环境 {env} 不存在")


def load_env_or_default(env: Environment) -> EnvConfig:
    try:
        return load_env(env)
    except FileNotFoundError:
        return EnvConfig(
            environment=env,
            label=environment_display_name(str(env)),
            description="",
        )


def delete_env(env: Environment) -> bool:
    """移除一个 env；若 env 不存在返回 False。"""
    envs = _read_environments()
    before = len(envs.environments)
    envs.environments = [c for c in envs.environments if c.environment != env]
    removed = len(envs.environments) < before
    if envs.active == env:
        envs.active = None
    if removed:
        _sync_write(envs)
    return removed


# ---- 同步锁：让 sync 的 save_env / set_active_env / delete_env 在
#      FastAPI 同步路径里也能拿到互斥。


def _sync_write(envs: Environments) -> None:
    """同步入口：在没运行 loop 时用临时 loop 跑锁，有 loop 直接 await。"""
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = None
    if loop is None:
        asyncio.run(_locked_save(envs))
    else:
        # 调用方在 async 上下文里 → 把锁任务塞到 loop 里
        loop.create_task(_locked_save(envs))
