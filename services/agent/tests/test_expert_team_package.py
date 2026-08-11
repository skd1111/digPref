"""test_expert_team_package.py —— 专家团资产包（zip）+ 交付物外部模板（2026-08-10）。

覆盖：
- 资产包导入：team.yaml（提示词）+ templates/（模板）一次到位；
  report_template 自动挂接；同名 409；缺 team.yaml / 非法模板 400
- 资产包导出：zip 内含 team.yaml + 当前生效模板（往返一致）
- 删团同步清理模板（系统重要资产的完整生命周期）
- ops 导出：外部 md 模板占位符填充（含未知占位符置空）；无模板降级内置结构
"""

from __future__ import annotations

import base64
import io
import zipfile

import pytest
import yaml
from fastapi import FastAPI
from fastapi.testclient import TestClient


def _team_yaml(team_id: str = "pkg_team", **extra) -> str:
    data = {
        "schema_version": "1.0",
        "id": team_id,
        "name": "资产包测试团",
        "members": [{"name": "专家", "role": "测试角色"}],
    }
    data.update(extra)
    return yaml.safe_dump(data, allow_unicode=True)


def _make_package(team_yaml_text: str, templates: dict[str, bytes] | None = None) -> str:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("team.yaml", team_yaml_text)
        for name, raw in (templates or {}).items():
            zf.writestr(f"templates/{name}", raw)
    return base64.b64encode(buf.getvalue()).decode()


@pytest.fixture
def et_client(tmp_path, monkeypatch):
    """专家团路由 TestClient：loader 与模板目录都重定向到临时路径。"""
    from agent.expert_teams import api as et_api
    from agent.expert_teams import templates as et_tpl
    from agent.expert_teams.loader import ExpertTeamLoader

    teams_dir = tmp_path / "expert_teams"
    monkeypatch.setattr(et_tpl, "EXPERT_TEAMS_DIR", teams_dir)
    loader = ExpertTeamLoader(teams_dir)
    loader.load_all()
    monkeypatch.setattr(et_api, "_loader", loader)

    app = FastAPI()
    app.include_router(et_api.router)
    return TestClient(app)


