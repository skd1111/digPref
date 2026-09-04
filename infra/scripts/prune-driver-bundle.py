#!/usr/bin/env python3
"""构建期精简 config/driver —— 移除已冻结进 exe 的冗余 wheel + 运行时再生成的 _site。

背景（2026-09-03 安装包瘦身）：config/driver 是内网离线 DB 驱动 wheel 目录（未入 git，
构建机本地放置）。历史上用 `pip download` 拉驱动时把**完整依赖闭包**也带进来了，于是
numpy / pandas / cryptography / asyncpg / aiomysql / pymysql / openpyxl / certifi …
这些**早已是 agent 依赖、被 PyInstaller 冻结进 eaide-agent.exe** 的包，在 config/driver
里又存了一份 wheel（+ 运行时解压的 _site 副本），纯属重复负载。

真正只靠 config/driver 提供、exe 里没有的，通常只有少数几个驱动（oracledb /
clickhouse_connect / aioodbc / pyodbc / lz4 / backports.zstd 等，合计 ~2.6MB）。

判定口径（TOC 交叉校验，2026-09-03 加固）：**以 PyInstaller 实际冻结结果为准**，取代旧的
「装在构建 venv 里 ⇒ 会冻结」启发式——后者在构建 venv 恰好装了 db-all extra（oracledb /
pyodbc / clickhouse 进 venv，但其懒加载模块 PyInstaller 未必静态可达而冻结）时，会误删
运行期唯一来源的驱动 wheel，导致对应数据库连不上。改为读取 build/eaide-agent/ 下
PYZ-00.toc（纯 py 模块）+ PKG-00.toc（二进制/数据）解析出 exe **真正冻结的顶层导入名**
集合；对每个 wheel 解压取其顶层导入名，**全部命中冻结集**才判冗余删除（任一缺失即保守
保留）。build-all 在 PyInstaller 之后调用本脚本，TOC 必然就绪。TOC 不可得时（独立运行）
回退旧的 venv 分发名比对，并打印告警。
同时删除 config/driver/_site（driver_bootstrap 运行期从 wheel 幂等重解压，从不入包）。

幂等、保守：缺失目录直接 no-op；删的是可重新放置的构建产物（config/driver 未入 git），
不动任何源码。

用法：
    uv run python infra/scripts/prune-driver-bundle.py            # 实际精简（TOC 交叉校验）
    uv run python infra/scripts/prune-driver-bundle.py --dry-run  # 只报告不删
    uv run python infra/scripts/prune-driver-bundle.py --toc-dir build/eaide-agent
    uv run python infra/scripts/prune-driver-bundle.py --driver-dir path/to/config/driver
"""

from __future__ import annotations

import argparse
import ast
import sys
import zipfile
from importlib.metadata import distributions
from pathlib import Path

# 运行时再生成、绝不入包的子目录
_SITE_DIRNAME = "_site"
# PyInstaller 工作目录默认位置（build-all.bat 用 --workpath build；spec 名 eaide-agent）
_DEFAULT_TOC_RELPATH = ("build", "eaide-agent")


def _repo_root() -> Path:
    """infra/scripts/prune-driver-bundle.py → 仓库根（parents[2]）。"""
    return Path(__file__).resolve().parents[2]


def _norm(name: str) -> str:
    """PEP 503 分发名归一化（大小写 / _ / . 折叠为 -）。"""
    return name.strip().lower().replace("_", "-").replace(".", "-")


def _installed_dists() -> set[str]:
    """（回退用）当前构建 venv 已安装的分发名集合。"""
    out: set[str] = set()
    for dist in distributions():
        name = dist.metadata.get("Name") if dist.metadata else None
        if name:
            out.add(_norm(name))
    return out


def _wheel_dist_name(whl: Path) -> str:
    """wheel 文件名首段 = 分发名（`{name}-{version}-...whl`）。"""
    return _norm(whl.name.split("-", 1)[0])


