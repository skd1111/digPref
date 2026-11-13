"""内置专家团种子（expert_teams/seed.py）测试。

覆盖：
- 全部种子 YAML 通过 schema + DSN 校验，且每个团都有成员与交付物表单
- 首次播种写入 YAML 与交付物模板，二次播种幂等
- 用户同名文件不被覆盖；播种后删除不复活（manifest 记账）
"""

from __future__ import annotations

import yaml
from agent.expert_teams.schema import validate_expert_team_yaml, validate_no_dsn
from agent.expert_teams.seed import resolve_seeds_dir, seed_builtin_expert_teams

# 运营工作台必须覆盖的业务域（缺一个就说明种子没补齐）
EXPECTED_TEAM_IDS = {
    "biz_process_team",
    "doc_precheck_team",
    "smart_form_team",
    "auth_review_team",
    "risk_warning_team",
    "aml_team",
    "corp_account_team",
    "post_loan_team",
    "customer_marketing_team",
    "customer_service_team",
    "error_rectify_team",
    "knowledge_training_team",
}


def _seed_files():
    seeds_dir = resolve_seeds_dir()
    assert seeds_dir is not None, "种子目录未定位到"
    return sorted(seeds_dir.glob("*.yaml"))


def test_all_seed_yaml_valid():
    files = _seed_files()
    assert len(files) >= len(EXPECTED_TEAM_IDS)
    ids = set()
    for path in files:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
        assert validate_expert_team_yaml(data) == [], path.name
        assert validate_no_dsn(data) == [], path.name
        assert data["members"], f"{path.name} 没有专家成员"
        assert ids.isdisjoint({data["id"]}), f"{path.name} id 重复"
        ids.add(data["id"])
    assert EXPECTED_TEAM_IDS.issubset(ids)


def test_seed_writes_yaml_and_templates(tmp_path):
    teams_dir = tmp_path / "expert_teams"
    seeded = seed_builtin_expert_teams(teams_dir)

    assert EXPECTED_TEAM_IDS.issubset(set(seeded))
    assert (teams_dir / "aml_team.yaml").is_file()
    # 交付物模板随种子落盘（导出 zip 时套用）
    templates = list((teams_dir / "templates").glob("*.md"))
    assert len(templates) >= len(EXPECTED_TEAM_IDS)
    # manifest 记账
    manifest = teams_dir / ".seeded-manifest.json"
    assert manifest.is_file()
    assert "aml_team" in manifest.read_text(encoding="utf-8")

    # 幂等：再次播种不再写入
    assert seed_builtin_expert_teams(teams_dir) == []


def test_seed_does_not_overwrite_user_team(tmp_path):
    teams_dir = tmp_path / "expert_teams"
    teams_dir.mkdir(parents=True)
    user_file = teams_dir / "aml_team.yaml"
    user_file.write_text("user: owned", encoding="utf-8")

    seeded = seed_builtin_expert_teams(teams_dir)

    assert "aml_team" not in seeded
    assert user_file.read_text(encoding="utf-8") == "user: owned"
    # 其余种子照常播种
    assert "biz_process_team" in seeded


def test_deleted_seed_not_resurrected(tmp_path):
    teams_dir = tmp_path / "expert_teams"
    seed_builtin_expert_teams(teams_dir)
    target = teams_dir / "biz_process_team.yaml"
    assert target.is_file()

    target.unlink()
    assert seed_builtin_expert_teams(teams_dir) == []
    assert not target.exists()


def test_seed_templates_do_not_overwrite_user_template(tmp_path):
    teams_dir = tmp_path / "expert_teams"
    tpl_dir = teams_dir / "templates"
    tpl_dir.mkdir(parents=True)
    user_tpl = tpl_dir / "aml_team.md"
    user_tpl.write_text("# 自定义模板", encoding="utf-8")

    seed_builtin_expert_teams(teams_dir)

    assert user_tpl.read_text(encoding="utf-8") == "# 自定义模板"
    assert (tpl_dir / "biz_process_team.md").is_file()


