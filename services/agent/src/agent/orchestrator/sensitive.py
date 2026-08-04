"""orchestrator.sensitive —— Phase 12 V1.5 敏感负载二次校验（铁律 3 / CLAUDE.md §2）。

设计（实现文档 §7.2 `prompt_safe_for_remote`）：
    子 Agent 的 prompt 往往由 DB 行 / SQL 错误 / 日志片段拼成 —— 这些内容
    **绝不能**路由到内网 LLM。本模块在 Worker 消费任务前做一次独立检测：

      1. `_LOCAL_ONLY_TASKS` 命中（task_type / model_policy.task_type）→ 强制本地
      2. `ModelPolicy.carries_sensitive_payload=True` 显式声明 → 强制本地
      3. 内容启发式命中（PII / DB 凭证字段 / SQL 错误码）→ 强制本地

    三条任意一条命中 → `local_only=True`，Orchestrator 把 backend 钉死在
    `ollama` / `local_small`，不给 TokenBucket 降级到内网或 mock 的机会。

与 Phase 2F+ `loganalysis/scrubber.py` 的关系：
    scrubber 负责「脱敏后再发」，本模块负责「压根不发」。两者互补，均不依赖对方
    （避免跨 Phase 循环依赖）。
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from agent.llm.router import _LOCAL_ONLY_TASKS

# ---- 检测规则 -------------------------------------------------------------

# PII（与 loganalysis/scrubber.py 同源的 7 类正则；此处仅做「命中判断」不做替换）
_PII_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("phone", re.compile(r"\b1[3-9]\d{9}\b")),
    ("id_card", re.compile(r"\b\d{17}[\dXx]\b")),
    ("bank_card", re.compile(r"\b\d{16,19}\b")),
    ("aws_key", re.compile(r"\bAKIA[0-9A-Z]{16}\b")),
    ("jwt", re.compile(r"\beyJ[A-Za-z0-9_-]+\.eyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\b")),
    ("email", re.compile(r"\b[\w._%+-]+@[\w.-]+\.[A-Za-z]{2,}\b")),
)

# DB 行 / 凭证特征字段名
_CREDENTIAL_KEYS = frozenset({
    "password", "passwd", "pwd", "secret", "api_key", "apikey", "token",
    "dsn", "connection_string", "conn_str", "private_key", "authorization",
    "credit_card", "id_card", "ssn", "session_id", "cookie",
})

# SQL 错误 / DB 报错特征
_SQL_ERROR_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("oracle_error", re.compile(r"\bORA-\d{4,5}\b")),
    ("pg_error", re.compile(r"\bSQLSTATE\b|\bERROR:\s+.*\b(relation|column|syntax)\b", re.I)),
    ("mysql_error", re.compile(r"\bERROR\s+\d{4}\s*\(\w{5}\)")),
    ("generic_sql_error", re.compile(r"\bsyntax error at or near\b|\bunknown column\b", re.I)),
)

# DDL/DML 语句 + 结果行（说明 prompt 里塞了真实数据库内容）
_SQL_RESULTSET_PATTERN = re.compile(
    r"\bSELECT\b[\s\S]{0,200}\bFROM\b", re.I,
)


@dataclass
class SensitivityVerdict:
    """敏感负载检测结论。"""
    local_only: bool
    reasons: list[str] = field(default_factory=list)

    @property
    def safe_for_remote(self) -> bool:
        return not self.local_only

    def to_dict(self) -> dict[str, Any]:
        return {"local_only": self.local_only, "reasons": list(self.reasons)}


def _scan_text(text: str) -> list[str]:
    hits: list[str] = []
    if not text:
        return hits
    for label, pat in _PII_PATTERNS:
        if pat.search(text):
            hits.append(f"pii:{label}")
    for label, pat in _SQL_ERROR_PATTERNS:
        if pat.search(text):
            hits.append(f"sql_error:{label}")
    if _SQL_RESULTSET_PATTERN.search(text):
        hits.append("sql_resultset")
    lowered = text.lower()
    for key in _CREDENTIAL_KEYS:
        # 形如 `password=xxx` / `"password": "xxx"` / `password: xxx`
        if re.search(rf"\b{re.escape(key)}\b\s*[:=]", lowered):
            hits.append(f"credential_key:{key}")
    return hits


def _scan_payload(payload: Any, *, _depth: int = 0) -> list[str]:
    """递归扫描结构化 payload（dict / list / str）。"""
    hits: list[str] = []
    if _depth > 5:
        return hits
    if isinstance(payload, dict):
        for k, v in payload.items():
            if str(k).lower() in _CREDENTIAL_KEYS:
                hits.append(f"credential_key:{str(k).lower()}")
            hits.extend(_scan_payload(v, _depth=_depth + 1))
    elif isinstance(payload, (list, tuple)):
        for item in payload:
            hits.extend(_scan_payload(item, _depth=_depth + 1))
    elif isinstance(payload, str):
        hits.extend(_scan_text(payload))
    return hits


def prompt_safe_for_remote(
    prompt: str,
    payload: dict[str, Any] | None = None,
) -> tuple[bool, list[str]]:
    """检测 prompt + 结构化输入是否可以发往内网 LLM。

    Returns:
        (safe, reasons) —— safe=False 时 reasons 说明命中了哪些规则。
    """
    hits = _scan_text(prompt or "")
    if payload:
        hits.extend(_scan_payload(payload))
    # 去重保持稳定顺序
    dedup = list(dict.fromkeys(hits))
    return (len(dedup) == 0), dedup


def classify_spec(spec: Any, prompt: str = "") -> SensitivityVerdict:
    """对 `SubAgentSpec` 做完整敏感度判定（三条规则合并）。

    Args:
        spec: `SubAgentSpec`（弱类型入参，避免 spec.py ↔ sensitive.py 循环 import）
        prompt: 已组装好的 prompt（可空 → 只看 spec 的结构化字段）
    """
    reasons: list[str] = []

    task_type = getattr(spec, "task_type", "") or ""
    model_policy = getattr(spec, "model_policy", None)
    policy_task = getattr(model_policy, "task_type", "") or ""

    if task_type in _LOCAL_ONLY_TASKS:
        reasons.append(f"local_only_task:{task_type}")
    if policy_task in _LOCAL_ONLY_TASKS:
        reasons.append(f"local_only_task:{policy_task}")
    if getattr(model_policy, "carries_sensitive_payload", False):
        reasons.append("declared_sensitive_payload")

    safe, hits = prompt_safe_for_remote(prompt, getattr(spec, "input_payload", None))
    if not safe:
        reasons.extend(hits)

    return SensitivityVerdict(local_only=bool(reasons), reasons=list(dict.fromkeys(reasons)))


__all__ = [
    "SensitivityVerdict",
    "prompt_safe_for_remote",
    "classify_spec",
]
