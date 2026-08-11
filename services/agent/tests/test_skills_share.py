"""V1 skill zip 分享测试。"""

from __future__ import annotations

import io
import zipfile

import pytest
from agent.skills.loader import SkillLoader
from agent.skills.models import Skill
from agent.skills.share import export_zip, import_zip


@pytest.fixture
def loader(tmp_path):
    d = tmp_path / "skills"
    d.mkdir()
    return SkillLoader(d)


def _make_skill(skill_id: str = "db_query_order", name: str = "订单") -> Skill:
    return Skill(
        id=skill_id,
        name=name,
        description="测试",
        trigger_keywords=["订单", "order"],
        mcp_servers=["database"],
        allowed_tools=["db.query"],
        role="utility",
        system_prompt="你是订单助手",
    )


def test_export_zip_basic():
    """导出 1 个 skill → 字节流包含 yaml 文件。"""
    skill = _make_skill()
    data = export_zip([skill])
    assert isinstance(data, bytes)
    assert len(data) > 0
    zf = zipfile.ZipFile(io.BytesIO(data))
    names = zf.namelist()
    assert "db_query_order.yaml" in names
    content = zf.read("db_query_order.yaml").decode("utf-8")
    assert "id: db_query_order" in content


def test_export_zip_skips_unsafe_id():
    """含路径分隔符的 id 跳过，不进 zip。"""
    skill = _make_skill(skill_id="../etc/passwd")
    data = export_zip([skill])
    zf = zipfile.ZipFile(io.BytesIO(data))
    # 危险路径不写入
    assert all(".." not in n for n in zf.namelist())


def test_export_zip_multiple():
    """导出多个 skill。"""
    s1 = _make_skill("db_query_order")
    s2 = _make_skill("finance_reconcile", name="财务对账")
    data = export_zip([s1, s2])
    zf = zipfile.ZipFile(io.BytesIO(data))
    names = set(zf.namelist())
    assert {"db_query_order.yaml", "finance_reconcile.yaml"}.issubset(names)


def test_import_zip_basic(loader):
    """导出 → 导入 round-trip。"""
    s1 = _make_skill("db_query_order")
    s2 = _make_skill("finance_reconcile", name="财务对账")
    data = export_zip([s1, s2])
    report = import_zip(data, loader)
    assert report.imported == ["db_query_order", "finance_reconcile"]
    assert report.skipped == []
    assert report.errors == []
    assert loader.get("db_query_order") is not None
    assert loader.get("finance_reconcile") is not None


def test_import_zip_skip_existing(loader):
    """已存在的 skill → skipped（overwrite 默认 False）。"""
    # 先写一个
    (loader._dir / "db_query_order.yaml").write_text(
        'schema_version: "1.0"\nid: db_query_order\nname: 订单\n',
        encoding="utf-8",
    )
    loader.load_all()

    incoming = _make_skill("db_query_order")
    data = export_zip([incoming])
    report = import_zip(data, loader)
    assert report.imported == []
    assert report.skipped == ["db_query_order"]


def test_import_zip_overwrite_existing(loader):
    """overwrite=True 时覆盖。"""
    (loader._dir / "db_query_order.yaml").write_text(
        'schema_version: "1.0"\nid: db_query_order\nname: 订单\n',
        encoding="utf-8",
    )
    loader.load_all()

    incoming = _make_skill("db_query_order", name="订单V2")
    data = export_zip([incoming])
    report = import_zip(data, loader, overwrite=True)
    assert report.imported == ["db_query_order"]
    assert loader.get("db_query_order").name == "订单V2"


def test_import_zip_bad_zip(loader):
    """非 zip 数据 → 报告错误。"""
    report = import_zip(b"not a zip", loader)
    assert report.imported == []
    assert len(report.errors) == 1
    assert "bad zip" in report.errors[0]["reason"]


def test_import_zip_empty(loader):
    """空 zip → 报告错误。"""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, mode="w"):
        pass
    report = import_zip(buf.getvalue(), loader)
    assert len(report.errors) == 1
    assert "no .yaml" in report.errors[0]["reason"]


def test_import_zip_zip_slip_blocked(loader):
    """zip slip（路径含 ../）被拦截。"""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, mode="w") as zf:
        zf.writestr("../evil.yaml", 'schema_version: "1.0"\nid: evil\nname: e\n')
    report = import_zip(buf.getvalue(), loader)
    assert report.imported == []
    assert len(report.errors) == 1
    assert "zip slip" in report.errors[0]["reason"]


def test_import_zip_invalid_skill_yaml(loader):
    """schema 不合法的 yaml → 错误。"""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, mode="w") as zf:
        zf.writestr("bad.yaml", "this is: not a skill")
    report = import_zip(buf.getvalue(), loader)
    assert report.imported == []
    assert len(report.errors) == 1
    # "this is: not a skill" 是合法 YAML（dict），schema 校验应该挂掉
    assert "schema" in report.errors[0]["reason"] or "yaml root" in report.errors[0]["reason"]


def test_import_zip_partial_success(loader):
    """部分成功：1 个有效 + 1 个无效。"""
    _make_skill("db_query_order")
    # 直接构造 zip：一个有效 + 一个 schema 不合法
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, mode="w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr(
            "db_query_order.yaml", 'schema_version: "1.0"\nid: db_query_order\nname: 订单\n'
        )
        zf.writestr("bad.yaml", 'schema_version: "1.0"\nid: bad id with space\nname: bad\n')
    report = import_zip(buf.getvalue(), loader)
    assert report.imported == ["db_query_order"]
    assert len(report.errors) == 1
    assert report.errors[0]["filename"] == "bad.yaml"
