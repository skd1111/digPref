"""evolution.prompt_opt —— Few-shot 影子回放优化（L3，DSPy/OPRO 裁剪，V1.5，设计文档 §5）。

可学习参数与禁区（红线）：
    - 只优化 Skill 的 `few_shot_examples`；**不改模板结构 / System Prompt
      主干**，不碰 `_LOCAL_ONLY_TASKS` 涉及的敏感任务模板。
    - 任务 `prompt_optimize` 接触用户历史请求与反馈 → 本地红线。

机制（影子评测离线只读，不影响在线链路）：
    1. 取材：同签名低分反馈（用户 👎 纠错 / Judge 低分理由）+ 历史请求
    2. 候选生成：LLM 输入现有 few-shot + 失败反馈 → 生成新 few-shot（OPRO 式）
    3. 影子回放：新旧两版 few-shot 各自引导模型对历史请求生成草稿回答，
       再经 `answer_judge` 打分，比较均分
    4. 采纳门槛：仅当增益 ≥ `prompt_optimize_gain_threshold` 才写入
       `prompt_versions`（candidate）；默认人工确认采纳（`apply` 端点），
       `evolution_prompt_auto_adopt` 可开自动采纳；版本保留，可一键回滚
"""

from __future__ import annotations

import json
import logging
from typing import Any

from agent.config import settings
from agent.evolution import storage
from agent.evolution.events import EVT_EVOLUTION_EXPERIMENT_DONE, emit_evolution_event
from agent.llm.json_discipline import extract_json
from agent.llm.prompts import load_prompt, render_prompt

logger = logging.getLogger(__name__)

_MAX_REPLAY_REQUESTS = 2  # 影子回放请求条数（桌面端控制 LLM 调用成本）
_MAX_EXAMPLES = 6  # few-shot 条目上限（3 对）
_SKILL_PROMPT_DIGEST_CHARS = 600


def format_few_shot_block(few_shot: list[dict[str, str]]) -> str:
    """把 few-shot 列表拼成回放提示词里的示例块。"""
    lines: list[str] = []
    for ex in few_shot:
        role = str(ex.get("role") or "").strip()
        content = str(ex.get("content") or "").strip()
        if role in ("user", "assistant") and content:
            label = "用户" if role == "user" else "助手"
            lines.append(f"[{label}] {content[:500]}")
    return "\n".join(lines) if lines else "（无示例）"


def parse_candidate_few_shot(text: str) -> list[dict[str, str]] | None:
    """解析优化器输出的候选 few-shot（结构校验 + 上限裁剪；非法返 None）。"""
    data = extract_json(text or "", want="object")
    if not isinstance(data, dict):
        return None
    raw = data.get("few_shot")
    if not isinstance(raw, list) or not raw:
        return None
    out: list[dict[str, str]] = []
    for item in raw[:_MAX_EXAMPLES]:
        if not isinstance(item, dict):
            continue
        role = str(item.get("role") or "").strip()
        content = str(item.get("content") or "").strip()[:500]
        if role in ("user", "assistant") and content:
            out.append({"role": role, "content": content})
    return out or None


async def _replay_score(few_shot: list[dict[str, str]], requests: list[str]) -> float | None:
    """影子回放打分：few-shot 引导生成草稿 → answer_judge 评分 → 均分。"""
    from agent.llm.router import LMRouter

    router = LMRouter()
    scores: list[int] = []
    block = format_few_shot_block(few_shot)
    for request in requests:
        try:
            draft = await router.route(
                task="prompt_optimize",
                prompt=render_prompt(
                    load_prompt("evolution/replay"),
                    FEW_SHOT_BLOCK=block,
                    REQUEST=request[:500],
                ),
            )
            verdict = await router.route(
                task="answer_judge",
                prompt=render_prompt(
                    load_prompt("evolution/answer_judge"),
                    USER_PROMPT=request[:500],
                    ANSWER=str(draft or "")[:1500],
                ),
            )
        except Exception as exc:
            logger.info("[evolution] replay scoring skipped: %s", exc)
            continue
        from agent.orchestrator.eval_collector import _parse_judge_output

        score, _ = _parse_judge_output(verdict or "")
        if score > 0:
            scores.append(score)
    if not scores:
        return None
    return sum(scores) / len(scores)


