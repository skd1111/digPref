"""test_project_profile.py —— init 风格项目画像（2026-08-05）。

覆盖：
- collect_project_facts：语言统计 / 依赖清单 / README 摘要 / 顶层结构
- facts_to_markdown：确定性兜底渲染
- generate_profile：LLM 失败 → 事实兜底；LLM 可用 → 用模型输出
- FeatureStorage.upsert_profile / get_profile 往返
- GET /biznav/profile 端点（有 / 无画像）
"""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient


@pytest.fixture
def sample_project(tmp_path):
    """造一个迷你工程：Python + package.json + README。"""
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "main.py").write_text("print('hi')\n", encoding="utf-8")
    (tmp_path / "src" / "util.py").write_text("x = 1\n", encoding="utf-8")
    (tmp_path / "app.ts").write_text("export {};\n", encoding="utf-8")
    (tmp_path / "package.json").write_text(
        '{"name": "demo-pkg", "dependencies": {"react": "^18"}}', encoding="utf-8"
    )
    (tmp_path / "README.md").write_text("# Demo\n一个演示工程。\n", encoding="utf-8")
    return tmp_path


# ---- 事实采集 / 兜底渲染 ----------------------------------------------------


def test_collect_facts_languages_and_deps(sample_project):
    from agent.biznav.project_profile import collect_project_facts

    facts = collect_project_facts(str(sample_project))
    langs = dict(facts["languages"])
    assert langs.get("Python", 0) == 2
    assert "react" in facts["dependencies"]
    assert facts["package_name"] == "demo-pkg"
    assert any("README" in str(e) or e.startswith("src") for e in facts["top_level"])


def test_collect_facts_reads_readme_head(sample_project):
    from agent.biznav.project_profile import collect_project_facts

    facts = collect_project_facts(str(sample_project))
    assert "一个演示工程" in facts.get("readme_head", "")


def test_facts_to_markdown_contains_key_sections(sample_project):
    from agent.biznav.project_profile import collect_project_facts, facts_to_markdown

    facts = collect_project_facts(str(sample_project))
    md = facts_to_markdown("demo", facts)
    assert "【项目画像 · demo】" in md
    assert "Python" in md
    assert "不要反问" in md


# ---- generate_profile 两级降级 ---------------------------------------------


async def test_generate_profile_falls_back_when_llm_fails(sample_project):
    from agent.biznav.project_profile import generate_profile

    async def broken_client(kind, messages):
        raise RuntimeError("所有 LLM 后端均不可用")

    profile = await generate_profile("demo", str(sample_project), broken_client)
    assert "【项目画像 · demo】" in profile
    assert "Python" in profile


async def test_generate_profile_uses_llm_output(sample_project):
    from agent.biznav.project_profile import generate_profile

    async def fake_client(kind, messages):
        assert kind == "project_profile"
        return "# demo 画像\nPython + TS 工程。" * 3

    profile = await generate_profile("demo", str(sample_project), fake_client)
    assert profile.startswith("# demo 画像")


async def test_generate_profile_rejects_too_short_llm_output(sample_project):
    from agent.biznav.project_profile import generate_profile

    async def terse_client(kind, messages):
        return "ok"

    profile = await generate_profile("demo", str(sample_project), terse_client)
    assert "【项目画像 · demo】" in profile  # 太短 → 回退事实 Markdown


# ---- storage 往返 -----------------------------------------------------------


def test_storage_profile_roundtrip(tmp_path):
    from agent.biznav.storage import FeatureStorage

    storage = FeatureStorage(str(tmp_path / "biznav.db"))
    assert storage.get_profile("demo") is None
    storage.upsert_profile("demo", "/x/demo", "profile-v1")
    row = storage.get_profile("demo")
    assert row is not None and row["profile_text"] == "profile-v1"
    # 覆盖更新
    storage.upsert_profile("demo", "/x/demo", "profile-v2")
    assert storage.get_profile("demo")["profile_text"] == "profile-v2"


# ---- GET /biznav/profile 端点 -----------------------------------------------


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("EAIDE_BIZNAV_DB", str(tmp_path / "biznav.db"))
    from agent.biznav import api as biznav_api

    biznav_api._reset_storage_for_tests()
    app = FastAPI()
    app.include_router(biznav_api.router)
    return TestClient(app)


def test_profile_endpoint_empty(client):
    resp = client.get("/biznav/profile", params={"project_name": "nope"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["has_profile"] is False and body["profile"] == ""


def test_profile_endpoint_returns_saved(client):
    from agent.biznav import api as biznav_api

    biznav_api._get_storage().upsert_profile("demo", "/x/demo", "画像正文")
    resp = client.get("/biznav/profile", params={"project_name": "demo"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["has_profile"] is True
    assert body["profile"] == "画像正文"
    assert body["project_root"] == "/x/demo"
