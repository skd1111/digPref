"""Token 用量计量 —— 状态栏实时速率（区分上传/下载）+ 当日总量 + 调用次数 + 费用。

职责：
    - `record_usage()` 由各 LLM 客户端在每次调用成功后记账：
        upload_tokens   = prompt_tokens（上传给模型）
        download_tokens = completion_tokens（模型生成返回）
        每次记账同时计一次模型调用（call_count）
    - 费用：按模型管理（router.db.llm_backends.cost_per_1k_tokens，单价/1K token）
      计算；当日总费用落库（跨重启保留），按模型明细进程内维护；
      本地/免费模型单价为 0 → 不计费。
    - 进程内维护滑动窗口样本（默认 30s），供「实时速率」读取；
    - 当日总量落 router.db 的 `token_usage_daily` 表（跨重启保留，按日滚动）。

红线：计量是 best-effort —— 任何异常都静默吞掉，绝不阻塞 LLM 主链路。
后端未返回 usage 字段时按 ~4 字符/token 估算（与 private_llm._CHARS_PER_TOKEN 一致）。
"""

from __future__ import annotations

import logging
import sqlite3
import threading
import time
from collections import deque
from datetime import date
from typing import Any

logger = logging.getLogger(__name__)

# 独立建表语句（schema.sql 是单一真源，此处保留内联副本仅供计量器首次
# 访问时兜底 —— 与 llm/metrics.py 对 routing_decisions 的处理同模式）。
_DAILY_TABLE_SCHEMA = """
CREATE TABLE IF NOT EXISTS token_usage_daily (
    day TEXT PRIMARY KEY,
    upload_tokens INTEGER NOT NULL DEFAULT 0,
    download_tokens INTEGER NOT NULL DEFAULT 0,
    call_count INTEGER NOT NULL DEFAULT 0,
    cost_total REAL NOT NULL DEFAULT 0.0,
    updated_at INTEGER
);
"""

# 速率统计滑动窗口（秒）—— 前端每 2s 轮询一次，30s 窗口足够平滑又不迟钝
_RATE_WINDOW_SECONDS = 30.0

# 计量标签 → llm_backends.type 映射（ollama / local_small 都是本地 local 类型）
_LABEL_TO_BACKEND_TYPE = {
    "ollama": "local",
    "local_small": "local",
    "private": "private",
    "cloud": "cloud",
}

# 单价缓存有效期（秒）：模型管理改价后最迟 60s 生效，避免每次记账查库
_PRICE_CACHE_TTL = 60.0


