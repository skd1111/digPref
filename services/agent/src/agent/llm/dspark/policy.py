"""DSpark 策略路由 + YAML 加载器。

加载顺序：
    1. try-load config/llm/speculative.yaml（存在时）
    2. 不存在 / 解析失败 → 落回 DEFAULT_POLICIES

决策顺序（decide_dspark）：
    1. 全局开关关闭 → 全部 off
    2. 任务类别在 _LOCAL_ONLY_TASKS → 强制 off（覆盖 YAML）
    3. max_tokens < short_output_threshold → 跳过 DSpark（off）
    4. 草稿模型路径为空 → 全部 off
    5. 任务类别无策略 → 落回 conservative
    6. 应用策略
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Iterable

import yaml
from pydantic import ValidationError

from agent.llm.dspark.config import (
    DEFAULT_POLICIES,
    DSparkConfig,
    SPECULATIVE_OFF,
    SpeculativePolicy,
    policy_for_mode,
)


logger = logging.getLogger(__name__)


# 必须在 import 时由 router 注入（避免循环）；V0 用默认值
_LOCAL_ONLY_TASKS: frozenset[str] = frozenset(
    {"intent", "repair", "skill_router", "data_summary", "biznav_extract",
     "local_intent", "vision_understand", "log_level_classify"}
)


def set_local_only_tasks(tasks: Iterable[str]) -> None:
    """在 router 完成初始化时调用一次，把 _LOCAL_ONLY_TASKS 注入到这里。

    保留 fn 而不是直接 import router —— 避免 dspark → llm → … 循环。
    """
    global _LOCAL_ONLY_TASKS
    _LOCAL_ONLY_TASKS = frozenset(tasks)


def get_local_only_tasks() -> frozenset[str]:
    return _LOCAL_ONLY_TASKS


# === YAML 加载 ============================================================


PolicyMap = dict[str, SpeculativePolicy]


def load_speculative_policies(yaml_path: Path | None) -> PolicyMap:
    """从 YAML 加载策略映射。

    YAML 格式（精简版，profiles 是 list + 类别列表）：
        profiles:
          - mode: aggressive
            task_categories: [sql_generation, code_completion]
          - mode: standard
            task_categories: [log_analysis, code_explanation]
          - mode: conservative
            task_categories: [chat_qa]
          - mode: off
            task_categories: [intent, repair, data_summary]

    或每类别显式覆盖 K/阈值：
        profiles:
          - mode: aggressive
            task_categories: [sql_generation]
            n_draft: 6
            draft_p_min: 0.80

    失败 / 文件不存在 → 落回 DEFAULT_POLICIES。
    """
    if yaml_path is None or not yaml_path.exists():
        return dict(DEFAULT_POLICIES)
    try:
        with open(yaml_path, encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
    except (OSError, yaml.YAMLError) as e:
        logger.warning("[DSpark] failed to load speculative.yaml: %s, fall back to defaults", e)
        return dict(DEFAULT_POLICIES)

    profiles = data.get("profiles") or []
    if not isinstance(profiles, list):
        logger.warning("[DSpark] speculative.yaml 'profiles' must be a list, fall back to defaults")
        return dict(DEFAULT_POLICIES)

    valid_modes = {"aggressive", "standard", "conservative", "off"}
    out: PolicyMap = {}
    for entry in profiles:
        if not isinstance(entry, dict):
            continue
        try:
            mode = entry["mode"]
            cats = entry["task_categories"]
        except KeyError:
            logger.warning("[DSpark] profile missing 'mode' or 'task_categories': %s", entry)
            continue
        if not isinstance(cats, list):
            continue
        n_draft_override = entry.get("n_draft")
        pmin_override = entry.get("draft_p_min")
        # YAML 1.1 把 off/no/true/false/y/n 解析成 bool；防御性转回 str
        if isinstance(mode, bool):
            mode = "off" if not mode else "on"
        # YAML 1.1 on → "on" 不是合法 SpeculativeMode，会被 ValidationError 静默吞掉
        # 显式拦截 + 警告（问题 7：防御不完整）
        if isinstance(mode, str) and mode not in valid_modes:
            logger.warning(
                "[DSpark] unknown mode %r in profile %s; expected one of %s",
                mode,
                cats,
                sorted(valid_modes),
            )
            continue
        for cat in cats:
            try:
                if n_draft_override is not None and pmin_override is not None:
                    pol = SpeculativePolicy(
                        task_category=cat,
                        mode=mode,
                        n_draft=n_draft_override,
                        draft_p_min=pmin_override,
                    )
                else:
                    n_d, pmin = policy_for_mode(mode)
                    pol = SpeculativePolicy(
                        task_category=cat,
                        mode=mode,
                        n_draft=entry.get("n_draft", n_d),
                        draft_p_min=entry.get("draft_p_min", pmin),
                    )
                out[cat] = pol
            except ValidationError as e:
                logger.warning("[DSpark] invalid policy for category %s: %s", cat, e)
                continue
    # 任何 DEFAULT_POLICIES 里有但 YAML 没覆盖的 → 仍然保留默认值
    for cat, pol in DEFAULT_POLICIES.items():
        out.setdefault(cat, pol)
    return out


# === 决策 ================================================================


# 5 关检查原因常量（统一在 policy.py 和 api.py 使用）
REASON_OFF_GLOBAL = "off-global"
REASON_OFF_LOCAL_ONLY = "off-local-only"
REASON_OFF_SHORT = "off-short"
REASON_OFF_NO_DRAFT = "off-no-draft"
REASON_APPLIED = "applied"
REASON_APPLIED_DEFAULT = "applied-default"
REASON_OFF_NO_RUNTIME = "off-no-runtime"


def _decide_dspark_with_reason(
    *,
    config: DSparkConfig | None,
    task_category: str,
    max_tokens: int,
    policies: PolicyMap | None,
    local_only_tasks: frozenset[str] | None = None,
) -> tuple[SpeculativePolicy, str]:
    """决策 + 返回 reason（唯一真源，policy.py + api.py 都复用）。

    五关铁律（**顺序敏感**，锁死）：
        1. runtime 未初始化 → off-no-runtime
        2. 全局开关关闭 → off-global
        3. 敏感任务（_LOCAL_ONLY_TASKS）→ off-local-only（铁律，覆盖 YAML）
        4. 短输出 → off-short
        5. 草稿模型路径为空 → off-no-draft
        6. 类别策略 → applied；若类别未知 → applied-default（conservative 兜底）

    返回 (policy, reason)。**policy** 始终为禁用态当 reason 以 off- 开头。
    """
    local_only = local_only_tasks if local_only_tasks is not None else _LOCAL_ONLY_TASKS

    # 1. runtime 未初始化（生产路径由 api.py 判定；测试可传 None）
    if config is None:
        return SPECULATIVE_OFF, REASON_OFF_NO_RUNTIME

    # 2. 全局开关
    if not config.enable_global:
        return SPECULATIVE_OFF, REASON_OFF_GLOBAL

    # 3. 敏感任务（铁律，早于短输出判定 —— 即便短输出也先 off-local-only）
    if task_category in local_only:
        return SPECULATIVE_OFF, REASON_OFF_LOCAL_ONLY

    # 4. 短输出
    if max_tokens < config.short_output_threshold:
        return SPECULATIVE_OFF, REASON_OFF_SHORT

    # 5. 草稿模型缺失
    if not config.draft_model_path:
        return SPECULATIVE_OFF, REASON_OFF_NO_DRAFT

    # 6. 类别策略
    if policies is not None:
        pol = policies.get(task_category)
        if pol is not None:
            return pol, REASON_APPLIED

    # 未知类别 → conservative 兜底（必须用 policy_for_mode 派生 K/p_min，否则默认 n_draft=1 → enabled=False）
    n_d, p_min = policy_for_mode("conservative")
    fallback = SpeculativePolicy(
        task_category=task_category,
        mode="conservative",
        n_draft=n_d,
        draft_p_min=p_min,
    )
    return fallback, REASON_APPLIED_DEFAULT


def decide_dspark(
    *,
    config: DSparkConfig,
    policies: PolicyMap,
    task_category: str,
    max_tokens: int,
    local_only_tasks: frozenset[str] | None = None,
) -> SpeculativePolicy:
    """只决策，不返 reason（公开 API 兼容老调用方 + 测试）。"""
    pol, _ = _decide_dspark_with_reason(
        config=config,
        task_category=task_category,
        max_tokens=max_tokens,
        policies=policies,
        local_only_tasks=local_only_tasks,
    )
    return pol