def _get_skill(skill_id: str) -> Any:
    from agent.skills import api as skills_api

    skill = skills_api.get_loader().get(skill_id)
    if skill is None:
        raise ValueError(f"skill {skill_id} not found")
    return skill


async def run_prompt_experiment(
    *,
    skill_id: str,
    task_signature: str = "",
    db_path: str | None = None,
) -> dict[str, Any]:
    """运行一次 Few-shot 影子优化实验。返回结果 dict（含 gain / 版本信息）。

    失败时抛 ValueError（参数/素材问题，API 转 400/404）；
    LLM 不可用等运行时问题记入 experiment_runs（status=failed）并返结果。
    """
    if not settings.evolution_enabled:
        raise ValueError("evolution disabled")
    skill = _get_skill(skill_id)
    current = [{"role": ex.role, "content": ex.content} for ex in skill.few_shot_examples]

    # 1) 取材：低分反馈 + 历史请求（无签名时用该 skill 相关轨迹兜底）
    feedback = (
        await storage.low_score_feedback(task_signature, db_path=db_path) if task_signature else []
    )
    requests = await _replay_requests(task_signature, skill_id, db_path=db_path)
    if not requests:
        raise ValueError("无可回放的历史请求（先积累该任务的执行轨迹）")

    # 2) 候选生成（OPRO 式：输入现有示例 + 失败反馈）
    current_text = json.dumps(current, ensure_ascii=False)[:1200] if current else "（无）"
    feedback_text = (
        "\n".join(f"- {fb}" for fb in feedback) if feedback else "（无具体反馈，按通用质量优化）"
    )
    prompt = render_prompt(
        load_prompt("evolution/prompt_optimize"),
        SKILL_NAME=str(skill.name or skill_id),
        SKILL_PROMPT_DIGEST=str(skill.system_prompt or "")[:_SKILL_PROMPT_DIGEST_CHARS],
        CURRENT_EXAMPLES=current_text,
        FEEDBACK=feedback_text[:1000],
    )
    try:
        from agent.llm.router import LMRouter

        raw = await LMRouter().route(task="prompt_optimize", prompt=prompt)
    except Exception as exc:
        await _record_run(
            task_signature, "failed", {"stage": "generate", "error": str(exc)}, db_path=db_path
        )
        raise ValueError(f"优化器 LLM 不可用：{exc}") from exc

    candidate = parse_candidate_few_shot(raw)
    if candidate is None:
        await _record_run(task_signature, "failed", {"stage": "parse"}, db_path=db_path)
        raise ValueError("优化器输出无法解析为合法 few-shot")

    # 3) 影子回放：新旧两版各自评分（离线，只读历史）
    old_avg = await _replay_score(current, requests)
    new_avg = await _replay_score(candidate, requests)
    if old_avg is None or new_avg is None:
        await _record_run(task_signature, "failed", {"stage": "replay"}, db_path=db_path)
        raise ValueError("影子回放评分失败（LLM 不可用或全部无效）")

    gain = round(new_avg - old_avg, 2)
    significant = gain >= settings.prompt_optimize_gain_threshold
    version_id: int | None = None
    if significant:
        status = "active" if settings.evolution_prompt_auto_adopt else "candidate"
        version_id = await storage.insert_prompt_version(
            skill_id=skill_id,
            few_shot=candidate,
            gain=gain,
            status=status,
            db_path=db_path,
        )
        if status == "active":
            _apply_few_shot_to_skill(skill_id, candidate)
            # 自动采纳也要保证同 skill 单 active（与 api 手动 apply 同语义）
            await _demote_other_actives(skill_id, version_id, db_path=db_path)

    await _record_run(
        task_signature,
        "done",
        {"skill_id": skill_id, "old_avg": old_avg, "new_avg": new_avg, "gain": gain},
        db_path=db_path,
    )
    result = {
        "skill_id": skill_id,
        "old_avg": round(old_avg, 2),
        "new_avg": round(new_avg, 2),
        "gain": gain,
        "significant": significant,
        "version_id": version_id,
        "auto_adopted": bool(significant and settings.evolution_prompt_auto_adopt),
    }
    emit_evolution_event(
        EVT_EVOLUTION_EXPERIMENT_DONE,
        {"kind": "evolution_experiment_done", **result},
    )
    logger.info("[evolution] prompt experiment done %s", result)
    return result