def _wheel_toplevel(whl: Path) -> set[str]:
    """wheel（zip）提供的顶层导入名：一级目录名 + 一级 .py 模块名，剔除 *.dist-info / *.data。"""
    tops: set[str] = set()
    try:
        with zipfile.ZipFile(whl) as z:
            for n in z.namelist():
                seg = n.split("/", 1)[0]
                if not seg or seg.endswith(".dist-info") or seg.endswith(".data"):
                    continue
                # 顶层文件 six.py / pyodbc.cp312-win_amd64.pyd / *.pyi → 取模块基名；
                # 顶层包目录名不含点，split 无副作用。
                seg = seg.split(".", 1)[0]
                if seg:
                    tops.add(seg)
    except (zipfile.BadZipFile, OSError):
        return set()
    return tops


def _eval_toc_entries(path: Path, index: int) -> list[tuple[str, ...]]:
    """解析 PyInstaller TOC（Python 字面量），取第 index 项的条目列表；异常/结构不符返回空。

    PYZ-00.toc = (pyz_path, [(modname, src, 'PYMODULE'), ...])   → index=1
    PKG-00.toc = (pkg_path, {flags}, [(dest, source, type), ...]) → index=2
    """
    try:
        raw = ast.literal_eval(path.read_text(encoding="utf-8"))
    except (ValueError, SyntaxError, OSError):
        return []
    if not isinstance(raw, tuple) or len(raw) <= index:
        return []
    section = raw[index]
    if not isinstance(section, (list, tuple)):
        return []
    out: list[tuple[str, ...]] = []
    for e in section:
        if isinstance(e, (list, tuple)):
            out.append(tuple(str(x) for x in e))
    return out


def _frozen_toplevel(toc_dir: Path) -> set[str] | None:
    """从 PyInstaller TOC 解析 exe 真正冻结的顶层导入名；无 TOC → None（触发 venv 回退）。"""
    pyz = toc_dir / "PYZ-00.toc"
    pkg = toc_dir / "PKG-00.toc"
    if not pyz.exists() and not pkg.exists():
        return None
    tops: set[str] = set()
    if pyz.exists():
        for e in _eval_toc_entries(pyz, 1):
            if e and e[0]:
                tops.add(e[0].split(".", 1)[0])  # 模块名首段 = 顶层包
    if pkg.exists():
        for e in _eval_toc_entries(pkg, 2):
            # dest：归档内路径/名（如 numpy\core\x.pyd 或 numpy.libs\...）
            if e and e[0]:
                seg = e[0].replace("\\", "/").split("/", 1)[0]
                if seg and not seg.endswith(".dist-info"):
                    tops.add(seg[:-3] if seg.endswith(".py") else seg)
            # source：site-packages 绝对路径 → 取 site-packages/<顶层>
            if len(e) >= 2 and e[1]:
                norm = e[1].replace("\\", "/")
                idx = norm.lower().rfind("site-packages/")
                if idx >= 0:
                    top = norm[idx + len("site-packages/") :].split("/", 1)[0]
                    if top:
                        tops.add(top)
    return tops


def _dir_size(path: Path) -> int:
    return sum(f.stat().st_size for f in path.rglob("*") if f.is_file())


def _rmtree(path: Path) -> None:
    for child in sorted(path.rglob("*"), key=lambda p: len(p.parts), reverse=True):
        if child.is_dir():
            child.rmdir()
        else:
            child.unlink()
    path.rmdir()


def _is_redundant(whl: Path, *, frozen: set[str] | None, installed: set[str]) -> tuple[bool, str]:
    """判定 wheel 是否冗余（内容已在 exe 内）。返回 (冗余?, 原因)。

    TOC 可得时以「wheel 顶层导入名 ⊆ exe 冻结顶层名」为准（保守：任一缺失即保留）；
    否则回退「分发名已装在构建 venv」。
    """
    if frozen is not None:
        tops = _wheel_toplevel(whl)
        if tops and tops <= frozen:
            return True, "TOC 冻结 " + "/".join(sorted(tops))
        return False, ""
    dist = _wheel_dist_name(whl)
    if dist in installed:
        return True, f"venv 已装（回退判定）{dist}"
    return False, ""