def test_import_package_saves_team_and_template(et_client, tmp_path):
    tpl = "# {{业务名称}} 报告模板".encode()
    r = et_client.post(
        "/expert-teams/import-package",
        json={
            "file_name": "pkg_team.zip",
            "content_base64": _make_package(_team_yaml(), {"报告模板.md": tpl}),
        },
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["team_id"] == "pkg_team"
    assert body["templates"] == ["报告模板.md"]
    # 包内恰好一个模板且 yaml 未指定 → 自动挂接 report_template
    got = et_client.get("/expert-teams/pkg_team").json()
    assert got["report_template"] == "报告模板.md"
    # 模板真实落盘在 templates/ 目录
    saved = tmp_path / "expert_teams" / "templates" / "报告模板.md"
    assert saved.is_file()
    assert saved.read_bytes() == tpl


def test_import_package_conflict_409(et_client):
    pkg = _make_package(_team_yaml())
    assert (
        et_client.post("/expert-teams/import-package", json={"content_base64": pkg}).status_code
        == 200
    )
    assert (
        et_client.post("/expert-teams/import-package", json={"content_base64": pkg}).status_code
        == 409
    )


def test_import_package_missing_team_yaml_400(et_client):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("readme.txt", "no yaml here")
    r = et_client.post(
        "/expert-teams/import-package",
        json={"content_base64": base64.b64encode(buf.getvalue()).decode()},
    )
    assert r.status_code == 400


def test_import_package_bad_template_suffix_400(et_client):
    pkg = _make_package(_team_yaml(), {"evil.exe": b"MZ..."})
    r = et_client.post("/expert-teams/import-package", json={"content_base64": pkg})
    assert r.status_code == 400


def test_export_package_roundtrip(et_client):
    tpl = "模板正文".encode()
    et_client.post(
        "/expert-teams/import-package",
        json={"content_base64": _make_package(_team_yaml(), {"t.md": tpl})},
    )
    r = et_client.get("/expert-teams/pkg_team/package")
    assert r.status_code == 200
    body = r.json()
    assert body["file_name"] == "pkg_team.zip"
    with zipfile.ZipFile(io.BytesIO(base64.b64decode(body["content_base64"]))) as zf:
        names = set(zf.namelist())
        assert names == {"team.yaml", "templates/t.md"}
        assert zf.read("templates/t.md") == tpl
        exported = yaml.safe_load(zf.read("team.yaml").decode("utf-8"))
        assert exported["report_template"] == "t.md"


def test_delete_team_removes_template(et_client, tmp_path):
    et_client.post(
        "/expert-teams/import-package",
        json={"content_base64": _make_package(_team_yaml(), {"t.md": b"x"})},
    )
    saved = tmp_path / "expert_teams" / "templates" / "t.md"
    assert saved.is_file()
    et_client.delete("/expert-teams/pkg_team")
    assert not saved.exists()


# ---------------------------------------------------------------------------
# ops 导出：外部模板渲染 + 降级链
# ---------------------------------------------------------------------------


@pytest.fixture
def ops_client(tmp_path, monkeypatch):
    monkeypatch.setenv("EAIDE_OPS_DB", str(tmp_path / "ops.db"))
    monkeypatch.setenv("EAIDE_AUDIT_DB_PATH", str(tmp_path / "audit.sqlite"))

    from agent.expert_teams import api as et_api
    from agent.expert_teams import templates as et_tpl
    from agent.expert_teams.loader import ExpertTeamLoader
    from agent.ops import api as ops_api

    teams_dir = tmp_path / "expert_teams"
    monkeypatch.setattr(et_tpl, "EXPERT_TEAMS_DIR", teams_dir)
    loader = ExpertTeamLoader(teams_dir)
    monkeypatch.setattr(et_api, "_loader", loader)

    ops_api._reset_storage_for_tests()
    ops_api._reset_case_storage_for_tests()
    app = FastAPI()
    app.include_router(ops_api.router)
    return TestClient(app)


def _seed_case_file(client: TestClient) -> None:
    r = client.post(
        "/ops/case/files",
        json={
            "case_id": "bank__ops_open",
            "team_id": "pkg_team",
            "member_key": "专家",
            "file_name": "营业执照.txt",
            "content_base64": base64.b64encode("统一社会信用代码 91310000XXXX".encode()).decode(),
        },
    )
    assert r.status_code == 200
    client.post(
        f"/ops/case/files/{r.json()['id']}/override", json={"status": "passed", "note": "ok"}
    )


def test_export_uses_external_md_template(ops_client, et_client, tmp_path):
    """外部 md 模板：占位符填充；未知占位符置空（不阻塞、不残留）。"""
    from agent.expert_teams import templates as et_tpl

    tpl_text = (
        "# {{业务名称}} 报告\n专家团：{{专家团}}\n材料：{{材料数量}} 份\n"
        "{{材料验收清单}}\n{{不存在的占位符}}\n结论：{{风险结论}}"
    )
    et_tpl.save_template("pkg_team", "报告模板.md", base64.b64encode(tpl_text.encode()).decode())
    et_client.post(
        "/expert-teams/import-package",
        json={"content_base64": _make_package(_team_yaml(report_template="报告模板.md"))},
    )
    _seed_case_file(ops_client)

    target = tmp_path / "out.zip"
    r = ops_client.post(
        "/ops/case/export",
        json={
            "case_id": "bank__ops_open",
            "target_path": str(target),
            "feature_name": "对公开户",
            "team_id": "pkg_team",
            "team_name": "资产包测试团",
        },
    )
    assert r.status_code == 200, r.text
    with zipfile.ZipFile(target) as zf:
        assert "报告初稿.md" in zf.namelist()
        report = zf.read("报告初稿.md").decode("utf-8")
    assert "# 对公开户 报告" in report
    assert "专家团：资产包测试团" in report
    assert "材料：1 份" in report
    assert "营业执照.txt" in report and "✓ 通过" in report
    assert "{{不存在的占位符}}" not in report
    assert "{{" not in report  # 所有占位符必须被消费


def test_export_falls_back_without_template(ops_client, tmp_path):
    """无模板（未传 team_id）→ 内置报告结构照旧（docx 或 md 降级）。"""
    _seed_case_file(ops_client)
    target = tmp_path / "out.zip"
    r = ops_client.post(
        "/ops/case/export",
        json={"case_id": "bank__ops_open", "target_path": str(target), "feature_name": "对公开户"},
    )
    assert r.status_code == 200
    with zipfile.ZipFile(target) as zf:
        names = zf.namelist()
        assert "尽调报告初稿.docx" in names or "报告初稿.md" in names


def test_export_unknown_team_falls_back(ops_client, tmp_path):
    """team_id 指向不存在的团 → 静默降级内置结构（模板永不阻塞导出）。"""
    _seed_case_file(ops_client)
    target = tmp_path / "out.zip"
    r = ops_client.post(
        "/ops/case/export",
        json={
            "case_id": "bank__ops_open",
            "target_path": str(target),
            "team_id": "ghost_team",
        },
    )
    assert r.status_code == 200
    with zipfile.ZipFile(target) as zf:
        assert "尽调报告初稿.docx" in zf.namelist() or "报告初稿.md" in zf.namelist()