async def _replay_requests(
    task_signature: str, skill_id: str, *, db_path: str | None = None
) -> list[str]:
    """回放请求素材：签名轨迹优先，兜底该 skill 的全部轨迹。"""
    import aiosqlite

    target = storage._db_target(db_path)
    queries = []
    if task_signature:
        queries.append(
            "SELECT intent_json FROM trajectories WHERE task_signature = ? ORDER BY id DESC LIMIT ?"
        )
    queries.append(
        "SELECT intent_json FROM trajectories WHERE active_skill_id = ? ORDER BY id DESC LIMIT ?"
    )
    rows: list[Any] = []
    async with aiosqlite.connect(target) as conn:
        await storage._ensure_schema(conn)
        if task_signature:
            cur = await conn.execute(queries[0], (task_signature, _MAX_REPLAY_REQUESTS))
            rows = list(await cur.fetchall())
        if len(rows) < _MAX_REPLAY_REQUESTS:
            cur = await conn.execute(queries[-1], (skill_id, _MAX_REPLAY_REQUESTS - len(rows)))
            rows.extend(await cur.fetchall())
    requests: list[str] = []
    for (intent_json,) in rows:
        try:
            intent = json.loads(intent_json or "{}")
        except (TypeError, ValueError):
            continue
        query = str((intent or {}).get("rewritten_query") or "").strip()
        if query:
            requests.append(query[:300])
    return requests[:_MAX_REPLAY_REQUESTS]


async def _record_run(
    task_signature: str,
    status: str,
    detail: dict[str, Any],
    *,
    db_path: str | None = None,
) -> None:
    try:
        await storage.record_experiment_run(
            kind="prompt_optimize",
            task_signature=task_signature,
            status=status,
            detail=detail,
            db_path=db_path,
        )
    except Exception as exc:
        logger.warning("[evolution] experiment run record failed: %s", exc)


async def _demote_other_actives(skill_id: str, keep_id: int, *, db_path: str | None = None) -> None:
    """把同 skill 的其他 active 版本降级为 rolled_back（保证单 active；
    自动采纳与手动 apply 共用，保留历史可回溯）。"""
    for it in await storage.list_prompt_versions(skill_id, db_path=db_path):
        if it["status"] == "active" and it["id"] != keep_id:
            await storage.set_prompt_version_status(it["id"], "rolled_back", db_path=db_path)


def _apply_few_shot_to_skill(skill_id: str, few_shot: list[dict[str, str]]) -> bool:
    """把 few-shot 写回技能 YAML 并重载（adopt / rollback 共用；失败返 False）。"""
    import yaml

    from agent.skills import api as skills_api

    loader = skills_api.get_loader()
    skill = loader.get(skill_id)
    if skill is None:
        return False
    path = loader._dir / f"{skill_id}.yaml"
    if not path.exists():
        return False
    try:
        with open(path, encoding="utf-8") as f:
            data = yaml.safe_load(f)
        if not isinstance(data, dict):
            return False
        data["few_shot_examples"] = [
            {"role": ex["role"], "content": ex["content"]} for ex in few_shot
        ]
        with open(path, "w", encoding="utf-8") as f:
            yaml.safe_dump(data, f, allow_unicode=True, sort_keys=False)
        return loader.load_one(path) is not None
    except Exception as exc:
        logger.warning("[evolution] apply few-shot to skill failed: %s", exc)
        return False