def estimate_tokens(text: str) -> int:
    """后端未返回 usage 时的兜底估算：约 4 字符/token；空文本记 0。"""
    if not text:
        return 0
    return max(1, len(text) // 4)


def _messages_text(messages: list[dict[str, Any]]) -> str:
    """把 messages 的 content 拼成一段文本，供上传量兜底估算。"""
    return "\n".join(str(m.get("content", "") or "") for m in messages)


class TokenUsageTracker:
    """进程内速率窗口 + 当日总量（router.db 持久化）。线程安全。"""

    def __init__(
        self,
        *,
        db_path: str | None = None,
        window_seconds: float = _RATE_WINDOW_SECONDS,
    ) -> None:
        self._db_path = db_path  # None → 每次从 settings 读（测试可 monkeypatch）
        self._window = float(window_seconds)
        self._lock = threading.Lock()
        # 样本：(monotonic, upload, download, calls)
        self._samples: deque[tuple[float, int, int, int]] = deque()
        self._day = date.today().isoformat()
        self._today_up = 0
        self._today_down = 0
        self._today_calls = 0
        self._today_cost = 0.0
        # 当日按模型费用明细（进程内，不持久化 —— 重启后重新累计）
        self._cost_by_model: dict[str, float] = {}
        # 单价缓存：key(backend, model) → (expire_ts, cost_per_1k, display_model)
        self._price_cache: dict[tuple[str, str], tuple[float, float, str]] = {}
        self._loaded = False

    # ---- 持久化 -------------------------------------------------------------

    def _resolve_db_path(self) -> str:
        if self._db_path:
            return self._db_path
        from agent.config import settings

        return settings.llm_router_db_path

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self._resolve_db_path(), timeout=5)
        conn.executescript(_DAILY_TABLE_SCHEMA)
        # 存量库（旧 schema 无 call_count / cost_total 列）兼容：补列，已存在则忽略
        for column in (
            "call_count INTEGER NOT NULL DEFAULT 0",
            "cost_total REAL NOT NULL DEFAULT 0.0",
        ):
            try:
                conn.execute(f"ALTER TABLE token_usage_daily ADD COLUMN {column}")
                conn.commit()
            except sqlite3.OperationalError:
                pass  # duplicate column —— 已是新 schema
        return conn

    def _load_today_locked(self) -> None:
        """从 DB 载入当日累计（跨重启保留）；失败时归零不抛。调用方持锁。"""
        try:
            conn = self._connect()
            try:
                cur = conn.execute(
                    "SELECT upload_tokens, download_tokens, call_count, cost_total "
                    "FROM token_usage_daily WHERE day=?",
                    (self._day,),
                )
                row = cur.fetchone()
                self._today_up = int(row[0]) if row else 0
                self._today_down = int(row[1]) if row else 0
                self._today_calls = int(row[2]) if row else 0
                self._today_cost = float(row[3]) if row else 0.0
            finally:
                conn.close()
        except Exception as exc:
            logger.debug("token_usage load failed: %s", exc)
            self._today_up = 0
            self._today_down = 0
            self._today_calls = 0
            self._today_cost = 0.0
        self._loaded = True

    def _persist_locked(self, day: str, up: int, down: int, calls: int, cost: float) -> None:
        """增量落库（UPSERT 累加）。调用方持锁；失败只记 debug 日志。"""
        try:
            conn = self._connect()
            try:
                conn.execute(
                    "INSERT INTO token_usage_daily"
                    "(day, upload_tokens, download_tokens, call_count, cost_total, updated_at) "
                    "VALUES (?, ?, ?, ?, ?, ?) "
                    "ON CONFLICT(day) DO UPDATE SET "
                    "upload_tokens=upload_tokens+excluded.upload_tokens, "
                    "download_tokens=download_tokens+excluded.download_tokens, "
                    "call_count=call_count+excluded.call_count, "
                    "cost_total=cost_total+excluded.cost_total, "
                    "updated_at=excluded.updated_at",
                    (day, up, down, calls, cost, int(time.time())),
                )
                conn.commit()
            finally:
                conn.close()
        except Exception as exc:
            logger.debug("token_usage persist failed: %s", exc)

    # ---- 费用 ---------------------------------------------------------------

    def _price_for(self, backend: str, model: str) -> tuple[float, str]:
        """查单价（cost_per_1k_tokens，来自模型管理 llm_backends）。

        优先按 model_name 精确匹配；未命中时按 backend 标签对应的 type 取
        第一个后端价格。查不到 / 单价为 0（本地免费模型）→ (0.0, ...)。
        结果缓存 60s；任何异常 → 0（计量不阻塞）。

        Returns:
            (cost_per_1k_tokens, display_model_name)
        """
        key = (backend or "", model or "")
        cached = self._price_cache.get(key)
        now = time.time()
        if cached is not None and cached[0] > now:
            return cached[1], cached[2]
        cost, display = 0.0, (model or backend or "unknown")
        try:
            conn = sqlite3.connect(self._resolve_db_path(), timeout=5)
            try:
                cur = conn.execute("SELECT model_name, type, cost_per_1k_tokens FROM llm_backends")
                rows = cur.fetchall()
            finally:
                conn.close()
            btype = _LABEL_TO_BACKEND_TYPE.get(backend or "", "")
            if model:
                for model_name, _t, price in rows:
                    if model_name == model:
                        cost = float(price or 0.0)
                        display = model_name
                        break
            if cost <= 0 and btype:
                # 精确匹配未命中：同 type 第一个后端的价格兜底
                for model_name, t, price in rows:
                    if t == btype:
                        cost = float(price or 0.0)
                        display = model_name or display
                        break
        except Exception as exc:
            logger.debug("token_usage price lookup failed: %s", exc)
        self._price_cache[key] = (now + _PRICE_CACHE_TTL, cost, display)
        return cost, display

    # ---- 写入 ---------------------------------------------------------------

    def record(
        self,
        *,
        upload_tokens: int,
        download_tokens: int,
        backend: str = "",
        model: str = "",
    ) -> None:
        """记账一次 LLM 调用的 token 消耗（同时计一次调用 + 按单价计费）。"""
        up = max(0, int(upload_tokens))
        down = max(0, int(download_tokens))
        cost_per_1k, display_model = self._price_for(backend, model)
        cost = round((up + down) * cost_per_1k / 1000.0, 8) if cost_per_1k > 0 else 0.0
        now = time.monotonic()
        with self._lock:
            today = date.today().isoformat()
            if today != self._day:
                # 跨天滚动：速率窗口保留，当日总量重新载入，按模型明细清零
                self._day = today
                self._loaded = False
                self._cost_by_model = {}
            if not self._loaded:
                self._load_today_locked()
            self._today_up += up
            self._today_down += down
            self._today_calls += 1
            self._today_cost += cost
            if cost > 0:
                self._cost_by_model[display_model] = (
                    self._cost_by_model.get(display_model, 0.0) + cost
                )
            self._samples.append((now, up, down, 1))
            self._trim_locked(now)
            day, delta_up, delta_down = self._day, up, down
        self._persist_locked(day, delta_up, delta_down, 1, cost)
        logger.debug(
            "token_usage backend=%s up=%d down=%d cost=%.6f",
            backend or "?",
            up,
            down,
            cost,
        )

    # ---- 读取 ---------------------------------------------------------------

    def _trim_locked(self, now: float) -> None:
        cutoff = now - self._window
        while self._samples and self._samples[0][0] < cutoff:
            self._samples.popleft()

    def snapshot(self) -> dict[str, Any]:
        """当前速率与当日总量快照（GET /llm/token-usage 直接返回）。"""
        now = time.monotonic()
        with self._lock:
            if not self._loaded:
                self._load_today_locked()
            self._trim_locked(now)
            win_up = sum(s[1] for s in self._samples)
            win_down = sum(s[2] for s in self._samples)
            win_calls = sum(s[3] for s in self._samples)
            return {
                "day": self._day,
                "window_seconds": int(self._window),
                "rate_upload_per_s": round(win_up / self._window, 1),
                "rate_download_per_s": round(win_down / self._window, 1),
                "rate_calls_per_s": round(win_calls / self._window, 2),
                "today_upload_tokens": self._today_up,
                "today_download_tokens": self._today_down,
                "today_total_tokens": self._today_up + self._today_down,
                "today_call_count": self._today_calls,
                "today_cost_total": round(self._today_cost, 6),
                "cost_by_model": {k: round(v, 6) for k, v in self._cost_by_model.items()},
            }


