"""Token 用量计量测试 —— tracker 单元行为（速率/总量/次数/费用）+ /llm/token-usage 端点。"""

from __future__ import annotations

import sqlite3
from datetime import date, timedelta

import pytest
from agent.llm.token_usage import (
    TokenUsageTracker,
    estimate_tokens,
    record_ollama_usage,
    record_openai_usage,
    reset_token_usage_tracker_for_testing,
)
from fastapi import FastAPI
from fastapi.testclient import TestClient


@pytest.fixture
def tracker(tmp_path):
    return TokenUsageTracker(db_path=str(tmp_path / "router.db"), window_seconds=30.0)


@pytest.fixture(autouse=True)
def _reset_singleton():
    """防止单例跨用例泄漏（_isolate 只切 cwd，不清 tracker）。"""
    reset_token_usage_tracker_for_testing(None)
    yield
    reset_token_usage_tracker_for_testing(None)


# ---- tracker 单元行为 --------------------------------------------------------


def test_record_updates_snapshot(tracker: TokenUsageTracker) -> None:
    tracker.record(upload_tokens=300, download_tokens=60, backend="ollama")
    snap = tracker.snapshot()
    assert snap["today_upload_tokens"] == 300
    assert snap["today_download_tokens"] == 60
    assert snap["today_total_tokens"] == 360
    assert snap["today_call_count"] == 1
    assert snap["day"] == date.today().isoformat()
    # 30s 窗口内：300/30=10.0，60/30=2.0，1 次/30s
    assert snap["rate_upload_per_s"] == pytest.approx(10.0)
    assert snap["rate_download_per_s"] == pytest.approx(2.0)
    assert snap["rate_calls_per_s"] == pytest.approx(0.03)  # 1 次/30s，快照保留 2 位小数


def test_record_zero_tokens_still_counts_call(tracker: TokenUsageTracker) -> None:
    tracker.record(upload_tokens=0, download_tokens=0)
    tracker.record(upload_tokens=-5, download_tokens=10)
    snap = tracker.snapshot()
    assert snap["today_upload_tokens"] == 0
    assert snap["today_download_tokens"] == 10
    # 两次 record 都是真实模型调用 → 都计次（负数 token 按 0 处理）
    assert snap["today_call_count"] == 2


def test_daily_total_persists_across_instances(tmp_path) -> None:
    db = str(tmp_path / "router.db")
    t1 = TokenUsageTracker(db_path=db)
    t1.record(upload_tokens=100, download_tokens=50)
    # 模拟 Agent 重启：新实例从 DB 载入当日累计（含调用次数）
    t2 = TokenUsageTracker(db_path=db)
    snap = t2.snapshot()
    assert snap["today_upload_tokens"] == 100
    assert snap["today_download_tokens"] == 50
    assert snap["today_call_count"] == 1
    # 继续累加
    t2.record(upload_tokens=10, download_tokens=5)
    snap = t2.snapshot()
    assert snap["today_total_tokens"] == 165
    assert snap["today_call_count"] == 2


def test_day_rollover_resets_counter(tmp_path) -> None:
    db = str(tmp_path / "router.db")
    # 先记一笔今天的（落库），模拟"昨天"已有运行历史
    t1 = TokenUsageTracker(db_path=db)
    t1.record(upload_tokens=100, download_tokens=100)
    # 新实例把"当前日"人为拨到昨天 → 下一次 record 触发滚动，重新载入今天（从 DB 读 100/100 继续累加）
    t2 = TokenUsageTracker(db_path=db)
    t2._day = (date.today() - timedelta(days=1)).isoformat()
    t2._loaded = True
    t2._today_up = 999  # 昨天的残留计数，滚动后应被丢弃
    t2._today_down = 999
    t2.record(upload_tokens=7, download_tokens=3)
    snap = t2.snapshot()
    assert snap["day"] == date.today().isoformat()
    assert snap["today_upload_tokens"] == 107
    assert snap["today_download_tokens"] == 103


def test_estimate_tokens() -> None:
    assert estimate_tokens("") == 0
    assert estimate_tokens("abc") == 1
    assert estimate_tokens("x" * 400) == 100


# ---- 费用（总费用 + 按模型明细）----------------------------------------------


def _seed_backends(db: str) -> None:
    """在测试库建 llm_backends 表并种三个后端（云端收费 / 内网收费 / 本地免费）。"""
    conn = sqlite3.connect(db)
    try:
        conn.execute(
            "CREATE TABLE IF NOT EXISTS llm_backends ("
            "name TEXT PRIMARY KEY, type TEXT NOT NULL, "
            "model_name TEXT NOT NULL, cost_per_1k_tokens REAL)"
        )
        conn.executemany(
            "INSERT INTO llm_backends(name, type, model_name, cost_per_1k_tokens) "
            "VALUES (?, ?, ?, ?)",
            [
                ("cloud-gpt", "cloud", "gpt-4o", 0.005),
                ("priv-ds", "private", "DeepSeek-70B", 0.002),
                ("local-qwen", "local", "qwen2.5:14b", 0.0),
            ],
        )
        conn.commit()
    finally:
        conn.close()


