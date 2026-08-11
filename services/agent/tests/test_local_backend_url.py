"""test_local_backend_url.py —— 端侧模型自定义 URL/端口接入模型管理（router.db）。

语义（用户要求）：端侧 Ollama 端口自定义时，在「设置 → 模型管理」里配的
local 后端 Base URL / Model 必须是真实调用链的权威来源（含 LMRouter.ollama
与 reload 热生效）；未配置时回退 settings.ollama_* 默认。
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

SCHEMA_PATH = Path(__file__).parent.parent / "src/agent/llm/schema.sql"

CUSTOM_URL = "http://127.0.0.1:21434"
CUSTOM_MODEL = "qwen2.5:7b"


def _seed_local_backend(db_path: Path, *, base_url: str, model: str, enabled: int = 1) -> None:
    conn = sqlite3.connect(db_path, timeout=5)
    conn.executescript(SCHEMA_PATH.read_text(encoding="utf-8"))
    conn.execute(
        "INSERT INTO llm_backends (name, type, base_url, model_name, max_context, enabled, data_residency, role) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        ("ollama-custom", "local", base_url, model, 32768, enabled, "local", "execution"),
    )
    conn.commit()
    conn.close()


def test_load_enabled_local_backend_uses_router_db(tmp_path, monkeypatch):
    """模型管理配了自定义端口的 local 后端 → 以 router.db 为准。"""
    from agent.config import settings
    from agent.llm.router import load_enabled_local_backend

    db_path = tmp_path / "router.db"
    _seed_local_backend(db_path, base_url=CUSTOM_URL, model=CUSTOM_MODEL)
    monkeypatch.setattr(settings, "llm_router_db_path", str(db_path))

    url, model = load_enabled_local_backend()
    assert url == CUSTOM_URL
    assert model == CUSTOM_MODEL


def test_load_enabled_local_backend_ignores_disabled(tmp_path, monkeypatch):
    """local 后端被停用 → 回退 settings 默认。"""
    from agent.config import settings
    from agent.llm.router import load_enabled_local_backend

    db_path = tmp_path / "router.db"
    _seed_local_backend(db_path, base_url=CUSTOM_URL, model=CUSTOM_MODEL, enabled=0)
    monkeypatch.setattr(settings, "llm_router_db_path", str(db_path))

    url, model = load_enabled_local_backend()
    assert url == settings.ollama_base_url.rstrip("/") or url == settings.ollama_base_url
    assert model == settings.ollama_model


def test_load_enabled_local_backend_missing_db_falls_back(tmp_path, monkeypatch):
    """router.db 不存在 → 回退 settings，不抛异常。"""
    from agent.config import settings
    from agent.llm.router import load_enabled_local_backend

    monkeypatch.setattr(settings, "llm_router_db_path", str(tmp_path / "nonexistent.db"))
    url, model = load_enabled_local_backend()
    assert url == settings.ollama_base_url
    assert model == settings.ollama_model


def test_lm_router_ollama_client_uses_custom_port(tmp_path, monkeypatch):
    """LMRouter 构造的 ollama client 必须用模型管理里的自定义 URL/端口。"""
    from agent.config import settings
    from agent.llm.router import LMRouter

    db_path = tmp_path / "router.db"
    _seed_local_backend(db_path, base_url=CUSTOM_URL + "/", model=CUSTOM_MODEL)  # 尾斜杠应被清掉
    monkeypatch.setattr(settings, "llm_router_db_path", str(db_path))

    router = LMRouter()
    assert router.ollama.base_url == CUSTOM_URL
    assert router.ollama.model == CUSTOM_MODEL
    assert router.ollama.max_context == 32768  # max_context 与自定义模型同源匹配


def test_reload_max_context_hot_swaps_url(tmp_path, monkeypatch):
    """保存模型后 reload：运行中的 ollama client 热切到新 URL/端口，无需重启。"""
    from agent.config import settings
    from agent.llm.router import LMRouter

    db_path = tmp_path / "router.db"
    monkeypatch.setattr(settings, "llm_router_db_path", str(db_path))

    router = LMRouter()
    assert router.ollama.base_url == settings.ollama_base_url.rstrip("/")

    # 用户在模型管理里新增自定义端口的 local 后端并保存 → 前端调 reload
    _seed_local_backend(db_path, base_url=CUSTOM_URL, model=CUSTOM_MODEL)
    router.reload_max_context()
    assert router.ollama.base_url == CUSTOM_URL
    assert router.ollama.model == CUSTOM_MODEL


def test_max_context_matches_custom_model(tmp_path, monkeypatch):
    """local 后端的 max_context 匹配以模型管理的 model 为准（不再绑 settings.ollama_model）。"""
    from agent.config import settings
    from agent.llm.router import _load_max_context_from_db

    db_path = tmp_path / "router.db"
    _seed_local_backend(db_path, base_url=CUSTOM_URL, model=CUSTOM_MODEL)
    monkeypatch.setattr(settings, "llm_router_db_path", str(db_path))
    # settings.ollama_model 与 router.db 里的模型不同名也不应失配
    monkeypatch.setattr(settings, "ollama_model", "qwen2.5:14b")

    ollama_ctx, private_ctx = _load_max_context_from_db()
    assert ollama_ctx == 32768
    assert private_ctx is None


def test_skill_router_gets_custom_url(tmp_path, monkeypatch):
    """SkillRouter 的 LLM 分类必须打到模型管理配置的自定义端口。"""
    from agent.config import settings
    from agent.skills.router import SkillRouter

    db_path = tmp_path / "router.db"
    _seed_local_backend(db_path, base_url=CUSTOM_URL, model=CUSTOM_MODEL)
    monkeypatch.setattr(settings, "llm_router_db_path", str(db_path))

    from agent.llm.router import load_enabled_local_backend

    router = SkillRouter(loader=None, ollama_base_url=load_enabled_local_backend()[0])  # type: ignore[arg-type]
    assert router._ollama_base_url == CUSTOM_URL