# ---- 模块级单例 + best-effort 记账入口 --------------------------------------

_tracker: TokenUsageTracker | None = None
_tracker_lock = threading.Lock()


def get_token_usage_tracker() -> TokenUsageTracker:
    global _tracker
    if _tracker is None:
        with _tracker_lock:
            if _tracker is None:
                _tracker = TokenUsageTracker()
    return _tracker


def reset_token_usage_tracker_for_testing(tracker: TokenUsageTracker | None = None) -> None:
    """测试 hook：替换/清空单例。"""
    global _tracker
    _tracker = tracker


def record_usage(
    *,
    upload_tokens: int,
    download_tokens: int,
    backend: str = "",
    model: str = "",
) -> None:
    """best-effort 记账：任何异常都吞掉（计量绝不影响 LLM 主链路）。"""
    try:
        get_token_usage_tracker().record(
            upload_tokens=upload_tokens,
            download_tokens=download_tokens,
            backend=backend,
            model=model,
        )
    except Exception as exc:
        logger.debug("token_usage record failed: %s", exc)


def record_openai_usage(
    body: dict[str, Any] | None,
    *,
    backend: str,
    model: str = "",
    fallback_messages: list[dict[str, Any]] | None = None,
    fallback_output: str = "",
) -> None:
    """OpenAI 兼容协议（private / cloud / local_small）：读 usage 字段记账。

    usage 缺失时按字符数兜底估算，保证计量表始终能动。
    """
    usage = (body or {}).get("usage") or {}
    try:
        up = int(usage.get("prompt_tokens") or 0)
        down = int(usage.get("completion_tokens") or 0)
    except (TypeError, ValueError):
        up, down = 0, 0
    if up <= 0:
        up = estimate_tokens(_messages_text(fallback_messages or []))
    if down <= 0:
        down = estimate_tokens(fallback_output)
    record_usage(upload_tokens=up, download_tokens=down, backend=backend, model=model)


def record_ollama_usage(
    body: dict[str, Any] | None,
    *,
    backend: str = "ollama",
    model: str = "",
    fallback_input: str = "",
    fallback_output: str = "",
) -> None:
    """Ollama /api/chat、/api/generate：读 prompt_eval_count / eval_count 记账。"""
    body = body or {}
    try:
        up = int(body.get("prompt_eval_count") or 0)
        down = int(body.get("eval_count") or 0)
    except (TypeError, ValueError):
        up, down = 0, 0
    if up <= 0:
        up = estimate_tokens(fallback_input)
    if down <= 0:
        down = estimate_tokens(fallback_output)
    record_usage(upload_tokens=up, download_tokens=down, backend=backend, model=model)