def test_cost_total_and_per_model(tmp_path) -> None:
    db = str(tmp_path / "router.db")
    _seed_backends(db)
    tracker = TokenUsageTracker(db_path=db)
    # 云端：2000 token × 0.005/1K = 0.01
    tracker.record(upload_tokens=1000, download_tokens=1000, backend="cloud", model="gpt-4o")
    # 内网：1000 token × 0.002/1K = 0.002
    tracker.record(upload_tokens=500, download_tokens=500, backend="private", model="DeepSeek-70B")
    # 本地免费：单价 0 → 不计费，但计次
    tracker.record(upload_tokens=100, download_tokens=100, backend="ollama", model="qwen2.5:14b")

    snap = tracker.snapshot()
    assert snap["today_cost_total"] == pytest.approx(0.012)
    assert snap["cost_by_model"] == {
        "gpt-4o": pytest.approx(0.01),
        "DeepSeek-70B": pytest.approx(0.002),
    }
    assert snap["today_call_count"] == 3

    # 总费用跨重启保留（按模型明细为进程内，重启后重新累计）
    t2 = TokenUsageTracker(db_path=db)
    snap2 = t2.snapshot()
    assert snap2["today_cost_total"] == pytest.approx(0.012)
    assert snap2["cost_by_model"] == {}


def test_cost_falls_back_to_type_price(tmp_path) -> None:
    db = str(tmp_path / "router.db")
    _seed_backends(db)
    tracker = TokenUsageTracker(db_path=db)
    # model 未在注册表：按 backend 标签对应 type（cloud）第一个后端价格兜底
    tracker.record(upload_tokens=1000, download_tokens=0, backend="cloud", model="unknown-model")
    snap = tracker.snapshot()
    assert snap["today_cost_total"] == pytest.approx(0.005)
    assert snap["cost_by_model"]["gpt-4o"] == pytest.approx(0.005)


def test_cost_zero_without_backends_table(tracker: TokenUsageTracker) -> None:
    # 无 llm_backends 表（全新库）→ 费用 0，不报错
    tracker.record(upload_tokens=1000, download_tokens=1000, backend="cloud", model="gpt-4o")
    snap = tracker.snapshot()
    assert snap["today_cost_total"] == 0.0
    assert snap["cost_by_model"] == {}
    assert snap["today_call_count"] == 1


# ---- 协议解析（usage 字段 / 兜底估算）----------------------------------------


def test_record_openai_usage_prefers_usage_field(tracker: TokenUsageTracker) -> None:
    reset_token_usage_tracker_for_testing(tracker)
    record_openai_usage(
        {"usage": {"prompt_tokens": 120, "completion_tokens": 40}},
        backend="private",
        fallback_messages=[{"role": "user", "content": "很长的文本"}],
    )
    snap = tracker.snapshot()
    assert snap["today_upload_tokens"] == 120
    assert snap["today_download_tokens"] == 40


def test_record_openai_usage_fallback_estimates(tracker: TokenUsageTracker) -> None:
    reset_token_usage_tracker_for_testing(tracker)
    record_openai_usage(
        {},  # 无 usage 字段 → 按字符数估算
        backend="cloud",
        fallback_messages=[{"role": "user", "content": "x" * 400}],
        fallback_output="y" * 200,
    )
    snap = tracker.snapshot()
    assert snap["today_upload_tokens"] == 100
    assert snap["today_download_tokens"] == 50


def test_record_ollama_usage(tracker: TokenUsageTracker) -> None:
    reset_token_usage_tracker_for_testing(tracker)
    record_ollama_usage({"prompt_eval_count": 88, "eval_count": 22})
    snap = tracker.snapshot()
    assert snap["today_upload_tokens"] == 88
    assert snap["today_download_tokens"] == 22


# ---- /llm/token-usage 端点 ---------------------------------------------------


def test_endpoint_returns_snapshot(tmp_path) -> None:
    from agent.llm import usage_api

    tracker = TokenUsageTracker(db_path=str(tmp_path / "router.db"))
    tracker.record(upload_tokens=900, download_tokens=30)
    reset_token_usage_tracker_for_testing(tracker)

    app = FastAPI()
    app.include_router(usage_api.router)
    client = TestClient(app)

    r = client.get("/llm/token-usage")
    assert r.status_code == 200
    body = r.json()
    assert body["today_upload_tokens"] == 900
    assert body["today_download_tokens"] == 30
    assert body["today_total_tokens"] == 930
    assert body["today_call_count"] == 1
    assert body["today_cost_total"] == 0.0
    assert body["cost_by_model"] == {}
    assert body["rate_upload_per_s"] == pytest.approx(30.0)
    assert body["rate_calls_per_s"] == pytest.approx(0.03)
    assert body["window_seconds"] == 30
    assert body["day"] == date.today().isoformat()