# ---------------------------------------------------------------------------
# 凭证节点（2026-11-09 凭证预览）：预设与存量补齐
# ---------------------------------------------------------------------------

# 影像密集型业务团：凭证预览开箱就要有东西可展
CREDENTIAL_TEAM_IDS = {
    "biz_process_team",
    "doc_precheck_team",
    "smart_form_team",
    "corp_account_team",
    "aml_team",
    "auth_review_team",
}


def test_seeds_preset_credential_nodes():
    """重点业务团都预设了凭证节点，且通过 schema 校验。"""
    from agent.expert_teams.models import ExpertTeam

    covered: set[str] = set()
    for path in _seed_files():
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
        team = ExpertTeam.from_dict(data)
        nodes = [c for m in team.members for c in m.credentials]
        if not nodes:
            continue
        covered.add(team.id)
        for node in nodes:
            assert node["name"], path.name
            assert node["media"] in {"image", "doc", "any"}
            assert isinstance(node["required"], bool)
            for f in node["fields"]:
                assert f["name"] and f["label"]
    assert CREDENTIAL_TEAM_IDS.issubset(covered), covered


def test_credentials_schema_rejects_bad_media():
    data = yaml.safe_load(
        (resolve_seeds_dir() / "biz_process_team.yaml").read_text(encoding="utf-8")
    )
    data["members"][0]["credentials"] = [{"name": "营业执照", "media": "video"}]
    errors = validate_expert_team_yaml(data)
    assert any("media" in e for e in errors), errors


def test_backfill_credentials_adds_preset_without_touching_user_edits(tmp_path):
    """存量团（用户没动过凭证）→ 只补 credentials，提示词与交付物不变。"""
    from agent.expert_teams.seed import backfill_credentials

    teams_dir = tmp_path / "expert_teams"
    seed_builtin_expert_teams(teams_dir)
    target = teams_dir / "biz_process_team.yaml"
    before = yaml.safe_load(target.read_text(encoding="utf-8"))
    # 模拟旧版本种子：用户文件里没有凭证清单
    for member in before["members"]:
        member.pop("credentials", None)
    target.write_text(yaml.safe_dump(before, allow_unicode=True, sort_keys=False), encoding="utf-8")

    patched = backfill_credentials(teams_dir, resolve_seeds_dir())

    assert "biz_process_team" in patched
    after = yaml.safe_load(target.read_text(encoding="utf-8"))
    seeds = yaml.safe_load(
        (resolve_seeds_dir() / "biz_process_team.yaml").read_text(encoding="utf-8")
    )
    by_name = {m["name"]: m for m in seeds["members"]}
    for member in after["members"]:
        assert member.get("credentials", []) == by_name[member["name"]].get("credentials", [])
        assert member["prompt"] == by_name[member["name"]]["prompt"]
        assert member.get("output_forms") == by_name[member["name"]].get("output_forms")


def test_backfill_credentials_keeps_user_list(tmp_path):
    from agent.expert_teams.seed import backfill_credentials

    teams_dir = tmp_path / "expert_teams"
    seed_builtin_expert_teams(teams_dir)
    target = teams_dir / "biz_process_team.yaml"
    data = yaml.safe_load(target.read_text(encoding="utf-8"))
    mine = [{"name": "我行自定义凭证", "required": True}]
    data["members"][0]["credentials"] = mine
    target.write_text(yaml.safe_dump(data, allow_unicode=True, sort_keys=False), encoding="utf-8")

    backfill_credentials(teams_dir, resolve_seeds_dir())

    after = yaml.safe_load(target.read_text(encoding="utf-8"))
    assert after["members"][0]["credentials"] == mine


def test_backfill_is_idempotent(tmp_path):
    """已补齐后再跑一次不重写文件（字节不变）。"""
    from agent.expert_teams.seed import backfill_credentials

    teams_dir = tmp_path / "expert_teams"
    seed_builtin_expert_teams(teams_dir)
    target = teams_dir / "corp_account_team.yaml"
    first = target.read_bytes()

    assert backfill_credentials(teams_dir, resolve_seeds_dir()) == []
    assert target.read_bytes() == first
