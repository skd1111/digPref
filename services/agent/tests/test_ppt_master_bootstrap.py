"""ppt_master_bootstrap 测试 —— 捆绑嵌入式 Python + 离线依赖解压（2026-08-26）。

覆盖：三级回退定位（显式注入根）/ wheel 解压幂等 / ._pth 登记幂等 /
前置缺失友好降级。无外部依赖，CI 必过。
"""

from __future__ import annotations

import sys
import zipfile
from pathlib import Path

import agent.ppt_master_bootstrap as bm
import pytest

# 捆绑 Python 可执行文件名随平台变化（win32=python.exe，其他=python3），
# fixture 必须造平台对应名字，否则 Linux CI 上 resolve 永远落空。
_PY_NAME = "python.exe" if sys.platform == "win32" else "python3"

_PTH_DEFAULT = "python312.zip\n.\n\n# Uncomment to run site.main() automatically\n#import site\n"


def _make_wheel(path: Path, module_name: str) -> None:
    """造一个最小合法 wheel（zip 内含单模块 + dist-info 占位）。"""
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr(f"{module_name}.py", f"NAME = {module_name!r}\n")
        zf.writestr(f"{module_name}-1.0.dist-info/METADATA", "Metadata-Version: 2.1\n")


@pytest.fixture()
def vendor_root(tmp_path: Path) -> Path:
    """伪安装目录：vendor/python + vendor/ppt-master/deps。"""
    py_dir = tmp_path / "vendor" / "python"
    py_dir.mkdir(parents=True)
    (py_dir / _PY_NAME).write_bytes(b"MZ fake")
    (py_dir / "python312._pth").write_text(_PTH_DEFAULT, encoding="utf-8")

    skill = tmp_path / "vendor" / "ppt-master"
    (skill / "deps").mkdir(parents=True)
    (skill / "SKILL.md").write_text("# PPT Master Skill\n", encoding="utf-8")
    _make_wheel(skill / "deps" / "fakepkg-1.0-py3-none-any.whl", "fakepkg")
    return tmp_path


@pytest.fixture(autouse=True)
def _reset_ensured():
    bm._ENSURED = False
    yield
    bm._ENSURED = False


class TestResolve:
    def test_resolve_bundled_python(self, vendor_root: Path):
        got = bm.resolve_bundled_python([vendor_root])
        assert got is not None and got.name == _PY_NAME
        assert got.parent == vendor_root / "vendor" / "python"

    def test_resolve_skill_dir_requires_skill_md(self, vendor_root: Path, tmp_path: Path):
        assert bm.resolve_ppt_master_skill_dir([vendor_root]) is not None
        empty = tmp_path / "bare"
        empty.mkdir()
        assert bm.resolve_ppt_master_skill_dir([empty]) is None

    def test_missing_returns_none(self, tmp_path: Path):
        assert bm.resolve_bundled_python([tmp_path]) is None


class TestEnsureRuntime:
    def test_extracts_wheels_and_registers_pth(self, vendor_root: Path):
        assert bm.ensure_ppt_master_runtime([vendor_root]) is True

        site = vendor_root / "vendor" / "python" / "ppt-master-site"
        assert (site / "fakepkg.py").is_file()
        assert (site / ".extracted").is_file()

        pth = (vendor_root / "vendor" / "python" / "python312._pth").read_text(encoding="utf-8")
        assert "ppt-master-site" in pth.splitlines()

    def test_idempotent_second_run(self, vendor_root: Path):
        assert bm.ensure_ppt_master_runtime([vendor_root]) is True
        # 第二次：_ENSURED 短路直接真
        assert bm.ensure_ppt_master_runtime([vendor_root]) is True

        pth = (vendor_root / "vendor" / "python" / "python312._pth").read_text(encoding="utf-8")
        assert pth.count("ppt-master-site") == 1  # 不重复登记

    def test_marker_prevents_reextract(self, vendor_root: Path):
        assert bm.ensure_ppt_master_runtime([vendor_root]) is True
        bm._ENSURED = False  # 绕过全局短路，验证 marker 分支
        sentinel = vendor_root / "vendor" / "python" / "ppt-master-site" / "sentinel.txt"
        sentinel.write_text("keep", encoding="utf-8")
        assert bm.ensure_ppt_master_runtime([vendor_root]) is True
        assert sentinel.is_file()  # marker 命中 → 未重新解压覆盖

    def test_new_wheel_triggers_reextract(self, vendor_root: Path):
        assert bm.ensure_ppt_master_runtime([vendor_root]) is True
        bm._ENSURED = False
        _make_wheel(
            vendor_root / "vendor" / "ppt-master" / "deps" / "zzz-2.0-py3-none-any.whl", "zzzmod"
        )
        assert bm.ensure_ppt_master_runtime([vendor_root]) is True
        site = vendor_root / "vendor" / "python" / "ppt-master-site"
        assert (site / "zzzmod.py").is_file()

    def test_missing_skill_friendly_skip(self, tmp_path: Path):
        assert bm.ensure_ppt_master_runtime([tmp_path]) is False  # 不抛异常

    def test_no_wheels_friendly_skip(self, vendor_root: Path):
        for whl in (vendor_root / "vendor" / "ppt-master" / "deps").glob("*.whl"):
            whl.unlink()
        assert bm.ensure_ppt_master_runtime([vendor_root]) is False
