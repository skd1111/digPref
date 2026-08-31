"""PPT Master 运行时引导 —— 捆绑嵌入式 Python + 离线依赖解压（2026-08-26）。

分发布局（Tauri resources 随安装包落到安装目录，与 exe 同级；开发态 = 仓库根）：

    <root>/vendor/python/            嵌入式 CPython 3.12（python.exe + python312._pth）
    <root>/vendor/ppt-master/        MIT 技能包（SKILL.md / workflows / scripts）
    <root>/vendor/ppt-master/deps/   离线依赖 wheel（cp312 win_amd64，不入 git，
                                     infra/scripts/fetch-ppt-master.ps1 拉取）

Agent 启动最早期调用 ``ensure_ppt_master_runtime()``：

    1. 定位 vendor/python 与 deps wheel 目录（缺失静默跳过）
    2. 把全部 .whl 解压到 ``vendor/python/ppt-master-site/``
       （marker 记账：与 config/driver 的 _site 同策略，幂等）
    3. 在 ``python312._pth`` 登记 ``ppt-master-site`` 相对路径条目
       （嵌入式 Python 按 ._pth 构建 sys.path，无需 pip / 联网）

best-effort：任何失败只记日志不抛出；运行期技能包缺失由种子 prompt
引导模型给出友好提示。设计对齐 driver_bootstrap（BUGFIX #132 三级回退模式）。
"""

from __future__ import annotations

import logging
import sys
import zipfile
from pathlib import Path

logger = logging.getLogger(__name__)

_PTHON_REL = Path("vendor") / "python"
_SKILL_REL = Path("vendor") / "ppt-master"
_SITE_DIRNAME = "ppt-master-site"
_MARKER_NAME = ".extracted"

_ENSURED = False


def _vendor_roots(roots: list[Path] | None = None) -> list[Path]:
    """候选根目录：显式注入 > cwd > PyInstaller exe 目录 > 仓库根推导。"""
    if roots:
        return list(roots)
    out: list[Path] = [Path.cwd()]
    if getattr(sys, "frozen", False):
        out.append(Path(sys.executable).resolve().parent)
    try:
        # ppt_master_bootstrap.py → agent → src → agent → services → 仓库根（parents[4]）
        repo_root = Path(__file__).resolve().parents[4]
        if repo_root not in out:
            out.append(repo_root)
    except IndexError:
        pass
    return out


def resolve_bundled_python(roots: list[Path] | None = None) -> Path | None:
    """三级回退定位捆绑嵌入式 Python 可执行文件；缺失返 None。"""
    name = "python.exe" if sys.platform == "win32" else "python3"
    for root in _vendor_roots(roots):
        candidate = root / _PTHON_REL / name
        if candidate.is_file():
            return candidate
    return None


def resolve_ppt_master_skill_dir(roots: list[Path] | None = None) -> Path | None:
    """三级回退定位 ppt-master 技能包目录（须含 SKILL.md）；缺失返 None。"""
    for root in _vendor_roots(roots):
        candidate = root / _SKILL_REL
        if (candidate / "SKILL.md").is_file():
            return candidate
    return None


def _extract_wheels(python_dir: Path, deps_dir: Path) -> Path | None:
    """把 deps 下全部 wheel 解压进 python_dir/ppt-master-site（幂等）。"""
    wheels = sorted(deps_dir.glob("*.whl"))
    if not wheels:
        return None
    site_dir = python_dir / _SITE_DIRNAME
    marker = site_dir / _MARKER_NAME
    newest = max(w.stem for w in wheels)
    if marker.is_file():
        try:
            if marker.read_text(encoding="utf-8").strip() == newest:
                return site_dir  # 已解压且无更新
        except OSError:
            pass
    site_dir.mkdir(parents=True, exist_ok=True)
    for whl in wheels:
        try:
            with zipfile.ZipFile(whl, "r") as zf:
                zf.extractall(site_dir)
        except Exception as exc:
            logger.warning("ppt-master wheel 解压失败 %s: %s", whl.name, exc)
    marker.write_text(newest, encoding="utf-8")
    return site_dir


def _register_site_in_pth(python_dir: Path) -> None:
    """在 python3*._pth 追加 site 目录条目（嵌入式 Python 的 sys.path 来源）。"""
    pth_files = sorted(python_dir.glob("python*._pth"))
    if not pth_files:
        logger.warning("ppt-master bootstrap: %s 下无 ._pth 文件", python_dir)
        return
    pth = pth_files[0]
    try:
        lines = pth.read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        logger.warning("ppt-master bootstrap: 读 %s 失败: %s", pth, exc)
        return
    if any(line.strip() == _SITE_DIRNAME for line in lines):
        return  # 已登记
    lines.append(_SITE_DIRNAME)
    try:
        pth.write_text("\n".join(lines) + "\n", encoding="utf-8")
    except OSError as exc:
        logger.warning("ppt-master bootstrap: 写 %s 失败: %s", pth, exc)


def ensure_ppt_master_runtime(roots: list[Path] | None = None) -> bool:
    """确保捆绑 Python 的离线依赖就位；返回是否完成（含已就绪）。

    幂等且保守：任何前置缺失或异常都只记日志，不阻断 Agent 启动。
    """
    global _ENSURED
    if _ENSURED:
        return True

    python_exe = resolve_bundled_python(roots)
    skill_dir = resolve_ppt_master_skill_dir(roots)
    if python_exe is None or skill_dir is None:
        logger.debug(
            "ppt-master bootstrap: 捆绑运行时不全（python=%s skill=%s），跳过",
            python_exe,
            skill_dir,
        )
        return False

    deps_dir = skill_dir / "deps"
    try:
        site_dir = _extract_wheels(python_exe.parent, deps_dir)
        if site_dir is None:
            logger.debug("ppt-master bootstrap: deps 无 wheel，跳过")
            return False
        _register_site_in_pth(python_exe.parent)
    except Exception:
        logger.exception("ppt-master bootstrap 失败（不阻断启动）")
        return False

    _ENSURED = True
    logger.info("ppt-master 运行时就绪: %s（site=%s）", python_exe, site_dir)
    return True