def prune(driver_dir: Path, *, toc_dir: Path, dry_run: bool) -> int:
    """精简 driver_dir；返回释放的字节数。缺失目录 → 0（no-op）。"""
    if not driver_dir.is_dir():
        print(f"[prune-driver] {driver_dir} 不存在，跳过")
        return 0

    frozen = _frozen_toplevel(toc_dir)
    installed: set[str] = set()
    if frozen is not None:
        print(
            f"[prune-driver] 判定口径：PyInstaller TOC 交叉校验（{toc_dir}，"
            f"exe 冻结顶层导入名 {len(frozen)} 个）"
        )
    else:
        print(
            f"[prune-driver] 警告：{toc_dir} 下无 PYZ/PKG TOC，回退 venv 分发名比对"
            "（精度较低；请在 PyInstaller 构建后运行，或用 --toc-dir 指定 TOC 目录）"
        )
        installed = _installed_dists()

    before = _dir_size(driver_dir)
    freed = 0
    tag = "(dry) " if dry_run else ""

    # 1. _site：运行时由 driver_bootstrap 从 wheel 重解压，绝不入包
    site = driver_dir / _SITE_DIRNAME
    if site.is_dir():
        size = _dir_size(site)
        freed += size
        print(f"[prune-driver] {tag}删除 {_SITE_DIRNAME}/  {size / 1e6:.1f} MB（运行时重解压）")
        if not dry_run:
            _rmtree(site)

    # 2. 冗余 wheel：内容已冻结进 exe（TOC 交叉校验，或回退 venv 比对）
    kept: list[str] = []
    for whl in sorted(driver_dir.glob("*.whl")):
        redundant, why = _is_redundant(whl, frozen=frozen, installed=installed)
        if redundant:
            size = whl.stat().st_size
            freed += size
            print(f"[prune-driver] {tag}删除冗余 wheel  {whl.name}  {size / 1e6:.2f} MB（{why}）")
            if not dry_run:
                whl.unlink()
        else:
            kept.append(whl.name)

    after = before - freed
    print(
        f"[prune-driver] config/driver: {before / 1e6:.1f} MB → {after / 1e6:.1f} MB "
        f"（释放 {freed / 1e6:.1f} MB{'，dry-run 未实际删除' if dry_run else ''}）"
    )
    print(f"[prune-driver] 保留 {len(kept)} 个必需驱动 wheel：{', '.join(kept) or '（无）'}")
    return freed


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="构建期精简 config/driver（以 PyInstaller TOC 为准移除已冻结冗余 wheel + _site）"
    )
    parser.add_argument(
        "--driver-dir", default=None, help="config/driver 路径（默认 <repo>/config/driver）"
    )
    parser.add_argument(
        "--toc-dir",
        default=None,
        help="PyInstaller TOC 目录（默认 <repo>/build/eaide-agent；无 TOC 则回退 venv 判定）",
    )
    parser.add_argument("--dry-run", action="store_true", help="只报告将删除什么，不实际删除")
    args = parser.parse_args(argv)

    root = _repo_root()
    driver_dir = (
        Path(args.driver_dir).expanduser().resolve()
        if args.driver_dir
        else root / "config" / "driver"
    )
    toc_dir = (
        Path(args.toc_dir).expanduser().resolve()
        if args.toc_dir
        else root.joinpath(*_DEFAULT_TOC_RELPATH)
    )
    prune(driver_dir, toc_dir=toc_dir, dry_run=args.dry_run)
    return 0


if __name__ == "__main__":
    sys.exit(main())
