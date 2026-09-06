#!/usr/bin/env python3
"""构建期条件处理 tauri.conf.json 中的 config/driver 资源条目。

当 config/driver/ 不存在或为空时，临时移除 tauri.conf.json 中的 driver
资源条目（避免 glob 匹配失败导致构建中断），构建完成后通过 restore 恢复。

用法：
    python infra/scripts/tauri-driver-strip.py strip    # 构建前：条件剥离
    python infra/scripts/tauri-driver-strip.py restore   # 构建后：恢复原始配置
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

_DRIVER_KEY = "../../../config/driver/*"


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _conf_path() -> Path:
    return _repo_root() / "apps" / "desktop" / "src-tauri" / "tauri.conf.json"


def _backup_path() -> Path:
    return _conf_path().with_suffix(".json.bak")


def _load(p: Path) -> dict:
    return json.loads(p.read_text(encoding="utf-8"))


def _save(p: Path, data: dict) -> None:
    p.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def strip() -> None:
    conf = _conf_path()
    backup = _backup_path()
    if backup.exists():
        print("[tauri-driver-strip] 已剥离过（备份存在），跳过")
        return
    driver_dir = _repo_root() / "config" / "driver"
    has_files = driver_dir.is_dir() and any(driver_dir.iterdir())
    if has_files:
        print(f"[tauri-driver-strip] {driver_dir} 有文件，保留 driver 资源条目")
        return
    original = _load(conf)
    _save(backup, original)
    resources = original.get("bundle", {}).get("resources", {})
    removed = resources.pop(_DRIVER_KEY, None)
    if removed is not None:
        _save(conf, original)
        print(f"[tauri-driver-strip] 已临时移除 driver 资源条目（{driver_dir} 不存在/为空）")
    else:
        print("[tauri-driver-strip] driver 资源条目不存在，无需操作")


def restore() -> None:
    conf = _conf_path()
    backup = _backup_path()
    if not backup.exists():
        print("[tauri-driver-strip] 无备份，无需恢复")
        return
    original = _load(backup)
    _save(conf, original)
    backup.unlink()
    print("[tauri-driver-strip] 已恢复 tauri.conf.json")


if __name__ == "__main__":
    if len(sys.argv) < 2 or sys.argv[1] not in ("strip", "restore"):
        print("Usage: tauri-driver-strip.py <strip|restore>")
        sys.exit(1)
    (strip if sys.argv[1] == "strip" else restore)()
