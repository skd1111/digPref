"""Phase 15 V0 · 前端框架自动检测。

规则（设计文档 §3.2）：
  1. 读取项目根目录 package.json 的 dependencies + devDependencies
  2. 关键字匹配：vue / react / svelte（多框架并存时按依赖出现顺序优先）
  3. 无 package.json → Framework.HTML（纯静态目录，Vite 也能跑）
  4. Vue 额外识别 2.x / 3.x（vite-plugin-vue2 vs @vitejs/plugin-vue）
  5. 包管理器检测：bun.lockb / pnpm-lock.yaml / yarn.lock / package-lock.json
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from agent.preview.models import Framework

PREVIEWABLE_SUFFIXES = frozenset({".vue", ".tsx", ".jsx", ".html", ".svelte", ".htm"})


def find_project_root(start_path: str | Path, max_levels: int = 8) -> Path | None:
    """从 start_path 向上查找包含 package.json 的最近目录（最多 8 层）。

    找不到 package.json 时返回 None —— 调用方按纯 HTML 目录处理。
    """
    p = Path(start_path).expanduser()
    if p.is_file():
        p = p.parent
    for _ in range(max_levels):
        if (p / "package.json").is_file():
            return p
        if p.parent == p:
            break
        p = p.parent
    return None


def read_package_json(project_path: str | Path) -> dict[str, Any]:
    """读取 package.json；缺失 / 损坏时返回空 dict（不抛异常）。"""
    pkg_path = Path(project_path) / "package.json"
    if not pkg_path.is_file():
        return {}
    try:
        data = json.loads(pkg_path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (json.JSONDecodeError, OSError):
        return {}


def detect_framework(project_path: str | Path) -> Framework:
    """自动检测项目框架。

    检测顺序（按优先级）：
      - svelte → SVELTE
      - vue → VUE
      - react / preact → REACT
      - 都没有 → HTML
    多框架并存时，按 dependencies 在前、devDependencies 在后的源码顺序
    第一次命中为准（常见于 react + vue 混合项目，用户可手动覆盖）。
    """
    pkg = read_package_json(project_path)
    deps: dict[str, Any] = pkg.get("dependencies") or {}
    dev_deps: dict[str, Any] = pkg.get("devDependencies") or {}

    merged: list[tuple[str, str]] = []
    for name in deps:
        merged.append((name, deps[name]))
    for name in dev_deps:
        merged.append((name, dev_deps[name]))

    for name, _ver in merged:
        normalized = name.lower()
        if normalized in {"svelte", "@sveltejs/kit"}:
            return Framework.SVELTE
        if normalized == "vue" or normalized.startswith("vue-") or "@vue/" in normalized:
            return Framework.VUE
        if normalized in {"react", "preact", "react-dom", "@preact/preset-vite"}:
            return Framework.REACT

    # 无 package.json / 无框架依赖 → 纯静态 HTML
    return Framework.HTML


def vue_major_version(project_path: str | Path) -> int:
    """返回 Vue 主版本（2 / 3；未知时默认 3）。"""
    pkg = read_package_json(project_path)
    raw = (
        (pkg.get("dependencies") or {}).get("vue")
        or (pkg.get("devDependencies") or {}).get("vue")
        or "3"
    )
    digits = "".join(ch for ch in str(raw) if ch.isdigit())
    if digits.startswith("2"):
        return 2
    return 3


def get_package_manager(project_path: str | Path) -> str:
    """按 lockfile 检测包管理器：npm / pnpm / yarn / bun（默认 npm）。"""
    root = Path(project_path)
    if (root / "bun.lockb").exists() or (root / "bun.lock").exists():
        return "bun"
    if (root / "pnpm-lock.yaml").exists():
        return "pnpm"
    if (root / "yarn.lock").exists():
        return "yarn"
    return "npm"


def is_previewable_file(path: str | Path) -> bool:
    """判断文件后缀是否可触发预览按钮高亮。"""
    return Path(path).suffix.lower() in PREVIEWABLE_SUFFIXES
