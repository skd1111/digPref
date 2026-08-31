"""evolution.signature —— 任务签名（自评测与进化的归一化主键）。

设计文档 §2.4：
    task_signature = hash(normalize(intent_category) | active_skill_id | tool_fp)

意义：把「同一类任务」聚合到一起，才能统计哪类任务做得好 / 差，
驱动经验复用（L1）与后续的技能蒸馏（L2）/ Prompt 优化（L3）。

工具指纹只取有序工具名，**不含参数明文**（敏感红线：参数可能含
SQL / PII / 凭证，绝不进入进化持久层）。
"""

from __future__ import annotations

import hashlib
from collections.abc import Sequence


def tool_fingerprint(tool_names: Sequence[str]) -> str:
    """有序工具名指纹：去重保序，逗号拼接。

    保序而非集合排序：同一任务签名下「先查库再写文件」与「先写文件再查库」
    是不同轨迹形态，保留顺序信息便于反思归因。
    """
    seen: list[str] = []
    for name in tool_names:
        n = str(name or "").strip()
        if n and n not in seen:
            seen.append(n)
    return ",".join(seen)


def compute_task_signature(
    intent_category: str,
    skill_id: str,
    tool_names: Sequence[str],
) -> str:
    """计算任务签名（归一化幂等：同输入恒等，大小写 / 空白不敏感）。"""
    intent = (intent_category or "").strip().lower()
    skill = (skill_id or "").strip()
    fp = tool_fingerprint(tool_names)
    raw = f"{intent}|{skill}|{fp}"
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:16]
