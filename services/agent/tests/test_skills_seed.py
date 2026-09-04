"""Skill 种子机制测试（seed.py，2026-08-25）。

覆盖：内置种子 YAML 合法性（无外部依赖，CI 必过）+ 播种机制
（缺失播种 / 存在不覆盖 / 删除不复活 / 坏种子跳过）+ _MEIPASS 回退。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest
import yaml

# 测试用合法种子样本（最小字段集，过 SKILL_JSON_SCHEMA）
_VALID_SEED = {
    "schema_version": "1.0",
    "id": "office_seed_test",
    "name": "种子测试",
    "description": "仅供单测使用的种子样本",
    "enabled": True,
    "trigger_keywords": ["测试种子"],
    "system_prompt": "这是测试种子。",
}


def _write_seed(dir_path: Path, data: dict) -> Path:
    dir_path.mkdir(parents=True, exist_ok=True)
    p = dir_path / f"{data.get('id', 'x')}.yaml"
    p.write_text(yaml.safe_dump(data, allow_unicode=True), encoding="utf-8")
    return p


class TestBundledSeedsValidity:
    """仓库内置种子必须全部合法（防止坏种子随包分发）。"""

    def _seeds_dir(self) -> Path:
        import agent.skills.seed as seed_mod

        d = seed_mod.resolve_seeds_dir()
        assert d is not None and d.is_dir(), "仓库内应存在内置种子目录"
        return d

    def test_all_seeds_pass_schema_and_dsn(self):
        from agent.skills.schema import validate_no_dsn, validate_skill_yaml

        for path in self._seeds_dir().glob("*.yaml"):
            data = yaml.safe_load(path.read_text(encoding="utf-8"))
            assert isinstance(data, dict), f"{path} 根必须是 dict"
            errors = validate_skill_yaml(data)
            assert errors == [], f"{path.name} schema 违规: {errors}"
            assert validate_no_dsn(data) == [], f"{path.name} 含 DSN 形态字符串"

    def test_seeds_contain_no_external_urls(self):
        """种子内容不得引用外部网络地址（企业内网红线）。"""
        for path in self._seeds_dir().glob("*.yaml"):
            raw = path.read_text(encoding="utf-8")
            assert "http://" not in raw and "https://" not in raw, f"{path.name} 含外部 URL"

    def test_expected_office_seeds_present(self):
        ids = {
            yaml.safe_load(p.read_text(encoding="utf-8")).get("id")
            for p in self._seeds_dir().glob("*.yaml")
        }
        expected = {
            "office_doc_writer",
            "office_excel_analyst",
            "visual_deck_designer",
            "ui_frontend_designer",
        }
        assert expected <= ids


class TestSeedingMechanism:
    """播种行为（种子源目录用 monkeypatch 指向临时目录）。"""

    @pytest.fixture()
    def seeds_src(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
        import agent.skills.seed as seed_mod

        src = tmp_path / "seeds_src"
        _write_seed(src, _VALID_SEED)
        monkeypatch.setattr(seed_mod, "resolve_seeds_dir", lambda: src)
        return src

    def test_seeds_missing_target(self, tmp_path: Path, seeds_src: Path):
        from agent.skills.seed import seed_builtin_skills

        target_dir = tmp_path / "skills"
        seeded = seed_builtin_skills(target_dir)
        assert seeded == ["office_seed_test"]
        assert (target_dir / "office_seed_test.yaml").is_file()

    def test_existing_user_file_not_overwritten(self, tmp_path: Path, seeds_src: Path):
        from agent.skills.seed import seed_builtin_skills

        target_dir = tmp_path / "skills"
        target_dir.mkdir()
        user_content = "# 用户自定义修改过的内容"
        (target_dir / "office_seed_test.yaml").write_text(user_content, encoding="utf-8")
        seeded = seed_builtin_skills(target_dir)
        assert seeded == []
        assert (target_dir / "office_seed_test.yaml").read_text(encoding="utf-8") == user_content

    def test_deleted_seed_not_resurrected(self, tmp_path: Path, seeds_src: Path):
        from agent.skills.seed import seed_builtin_skills

        target_dir = tmp_path / "skills"
        assert seed_builtin_skills(target_dir) == ["office_seed_test"]
        (target_dir / "office_seed_test.yaml").unlink()
        # 用户删除即永久移除：再次播种不复活（manifest 记账）
        assert seed_builtin_skills(target_dir) == []
        assert not (target_dir / "office_seed_test.yaml").exists()
        manifest = json.loads((target_dir / ".seeded-manifest.json").read_text(encoding="utf-8"))
        assert "office_seed_test" in manifest["seeded"]

    def test_invalid_seed_skipped_without_raising(self, tmp_path: Path, seeds_src: Path):
        from agent.skills.seed import seed_builtin_skills

        bad = dict(_VALID_SEED, id="x")  # id 不满足 ^[a-z][a-z0-9_]{2,63}$
        _write_seed(seeds_src, bad)
        target_dir = tmp_path / "skills"
        seeded = seed_builtin_skills(target_dir)
        assert seeded == ["office_seed_test"]  # 只播合法种子
        assert not (target_dir / "x.yaml").exists()

    def test_missing_seeds_dir_returns_empty(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
        import agent.skills.seed as seed_mod
        from agent.skills.seed import seed_builtin_skills

        monkeypatch.setattr(seed_mod, "resolve_seeds_dir", lambda: None)
        assert seed_builtin_skills(tmp_path / "skills") == []


class TestSeedsDirResolution:
    """三级回退定位（与 config/biz_dict 同策略）。"""

    def test_meipass_fallback(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
        import agent.skills.seed as seed_mod

        meipass = tmp_path / "meipass"
        bundled = meipass / "agent" / "skills" / "seeds"
        bundled.mkdir(parents=True)
        monkeypatch.setattr(sys, "_MEIPASS", str(meipass), raising=False)
        monkeypatch.chdir(tmp_path)  # 避开真实仓库根的干扰
        resolved = seed_mod.resolve_seeds_dir()
        assert resolved is not None
        assert str(resolved).startswith(str(meipass))

    def test_repo_root_fallback_in_dev(self):
        """开发态：从模块位置推导仓库根（本测试在真实仓库内跑）。"""
        import agent.skills.seed as seed_mod

        resolved = seed_mod.resolve_seeds_dir()
        assert resolved is not None
        assert resolved.name == "seeds"
