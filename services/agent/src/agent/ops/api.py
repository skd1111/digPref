"""ops.api —— FastAPI 路由（运营工作台业务记录，Phase 2H）。

端点：
  POST   /ops/records               — 创建业务记录卡片
  GET    /ops/records               — 列表（feature_id / project_name / limit 过滤）
  GET    /ops/records/{id}          — 详情
  DELETE /ops/records/{id}          — 删除
  POST   /ops/records/summarize     — AI 根据会话 + 功能点 + Skill 生成总结草稿

专家验收工作流（2026-08-10，取代运营模式的传统大 Chat）：
  GET    /ops/case                  — 获取 Case（材料文件 + 问答记录 + 交付草稿）
  POST   /ops/case/files            — 上传材料给对应专家
  POST   /ops/case/files/{id}/review   — 先过「系统合规审核」（文档审核同款分类 + 分块分析）
      → 有风险点随材料落库（前端弹报告，附原文证据）→ 专家按人设验收
  POST   /ops/case/files/{id}/override — 人工改判
  DELETE /ops/case/files/{id}       — 删除材料
  POST   /ops/case/ask              — 向专家提问（模板/清单类请求 → 生成直填草稿）
  POST   /ops/case/export           — 全部验收后打包导出交付物 zip

交付草稿（BUGFIX #78：要模板不给一大段文字，界面直填）：
  GET    /ops/case/drafts           — 草稿列表
  PUT    /ops/case/drafts/{id}      — 保存填写值
  POST   /ops/case/drafts/{id}/submit — 提交：渲染成材料自动审核，通过即入交付物
  POST   /ops/case/drafts/{id}/ocr-fill — 上传文件（2026-11-05）：本地 OCR / 文档解析
      → 模型抽取字段值 → 前端自动填入表单（所有专家草稿表单通用）

业务办理台账（业务汇总，2026-11-09）：
POST   /ops/tasks              — 业务发起建档（幂等；上传第一份材料时）
GET    /ops/tasks              — 台账查询（状态 / 大类 / 小类 / 日期 / 关键词）
GET    /ops/tasks/{id}         — 台账详情（待处理项 + 材料进度 + 关联小结）
POST   /ops/tasks/{id}/status  — 人工挂起 / 恢复办理

LLM 优先级与 biznav/reqflow 一致：本地 Ollama → DB 内网 → DB 云端（三级降级链）。
"""

from __future__ import annotations

import asyncio
import base64
import json
import logging
import os
import re
import tempfile
import time
import zipfile
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from agent.config import settings
from agent.doc_review.llm import LLMFunc
from agent.paths import data_root

from .cases import (
    ALL_FILE_STATUSES,
    FILE_PASSED,
    FILE_PENDING,
    FILE_REJECTED,
    FILE_REVIEWING,
    CaseStorage,
    dump_json,
    is_image_file,
    make_case_id,
)
from .doc_audit import run_doc_audit
from .models import ALL_RESULTS
from .storage import BusinessRecordStorage
from .tasks import (
    TASK_CLOSED,
    TASK_IN_PROGRESS,
    TASK_MANUAL_REVIEW,
    TASK_STATUS_LABEL,
    TASK_SUSPENDED,
    BusinessTask,
    TaskStorage,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/ops", tags=["ops"])


# ---------------------------------------------------------------------------
# Storage 单例
# ---------------------------------------------------------------------------


def _default_db_path() -> str:
    # BUGFIX #98：统一落数据根（生产=安装目录）
    return str(data_root() / "ops.db")


_storage: BusinessRecordStorage | None = None


def _get_storage(db_path: str | None = None) -> BusinessRecordStorage:
    global _storage
    if db_path:
        return BusinessRecordStorage(db_path)
    if _storage is None:
        _storage = BusinessRecordStorage(os.environ.get("EAIDE_OPS_DB", _default_db_path()))
    return _storage


def _reset_storage_for_tests() -> None:
    global _storage
    _storage = None


_case_storage: CaseStorage | None = None


def _get_case_storage(db_path: str | None = None) -> CaseStorage:
    global _case_storage
    if db_path:
        return CaseStorage(db_path)
    if _case_storage is None:
        _case_storage = CaseStorage(os.environ.get("EAIDE_OPS_DB", _default_db_path()))
    return _case_storage


def _reset_case_storage_for_tests() -> None:
    global _case_storage
    _case_storage = None


_task_storage: TaskStorage | None = None


def _get_task_storage(db_path: str | None = None) -> TaskStorage:
    """业务办理台账存储（与 Case / 业务记录共用同一个 ops.sqlite 文件）。"""
    global _task_storage
    if db_path:
        return TaskStorage(db_path)
    if _task_storage is None:
        _task_storage = TaskStorage(os.environ.get("EAIDE_OPS_DB", _default_db_path()))
    return _task_storage


def _reset_task_storage_for_tests() -> None:
    global _task_storage
    _task_storage = None


# 本进程正在审核的文件 id（BUGFIX #202 自愈用）：
# 状态是 reviewing 且不在此集合里 = 僵尸（上次 Agent 重启/请求被硬断留下的）。
_INFLIGHT_REVIEWS: set[str] = set()


# ---------------------------------------------------------------------------
# LLM 降级链（与 biznav/reqflow 同一形态）
# ---------------------------------------------------------------------------


def _make_summarize_llm():
    """构造 async (messages) -> str；三级全失败抛 RuntimeError。"""

    async def _call(backend, messages: list[dict]) -> str:
        return str(await backend.extract_chat(messages) or "")

    async def _client(messages: list[dict]) -> str:
        from agent.llm.router import LMRouter

        router = LMRouter()
        if router._mock_mode:
            return await _call(router.mock, messages)

        failures: list[str] = []
        # 1/3 本地 Ollama（本地优先）
        try:
            text = await _call(router.ollama, messages)
            if text.strip():
                return text
            failures.append("本地 Ollama 返回空")
        except Exception as e:
            failures.append(f"本地 Ollama 不可用: {e}")
        # 2/3 内网
        try:
            private = await router._build_private_client()
            if private is not None:
                try:
                    text = await _call(private, messages)
                    if text.strip():
                        return text
                    failures.append("内网模型返回空")
                except Exception as e:
                    failures.append(f"内网模型不可用: {e}")
            else:
                failures.append("内网模型未启用")
        except Exception as e:
            failures.append(f"内网模型查询失败: {e}")
        # 3/3 云端
        try:
            cloud = await router._build_cloud_client()
            if cloud is not None:
                text = await _call(cloud, messages)
                if text.strip():
                    return text
                failures.append("云端模型返回空")
            else:
                failures.append("云端模型未配置")
        except Exception as e:
            failures.append(f"云端模型不可用: {e}")

        raise RuntimeError(
            "所有 LLM 后端均不可用（" + "；".join(failures) + "）。"
            "请启动本地 Ollama，或在「模型管理」中配置可用的云端/内网模型"
        )

    return _client


# ---------------------------------------------------------------------------
# Pydantic 模型
# ---------------------------------------------------------------------------


class CreateRecordRequest(BaseModel):
    project_name: str = ""
    feature_id: str = ""
    business_type: str = ""
    title: str = ""
    summary: str = ""
    materials_checked: list[str] = Field(default_factory=list)
    materials_missing: list[str] = Field(default_factory=list)
    risk_points: list[str] = Field(default_factory=list)
    result: str = "done"
    skill_id: str = ""
    session_id: str = ""
    source: str = "ai"
    created_by: str = ""


class SummarizeRequest(BaseModel):
    feature_id: str = ""
    project_name: str = ""
    business_type: str = ""
    conversation: list[dict[str, str]] = Field(default_factory=list)
    session_id: str = ""


def _validate_result(result: str) -> str:
    if result not in ALL_RESULTS:
        raise HTTPException(400, f"invalid result: {result} (allowed: {', '.join(ALL_RESULTS)})")
    return result


def _load_feature(feature_id: str, project_name: str) -> dict[str, Any] | None:
    if not feature_id:
        return None
    try:
        from agent.biznav.api import _get_storage as _biznav_storage

        f = _biznav_storage().get(feature_id, project_name)
    except Exception as e:
        logger.warning("[ops] biznav storage unavailable: %s", e)
        return None
    if f is None:
        return None
    return {
        "id": f.id,
        "name": f.name,
        "description": f.description,
        "category": f.category,
        "business_rules": [r.text for r in f.business_rules],
        "skill_id": f.skill_id,
    }


def _load_skill(skill_id: str) -> dict[str, Any] | None:
    if not skill_id:
        return None
    try:
        from agent.skills.api import _loader

        s = _loader.get(skill_id)
    except Exception as e:
        logger.warning("[ops] skills loader unavailable: %s", e)
        return None
    if s is None:
        return None
    return {
        "id": s.id,
        "name": s.name,
        "description": s.description,
        "system_prompt": s.system_prompt,
        "trigger_keywords": list(s.trigger_keywords),
    }


def _render_conversation(conversation: list[dict[str, str]]) -> str:
    lines: list[str] = []
    for m in conversation[-30:]:
        role = str(m.get("role", ""))
        content = str(m.get("content", "")).strip()
        if not content:
            continue
        lines.append(f"[{role}] {content[:1200]}")
    return "\n".join(lines)


def _build_summarize_prompt(
    *,
    feature: dict[str, Any] | None,
    skill: dict[str, Any] | None,
    business_type: str,
    conversation_text: str,
) -> list[dict[str, str]]:
    sys_ctx: list[str] = [
        "你是银行运营一线的工作台 AI。用户完成一笔业务后，请把这段会话总结成一张"
        "结构化「业务记录卡片」，要求：",
        "1. 只基于会话与提供的功能点/Skill 经验，不要编造；",
        "2. 材料核对：区分「已具备材料」与「缺失/待补材料」；",
        "3. 风险点：只列会话中出现的风险或 Skill 经验中提示的高风险项；",
        "4. 输出严格 JSON（不要 Markdown 代码块），字段：",
        '   {"title": str, "business_type": str, "summary": str, '
        '"materials_checked": [str], "materials_missing": [str], '
        '"risk_points": [str], "result": "done|pending|rejected|follow_up"}',
        "5. 如果会话不足以判断，result 用 pending，缺失材料写进 materials_missing。",
    ]
    if feature:
        sys_ctx.append(f"【功能点】{feature['name']}（{feature['id']}）：{feature['description']}")
        if feature.get("business_rules"):
            sys_ctx.append("业务规则：\n" + "\n".join(f"- {r}" for r in feature["business_rules"]))
    if skill:
        sys_ctx.append(f"【Skill 经验】{skill['name']}：\n{skill['system_prompt'][:3000]}")
    return [
        {"role": "system", "content": "\n".join(sys_ctx)},
        {
            "role": "user",
            "content": (
                f"业务类型：{business_type or '未指定'}\n\n"
                f"会话记录：\n{conversation_text or '（无会话记录）'}\n\n"
                "请输出业务记录卡片 JSON。"
            ),
        },
    ]


async def _audit(event: str, payload: dict[str, Any]) -> None:
    try:
        from agent.audit.store import audit

        await audit(event, payload)
    except Exception as e:
        logger.warning("[ops] audit emit failed: %s", e)


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@router.post("/records")
async def create_record(req: CreateRecordRequest) -> dict:
    storage = _get_storage()
    _validate_result(req.result)
    rec = storage.create(
        project_name=req.project_name,
        feature_id=req.feature_id,
        business_type=req.business_type,
        title=req.title,
        summary=req.summary,
        materials_checked=req.materials_checked,
        materials_missing=req.materials_missing,
        risk_points=req.risk_points,
        result=req.result,
        skill_id=req.skill_id,
        session_id=req.session_id,
        source=req.source,
        created_by=req.created_by,
    )
    await _audit(
        "ops_record_create",
        {"record_id": rec.id, "feature_id": rec.feature_id, "title": rec.title},
    )
    return rec.to_dict()


@router.get("/records")
async def list_records(
    feature_id: str | None = Query(None),
    project_name: str | None = Query(None),
    limit: int = Query(100),
) -> dict:
    storage = _get_storage()
    records = storage.list(feature_id=feature_id, project_name=project_name, limit=limit)
    return {
        "records": [r.to_dict() for r in records],
        "total": len(records),
    }


@router.get("/records/{record_id}")
async def get_record(record_id: str) -> dict:
    storage = _get_storage()
    rec = storage.get(record_id)
    if rec is None:
        raise HTTPException(404, f"record {record_id} not found")
    return rec.to_dict()


@router.delete("/records/{record_id}")
async def delete_record(record_id: str) -> dict:
    storage = _get_storage()
    storage.delete(record_id)
    await _audit("ops_record_delete", {"record_id": record_id})
    return {"ok": True, "record_id": record_id}


@router.post("/records/summarize")
async def summarize_record(req: SummarizeRequest) -> dict:
    feature = _load_feature(req.feature_id, req.project_name)
    skill_id = feature.get("skill_id") if feature else ""
    skill = _load_skill(skill_id or "")
    conversation_text = _render_conversation(req.conversation)
    messages = _build_summarize_prompt(
        feature=feature,
        skill=skill,
        business_type=req.business_type,
        conversation_text=conversation_text,
    )
    llm_call = _make_summarize_llm()
    try:
        text = await llm_call(messages)
    except RuntimeError as e:
        raise HTTPException(502, str(e)) from e

    draft: dict[str, Any] = {}
    try:
        text = text.strip()
        if text.startswith("```"):
            text = text.strip("`")
            if text.startswith("json"):
                text = text[4:]
        draft = json.loads(text)
    except (ValueError, TypeError) as e:
        logger.warning("[ops] summarize returned non-JSON (%s): %s", e, text[:200])
        raise HTTPException(
            502,
            "模型未返回有效 JSON，请重试或手动填写业务记录",
        ) from e

    if not isinstance(draft, dict):
        raise HTTPException(502, "模型返回格式错误（非对象）")
    if not str(draft.get("title", "")).strip():
        draft["title"] = req.business_type or feature.get("name", "") if feature else ""
    result = str(draft.get("result", "pending"))
    if result not in ALL_RESULTS:
        result = "pending"
    return {
        "draft": {
            "title": str(draft.get("title", "")),
            "business_type": str(draft.get("business_type", req.business_type)),
            "summary": str(draft.get("summary", "")),
            "materials_checked": [str(x) for x in (draft.get("materials_checked") or [])],
            "materials_missing": [str(x) for x in (draft.get("materials_missing") or [])],
            "risk_points": [str(x) for x in (draft.get("risk_points") or [])],
            "result": result,
        }
    }


# ---------------------------------------------------------------------------
# 专家验收工作流（2026-08-10）
# ---------------------------------------------------------------------------


class CaseFileAddRequest(BaseModel):
    case_id: str = Field(min_length=1, max_length=512)
    team_id: str = ""
    member_key: str = Field(min_length=1, max_length=128)
    file_name: str = Field(min_length=1, max_length=512)
    content_base64: str = Field(min_length=1)


class CaseFileOverrideRequest(BaseModel):
    status: str
    note: str = ""


class CaseAskRequest(BaseModel):
    case_id: str = Field(min_length=1, max_length=512)
    team_id: str = ""
    member_key: str = Field(min_length=1, max_length=128)
    question: str = Field(min_length=1, max_length=4000)


class DraftSaveRequest(BaseModel):
    """保存草稿填写值（BUGFIX #78：界面直填）。"""

    values: dict[str, Any] = Field(default_factory=dict)


class DraftOcrFillRequest(BaseModel):
    """上传文件 OCR 自动填入草稿（2026-11-05：所有专家录入表单通用）。"""

    file_name: str = Field(min_length=1, max_length=512)
    content_base64: str = Field(min_length=1)


class CaseExportRequest(BaseModel):
    case_id: str = Field(min_length=1, max_length=512)
    target_path: str = Field(min_length=1, max_length=4096)
    feature_name: str = ""
    team_id: str = ""  # 专家团 id（2026-08-10）：用于定位外部报告模板
    team_name: str = ""
    checklist: list[str] = Field(default_factory=list)


def _resolve_member(team_id: str, member_key: str) -> dict[str, Any] | None:
    """按 team_id + 成员名解析专家人设（prompt / focus_points / outputs）。"""
    if not team_id or not member_key:
        return None
    try:
        from agent.expert_teams.api import get_loader

        team = get_loader().get(team_id)
    except Exception as e:
        logger.warning("[ops-case] expert teams loader unavailable: %s", e)
        return None
    if team is None:
        return None
    for m in team.members:
        if m.name == member_key:
            return m.to_dict()
    return None


def _extract_material_text(file_path: str, file_name: str) -> str:
    """提取非图片类材料的文本（解析失败返空串，不阻断审核）。"""
    try:
        from agent.doc_review.parser import parse_document

        parsed = parse_document(file_path)
        return str(getattr(parsed, "full_text", "") or "")[:20000]
    except Exception as e:
        logger.warning("[ops-case] parse %s failed: %s", file_name, e)
        return ""


async def _vision_describe(file_path: str) -> str:
    """本地视觉模型描述图片内容（不可用返空串）。"""
    try:
        from agent.config import settings
        from agent.llm.local_vision import LocalVisionClient

        client = LocalVisionClient(
            base_url=settings.local_vision_base_url or "http://127.0.0.1:8082/v1",
            model=settings.local_vision_model or "moondream2",
        )
        image_bytes = _read_file_bytes(file_path)
        return str(
            await client.understand_screenshot(
                image_bytes,
                prompt="请详细描述这份材料图片中的关键信息（名称、编号、日期、公章等）。",
            )
            or ""
        )
    except Exception as e:
        logger.debug("[ops-case] local vision unavailable: %s", e)
        return ""


def _read_file_bytes(path: str) -> bytes:
    """同步读文件（单独抽出，避免 async 函数内直接阻塞 IO 的 lint 告警）。"""
    return Path(path).read_bytes()


def _path_parent_exists(target: Path) -> bool:
    return target.parent.exists()


_REVIEW_OUTPUT_RULE = (
    "请验收这份材料，输出严格 JSON（不要 Markdown 代码块）："
    '{"verdict": "passed" 或 "rejected", "note": "验收意见（通过依据或打回原因，中文，不超 200 字）", '
    '"fields": [{"field": "要素名", "value": "要素值", "confidence": 0到1的小数}], '
    '"evidence": ["支撑结论的原文摘录"], '
    '"reject_spans": [{"quote": "问题所在的原文片段", "advice": "该怎么改（不超 60 字）"}]}。'
    "fields 要求：提取材料中的关键要素（如企业名称/统一社会信用代码/法人/有效期/"
    "金额/日期等，按材料类型自行判断），找不到确切值但应有该要素时 confidence 给低于 0.6；"
    "evidence 要求：直接引用材料原文片段（最多 3 条，每条不超 80 字），严禁编造；"
    "reject_spans 要求：仅在 verdict=rejected 时填写，逐条指出问题在原文中的具体位置（"
    "quote 必须是材料里真实存在的片段，最多 5 条）；passed 时给空数组；"
    "判定标准：材料内容与你的职责相关、信息可辨认且无明显矛盾 → passed；"
    "内容缺失、与职责无关、关键信息不可辨认或存在明显疑点 → rejected。"
)

# 规则兑底提取（LLM 不可用/未返回 fields 时）：正则命中 = 高置信（0.95）
_FIELD_PATTERNS: tuple[tuple[str, str], ...] = (
    ("统一社会信用代码", r"[0-9A-HJ-NPQRTUWXY]{2}\d{6}[0-9A-HJ-NPQRTUWXY]{10}"),
    ("身份证号", r"\d{17}[\dXx]"),
    ("手机号", r"1[3-9]\d{9}"),
    ("日期", r"\d{4}\s*[\-/年]\s*\d{1,2}\s*[\-/月]\s*\d{1,2}\s*日?"),
    ("金额", r"[\d,]+(?:\.\d+)?\s*(?:万元|亿元|元)"),
)


def _extract_fields_regex(text: str, limit: int = 12) -> list[dict]:
    """正则提取关键要素（纠错成本陷阱的兑底：LLM 抽风时员工仍能看到结构化要素）。"""
    import re

    out: list[dict] = []
    for field, pattern in _FIELD_PATTERNS:
        seen: set[str] = set()
        for m in re.finditer(pattern, text):
            value = m.group(0).strip()
            if value in seen:
                continue
            seen.add(value)
            out.append({"field": field, "value": value, "confidence": 0.95})
            if len(out) >= limit:
                return out
    return out


def _build_review_messages(
    member: dict[str, Any] | None,
    file_name: str,
    material_text: str,
    audit_hint: str = "",
) -> list[dict[str, str]]:
    persona = (
        member.get("prompt") or f"你是{member.get('name', '审核专家')}，{member.get('role', '')}"
        if member
        else "你是银行运营审核专家。"
    )
    focus = member.get("focus_points") or [] if member else []
    sys_parts = [
        persona,
        "当前场景：客户经理正在办理业务，向你上传了一份待验收材料，请你按自己的职责审核验收。",
    ]
    if focus:
        sys_parts.append("你的关注点：\n" + "\n".join(f"- {x}" for x in focus))
    return [
        {"role": "system", "content": "\n\n".join(sys_parts)},
        {
            "role": "user",
            "content": (
                f"材料文件名：{file_name}\n\n材料内容：\n{material_text or '（无可提取文本）'}\n\n"
                + (f"{audit_hint}\n\n" if audit_hint else "")
                + _REVIEW_OUTPUT_RULE
            ),
        },
    ]


def _format_audit_hint(report: dict[str, Any]) -> str:
    """系统合规审核结论 → 专家审核提示词参考（无风险返回空串）。"""
    findings = report.get("findings") or []
    if not isinstance(findings, list) or not findings:
        return ""
    lines = [
        f"【系统合规审核参考】总体风险：{report.get('overall_risk_level') or 'low'}；"
        f"发现 {len(findings)} 个风险点："
    ]
    for f in findings[:8]:
        if not isinstance(f, dict):
            continue
        lines.append(
            f"- [{f.get('risk_level', '')}][{f.get('risk_type', '')}] "
            f"{f.get('title', '')}：{str(f.get('description', ''))[:120]}"
        )
    lines.append("请结合你的职责与上述风险判断验收结论；若风险影响交付，请打回并写明补正要求。")
    return "\n".join(lines)


def _ops_audit_llm() -> LLMFunc:
    """系统合规审核的 LLM：复用 ops 本地优先三级降级链（材料含 PII，本地可用绝不出域）。"""
    summarize = _make_summarize_llm()

    async def _call(_kind: Any, prompt: str) -> str:
        return await summarize([{"role": "user", "content": prompt}])

    return _call


async def _run_doc_audit_safe(file_name: str, text: str, *, basis: str) -> dict[str, Any]:
    """专家审核前的系统合规审核（best-effort）：超时/异常都给 degraded 报告，不弹窗。"""
    try:
        return await asyncio.wait_for(
            run_doc_audit(file_name, text, basis=basis, llm=_ops_audit_llm()),
            timeout=float(settings.ops_doc_audit_timeout_sec),
        )
    except Exception as exc:
        logger.warning("[ops-case] system doc audit unavailable: %s", exc)
        return {
            "audited": False,
            "degraded": True,
            "error": f"系统合规审核不可用：{exc}",
            "basis": basis,
            "doc_category": "",
            "risk_types": [],
            "overall_risk_level": "",
            "findings": [],
            "elapsed_ms": 0,
            "audited_at": int(time.time()),
        }


def _parse_review_result(
    text: str, source_text: str
) -> tuple[str, str, list[dict], list[str], list[dict]]:
    """解析 LLM 验收结果 → (status, note, fields, evidence, reject_marks)。

    - JSON 解析失败 → 保守打回 + 正则兑底要素；
    - LLM fields 与正则 fields 去重合并（LLM 优先）；
    - evidence 只保留真实存在于原文的摘录（容忍空白差异，防幻觉证据链）；
    - reject_marks 同样白名单校验（quote 必须在原文中），模型未返回时宽容置空（BUGFIX #80）。
    """
    fields = _extract_fields_regex(source_text)
    evidence: list[str] = []
    marks: list[dict] = []
    t = (text or "").strip()
    if t.startswith("```"):
        t = t.strip("`")
        if t.startswith("json"):
            t = t[4:]
    try:
        data = json.loads(t)
        if isinstance(data, dict):
            verdict = str(data.get("verdict", "")).lower()
            note = str(data.get("note", "")).strip()
            llm_fields = data.get("fields")
            if isinstance(llm_fields, list) and llm_fields:
                merged: list[dict] = []
                seen_keys: set[str] = set()
                for f in llm_fields:
                    if not isinstance(f, dict):
                        continue
                    fname = str(f.get("field", "")).strip()
                    fvalue = str(f.get("value", "")).strip()
                    if not fname or not fvalue:
                        continue
                    key = f"{fname}::{fvalue}"
                    if key in seen_keys:
                        continue
                    seen_keys.add(key)
                    try:
                        conf = float(f.get("confidence", 0.8))
                    except (TypeError, ValueError):
                        conf = 0.8
                    merged.append(
                        {"field": fname, "value": fvalue, "confidence": max(0.0, min(1.0, conf))}
                    )
                for rf in fields:
                    if f"{rf['field']}::{rf['value']}" not in seen_keys:
                        merged.append(rf)
                fields = merged[:15]
            ev = data.get("evidence")
            if isinstance(ev, list) and source_text:
                norm_source = "".join(source_text.split())
                for e in ev[:3]:
                    s = str(e).strip()
                    if s and "".join(s.split()) in norm_source:
                        evidence.append(s[:120])
            # 打回定位（BUGFIX #80）：quote 必须在原文中真实存在，否则丢弃（防幻觉标注）
            spans = data.get("reject_spans")
            if isinstance(spans, list) and source_text:
                norm_src = "".join(source_text.split())
                for sp in spans[:5]:
                    if not isinstance(sp, dict):
                        continue
                    quote = str(sp.get("quote", "") or "").strip()
                    advice = str(sp.get("advice", "") or "").strip()
                    if not quote or "".join(quote.split()) not in norm_src:
                        continue
                    marks.append({"quote": quote[:120], "advice": advice[:200]})
            if verdict in ("passed", "pass", "ok", "通过"):
                return FILE_PASSED, note or "AI 审核通过", fields, evidence, []
            if verdict in ("rejected", "reject", "fail", "打回"):
                return FILE_REJECTED, note or "AI 审核未通过", fields, evidence, marks
    except (ValueError, TypeError):
        pass
    return (
        FILE_REJECTED,
        f"AI 审核结论无法解析，已保守打回。原文：{t[:300]}",
        fields,
        evidence,
        [],
    )


def _normalize_file_row(row: dict | None) -> dict:
    """存储行 → API 响应：JSON 字段从文本解析为数组/对象（含系统审核报告）。

    ocr_meta 只回轻摘要（块坐标体积不可控），面板选中影像时单独拉。
    """
    if row is None:
        return {}
    out = dict(row)
    for key, default in (
        ("extracted_fields", []),
        ("evidence", []),
        ("reject_marks", []),
        ("risk_report", {}),
    ):
        val = out.get(key)
        if isinstance(val, str):
            try:
                out[key] = json.loads(val or ("[]" if isinstance(default, list) else "{}"))
            except (ValueError, TypeError):
                out[key] = default
        elif val is None:
            out[key] = default
    meta = _load_ocr_meta(out.get("ocr_meta"))
    out["credential"] = str(out.get("credential", "") or "")
    out["classified_by"] = str(out.get("classified_by", "") or "")
    out["ocr"] = _asset_ocr_brief(meta)
    out.pop("ocr_meta", None)
    return out


@router.get("/case")
async def get_case(
    project_name: str = Query(""),
    feature_id: str = Query(""),
    case_id: str = Query(""),
) -> dict:
    cid = case_id or make_case_id(project_name, feature_id)
    store = _get_case_storage()
    _reap_orphaned_reviews(store)
    task = _get_task_storage().get_by_case(cid)
    return {
        "case_id": cid,
        "files": [_normalize_file_row(f) for f in store.list_files(cid)],
        "qa": store.list_qa(cid),
        "drafts": [_normalize_draft_row(d) for d in store.list_drafts(cid)],
        # 业务台账（业务汇总，2026-11-09）：当前业务的办理状态（挂起 → 前端冻结界面）
        "task": task.to_dict() if task else None,
    }


def _reap_orphaned_reviews(store: CaseStorage) -> int:
    """自愈：把僵尸 reviewing 材料落回 pending（BUGFIX #202）。

    调用时机：读取 Case 前（用户打开面板/轮询）与 Agent 启动时。
    正在本进程真实审核中的文件（_INFLIGHT_REVIEWS）不算僵尸。
    """
    try:
        healed = store.reap_orphaned_reviews(
            inflight=_INFLIGHT_REVIEWS,
            reason="上次审核被中断（Agent 重启或连接断开），可点「重审」重新审核",
        )
    except Exception as e:
        logger.warning("[ops-case] reap orphaned reviews failed: %s", e)
        return 0
    if healed:
        logger.info("[ops-case] %d orphaned reviewing material(s) reset to pending", healed)
    return healed


@router.delete("/case")
async def clear_case(
    project_name: str = Query(""),
    feature_id: str = Query(""),
    case_id: str = Query(""),
) -> dict:
    """清空 Case（BUGFIX #85）：重新开始办理 —— 材料/问答/草稿/落盘文件全部清除。"""
    cid = case_id or make_case_id(project_name, feature_id)
    store = _get_case_storage()
    counts = store.clear_case(cid)
    await _audit("ops_case_clear", {"case_id": cid, **counts})
    return {"ok": True, "case_id": cid, **counts}


@router.post("/case/files")
async def case_add_file(req: CaseFileAddRequest) -> dict:
    store = _get_case_storage()
    try:
        path = store.save_upload(req.case_id, req.file_name, req.content_base64)
    except Exception as e:
        raise HTTPException(400, f"文件保存失败: {e}") from e
    row = store.add_file(
        case_id=req.case_id,
        team_id=req.team_id,
        member_key=req.member_key,
        file_name=req.file_name,
        file_path=str(path),
    )
    await _audit(
        "ops_case_file_add",
        {
            "case_id": req.case_id,
            "file_id": row["id"],
            "member_key": req.member_key,
            "file_name": req.file_name,
        },
    )
    # 业务「发起即建档」：首次上传材料时台账落一行（前端会先带分类标签建档，
    # 这里兵底兼防：万一前端调用失败，也保证业务进了汇总）
    _ensure_task_for_case(req.case_id)
    _sync_task_status(req.case_id, store=store)
    # 凭证预览（2026-11-09）：后台补跑「影像识别 → 关键词归类」，
    # 上传主链路不等 OCR（RapidOCR 冷启动可遮十几秒）。
    _schedule_asset_pipeline(store, str(row["id"]), ASSET_FILE, str(path))
    return _normalize_file_row(store.get_file(str(row["id"])))


async def _do_review_file(store: CaseStorage, file_id: str) -> dict:
    """AI 专家审核单个材料（端点与草稿提交共用，BUGFIX #78）。

    2026-11-05：验收前先过一遍「系统合规审核」（与审核专家·文档审核同一套
    classify + 分块分析 + 证据定位）；有风险点随材料落库 → 前端弹审核报告，
    无风险不弹窗。合规审核失败/超时只降级，不阻断专家验收。

    BUGFIX #202：本函数是「写状态 → 调长耗时外部 LLM」的形态，异常/超时/取消
    都可能让材料永久停在 reviewing（界面一直转圈、无自愈入口）。因此：
      1. 整体包一层墙钟上限（settings.ops_review_timeout_sec）；
      2. 任何异常路径都把状态收敛到 pending / rejected 等终态，绝不停在 reviewing。
    """
    row = store.get_file(file_id)
    if row is None:
        raise HTTPException(404, f"case file {file_id} not found")

    member = _resolve_member(str(row["team_id"]), str(row["member_key"]))
    store.update_file(file_id, status=FILE_REVIEWING)
    _INFLIGHT_REVIEWS.add(file_id)
    try:
        return await asyncio.wait_for(
            _run_review_pipeline(store, file_id, row, member),
            timeout=float(settings.ops_review_timeout_sec),
        )
    except asyncio.TimeoutError:
        # 墙钟超时：降级为待审核（带原因），前端不再无限转圈，可点「重审」再来
        logger.warning("[ops-case] review timeout for %s", file_id)
        updated = store.update_file(
            file_id,
            status=FILE_PENDING,
            review_note=(
                f"AI 审核超时（超过 {int(settings.ops_review_timeout_sec)}s 未完成），"
                "可点「重审」重新审核"
            ),
            reviewed_by="",
        )
        await _audit(
            "ops_case_file_review",
            {"file_id": file_id, "verdict": FILE_PENDING, "mode": "timeout"},
        )
        return _normalize_file_row(updated)
    except asyncio.CancelledError:
        # 客户端断开 / Agent 重启：同样要收敛状态，不能留在 reviewing
        store.update_file(
            file_id,
            status=FILE_PENDING,
            review_note="审核被中断（Agent 重启或连接断开），可点「重审」重新审核",
            reviewed_by="",
        )
        raise
    except HTTPException:
        # 已是终态语义（如 502 LLM 不可用 → 内层已置 pending），直接抛出
        raise
    except Exception as e:
        logger.warning("[ops-case] review failed for %s: %s", file_id, e)
        store.update_file(
            file_id,
            status=FILE_PENDING,
            review_note=f"AI 审核异常中断：{e}；可点「重审」重新审核",
            reviewed_by="",
        )
        raise
    finally:
        _INFLIGHT_REVIEWS.discard(file_id)
        # 审核结果落地后同步业务台账状态（被打回 / 审核异常 → 人工确认）
        try:
            _sync_task_status(str(row.get("case_id", "")), store=store)
        except Exception as e:  # pragma: no cover - 台账同步失败不阻断审核主链路
            logger.warning("[ops-task] sync after review failed: %s", e)


async def _run_review_pipeline(
    store: CaseStorage,
    file_id: str,
    row: dict,
    member: dict | None,
) -> dict:
    """审核主流程（已被 _do_review_file 加上墙钟上限与异常收敛）。"""
    # 图片类：优先本地 vision；不可用 → 降级为关注点核对清单 + 待人工确认
    source_text = ""
    basis = "document"
    if is_image_file(str(row["file_name"])):
        basis = "vision"
        description = await _vision_describe(str(row["file_path"]))
        if description.strip():
            source_text = description
        else:
            focus = (member or {}).get("focus_points") or []
            note = "本地视觉模型不可用，无法自动识别图片内容。请人工按以下要点核对后改判：\n" + (
                "\n".join(f"- {x}" for x in focus) if focus else "- 材料真实、清晰、与业务相关"
            )
            # 低置信占位要素：前端标红提醒「未能自动提取，需人工核对」（纠错成本陷阱的诚实表达）
            placeholder_fields = [
                {
                    "field": "图片内容",
                    "value": "本地视觉模型不可用，未能自动提取关键要素",
                    "confidence": 0.1,
                }
            ]
            risk_report = await _run_doc_audit_safe(str(row["file_name"]), "", basis="vision")
            updated = store.update_file(
                file_id,
                status=FILE_PENDING,
                review_note=note,
                reviewed_by="ai",
                extracted_fields=dump_json(placeholder_fields),
                evidence="[]",
                risk_report=dump_json(risk_report),
            )
            await _audit(
                "ops_case_file_review",
                {"file_id": file_id, "verdict": FILE_PENDING, "mode": "vision_fallback"},
            )
            return _normalize_file_row(updated)
    else:
        source_text = _extract_material_text(str(row["file_path"]), str(row["file_name"]))

    # ① 系统合规审核先行（有风险点 → 前端弹审核报告；无风险不弹）
    risk_report = await _run_doc_audit_safe(str(row["file_name"]), source_text, basis=basis)
    audit_findings = risk_report.get("findings") or []
    audit_count = len(audit_findings) if isinstance(audit_findings, list) else 0

    # ② 专家按人设验收（合规风险作为参考注入，结论仍由专家职责决定）
    messages = _build_review_messages(
        member,
        str(row["file_name"]),
        source_text,
        _format_audit_hint(risk_report),
    )
    llm_call = _make_summarize_llm()
    try:
        raw = await llm_call(messages)
    except RuntimeError as e:
        # 三级 LLM 均不可用 → 回退待审核状态，但正则要素照样给（员工不必白等）
        store.update_file(
            file_id,
            status=FILE_PENDING,
            review_note=f"AI 审核不可用：{e}",
            reviewed_by="",
            extracted_fields=dump_json(_extract_fields_regex(source_text)),
            risk_report=dump_json(risk_report),
        )
        raise HTTPException(502, str(e)) from e

    status, note, fields, evidence, marks = _parse_review_result(raw, source_text)
    updated = store.update_file(
        file_id,
        status=status,
        review_note=note,
        reviewed_by="ai",
        extracted_fields=dump_json(fields),
        evidence=dump_json(evidence),
        # 通过时清空旧打回定位；打回时写入新定位（BUGFIX #80）
        reject_marks=dump_json(marks),
        risk_report=dump_json(risk_report),
    )
    await _audit(
        "ops_case_file_review",
        {
            "file_id": file_id,
            "member_key": row["member_key"],
            "verdict": status,
            "audit_findings": audit_count,
        },
    )
    return _normalize_file_row(updated)


@router.post("/case/files/{file_id}/review")
async def case_review_file(file_id: str) -> dict:
    return await _do_review_file(_get_case_storage(), file_id)


@router.get("/case/files/{file_id}/content")
async def case_file_content(file_id: str) -> dict:
    """交付物柜预览（BUGFIX #79）：返回材料文件 base64 内容。"""
    import base64

    store = _get_case_storage()
    row = store.get_file(file_id)
    if row is None:
        raise HTTPException(404, f"case file {file_id} not found")
    path = Path(str(row.get("file_path", "")))
    if not path.is_file():
        raise HTTPException(404, "文件已不存在（可能被清理）")
    raw = path.read_bytes()
    return {
        "file_name": str(row.get("file_name", "")),
        "content_base64": base64.b64encode(raw).decode("ascii"),
    }


class CaseFileSaveAsRequest(BaseModel):
    target_path: str = Field(min_length=1, max_length=4096)


@router.post("/case/files/{file_id}/save-as")
async def case_file_save_as(file_id: str, req: CaseFileSaveAsRequest) -> dict:
    """交付物柜另存（BUGFIX #79）：后端直接复制文件，避免大体积 base64 过桥。"""
    import shutil

    store = _get_case_storage()
    row = store.get_file(file_id)
    if row is None:
        raise HTTPException(404, f"case file {file_id} not found")
    src = Path(str(row.get("file_path", "")))
    if not src.is_file():
        raise HTTPException(404, "文件已不存在（可能被清理）")
    target = Path(req.target_path)
    if not _path_parent_exists(target):
        raise HTTPException(400, f"保存目录不存在: {target.parent}")
    shutil.copyfile(src, target)
    await _audit(
        "ops_case_file_save_as",
        {"file_id": file_id, "target_path": str(target)},
    )
    return {"ok": True, "path": str(target)}


@router.post("/case/files/{file_id}/override")
async def case_override_file(file_id: str, req: CaseFileOverrideRequest) -> dict:
    if req.status not in ALL_FILE_STATUSES:
        raise HTTPException(400, f"invalid status: {req.status}")
    store = _get_case_storage()
    before = store.get_file(file_id)
    if before is None:
        raise HTTPException(404, f"case file {file_id} not found")
    updated = store.update_file(
        file_id,
        status=req.status,
        review_note=req.note,
        reviewed_by="human",
    )
    # 纠错闭环（铁律 2，2026-08-10）：AI 结论被人工改判 → 落纠错样本，
    # 让 AI 越用越聪明，而不是永远犯同样的错
    if str(before.get("reviewed_by", "")) == "ai" and str(before.get("status", "")) != req.status:
        store.add_correction(
            case_id=str(before.get("case_id", "")),
            file_id=file_id,
            member_key=str(before.get("member_key", "")),
            file_name=str(before.get("file_name", "")),
            ai_status=str(before.get("status", "")),
            ai_note=str(before.get("review_note", "")),
            human_status=req.status,
            human_note=req.note,
        )
        await _audit(
            "ops_case_correction",
            {
                "file_id": file_id,
                "ai_status": str(before.get("status", "")),
                "human_status": req.status,
            },
        )
    await _audit(
        "ops_case_file_override",
        {"file_id": file_id, "status": req.status, "note": req.note},
    )
    # 人工改判后重算台账状态（待处理项清空 → 回到「正在办理」）
    try:
        _sync_task_status(str(before.get("case_id", "")), store=store)
    except Exception as e:  # pragma: no cover - 台账同步失败不阻断改判主链路
        logger.warning("[ops-task] sync after override failed: %s", e)
    return _normalize_file_row(updated)


@router.get("/case/corrections")
async def case_list_corrections(case_id: str = Query("")) -> dict:
    """人工纠错样本列表（供后续提示词/模型改进分析，不指定 case_id 返全部）。"""
    store = _get_case_storage()
    rows = store.list_corrections(case_id or None)
    return {"corrections": rows, "total": len(rows)}


@router.delete("/case/files/{file_id}")
async def case_delete_file(file_id: str) -> dict:
    store = _get_case_storage()
    row = store.delete_file(file_id)
    if row is None:
        raise HTTPException(404, f"case file {file_id} not found")
    await _audit(
        "ops_case_file_delete",
        {"file_id": file_id, "file_name": row.get("file_name", "")},
    )
    return {"ok": True, "file_id": file_id}


async def _search_local_knowledge(question: str, limit: int = 3) -> list[dict]:
    """制度问答检索（找制度变问助手）：走**用户上传的本地 RAG 知识库**混合检索。

    2026-09-04：内置 knowledge-base/ 关键词检索下线，统一改走上传 RAG 库
    （FTS5 BM25 + 向量 + RRF + reranker），与文档审核依据同源。命中返回
    [{title, source, snippet}]（title/source=上传文档标题，snippet=命中片段），
    未启用 RAG / 库空 / 检索异常 → 空列表（best-effort 降级，不阻塞问答）。
    """
    if not question.strip() or not settings.rag_enabled:
        return []
    try:
        from agent.knowledge.retriever import get_default_retriever

        ctx = await get_default_retriever().retrieve(question, top_k=limit)
    except Exception:
        logger.debug("ops case_ask 制度问答 RAG 检索失败", exc_info=True)
        return []
    out: list[dict] = []
    for r in ctx.results:
        meta = getattr(r.chunk, "metadata", {}) or {}
        title = str(r.doc_title or r.citation or "")
        snippet = str(meta.get("child_content") or r.chunk.content or "")[:220]
        out.append({"title": title, "source": title, "snippet": snippet})
    return out


def _build_ask_messages(
    member: dict[str, Any] | None,
    question: str,
    materials: list[dict],
    kb_results: list[dict],
) -> list[dict[str, str]]:
    persona = (
        member.get("prompt") or f"你是{member.get('name', '业务专家')}，{member.get('role', '')}"
        if member
        else "你是银行运营业务专家。"
    )
    sys_parts = [
        persona,
        "当前场景：客户经理在办理业务过程中向你请教，请用中文简洁、专业地回答；"
        "无法确定时明确说明，不要编造。",
    ]
    if materials:
        names = "、".join(str(f.get("file_name", "")) for f in materials[:10])
        sys_parts.append(f"已向你提交的材料：{names}")
    if kb_results:
        kb_lines = ["知识库检索结果（回答必须优先依据这些制度内容，并注明出处）："]
        for r in kb_results:
            kb_lines.append(f"《{r['title']}》：{r['snippet']}")
        sys_parts.append("\n".join(kb_lines))
    return [
        {"role": "system", "content": "\n\n".join(sys_parts)},
        {"role": "user", "content": question},
    ]


def _sessions_store():
    """会话存储（测试可 monkeypatch，避免碰真实 %APPDATA% sessions.db）。"""
    from agent.sessions.storage import SessionStorage

    return SessionStorage()


def _archive_qa_best_effort(
    store: CaseStorage, case_id: str, member_key: str, question: str, answer: str
) -> None:
    """会话管理归档（2026-08-11）：专家团对话也进 sessions，不再「永远只有 1 个」。

    best-effort：归档失败只记日志，绝不阻塞问答主链路。
    """
    try:
        sstore = _sessions_store()
        sid = store.get_case_session(case_id)
        if sid is None or sstore.get_session(sid) is None:
            sess = sstore.create_session(
                title=f"专家问答 · {case_id}",
                metadata={"source": "ops_case", "case_id": case_id},
            )
            sid = sess.id
            store.set_case_session(case_id, sid)
        sstore.append_message(sid, "user", question, metadata={"member_key": member_key})
        sstore.append_message(sid, "assistant", answer, metadata={"member_key": member_key})
    except Exception as e:
        logger.warning("[ops-case] sessions archive failed: %s", e)


@router.post("/case/ask")
async def case_ask(req: CaseAskRequest) -> dict:
    store = _get_case_storage()
    member = _resolve_member(req.team_id, req.member_key)
    materials = [f for f in store.list_files(req.case_id) if f.get("member_key") == req.member_key]
    # 模板/清单类请求（BUGFIX #78）：不在气泡里砸一大段文字，
    # 而是生成可界面直填的结构化草稿；生成失败自动降级普通问答
    if _is_template_request(req.question):
        draft = await _try_create_draft(store, req, member, materials)
        if draft is not None:
            answer = (
                f"已为你生成《{draft['title']}》填写表单："
                "在下方「交付草稿」区直接填写，提交后我来审核验收，通过即入交付物。"
            )
            qa = store.add_qa(
                case_id=req.case_id,
                member_key=req.member_key,
                question=req.question,
                answer=answer,
            )
            _archive_qa_best_effort(store, req.case_id, req.member_key, req.question, answer)
            await _audit(
                "ops_case_ask",
                {
                    "case_id": req.case_id,
                    "member_key": req.member_key,
                    "question": req.question[:200],
                    "draft_id": draft["id"],
                },
            )
            return {"qa": qa, "draft": _normalize_draft_row(draft)}
    # 找制度变问助手：先查上传的本地 RAG 知识库，命中则回答附制度出处（防黑盒）
    kb_results = await _search_local_knowledge(req.question)
    messages = _build_ask_messages(member, req.question, materials, kb_results)
    llm_call = _make_summarize_llm()
    try:
        answer = await llm_call(messages)
    except RuntimeError as e:
        raise HTTPException(502, str(e)) from e
    answer_text = answer.strip()
    if kb_results:
        sources = "\n".join(f"{i + 1}. 《{r['title']}》" for i, r in enumerate(kb_results))
        answer_text += f"\n\n---\n📚 制度出处：\n{sources}"
    # 清单形态长文回答 → 自动转可直填草稿（BUGFIX #82）：问题没带「模板/清单」
    # 关键词（如「需要什么材料」）也能出表单，不在气泡里砸一大段文字；
    # 转换失败保留原文不断链
    draft: dict | None = None
    if _looks_like_checklist(answer_text):
        draft = await _try_convert_answer_to_draft(store, req, materials, answer_text)
        if draft is not None:
            answer_text = (
                f"已把资料清单生成为《{draft['title']}》草稿表单："
                "在下方「交付草稿」区直接填写，提交后我来审核验收，通过即入交付物。"
            )
            if kb_results:
                # 转草稿后仍保留制度出处（防黑盒）
                sources = "\n".join(f"{i + 1}. 《{r['title']}》" for i, r in enumerate(kb_results))
                answer_text += f"\n\n---\n📚 制度出处：\n{sources}"
    qa = store.add_qa(
        case_id=req.case_id,
        member_key=req.member_key,
        question=req.question,
        answer=answer_text,
    )
    _archive_qa_best_effort(store, req.case_id, req.member_key, req.question, answer_text)
    await _audit(
        "ops_case_ask",
        {"case_id": req.case_id, "member_key": req.member_key, "question": req.question[:200]},
    )
    if draft is not None:
        return {"qa": qa, "draft": _normalize_draft_row(draft)}
    return {"qa": qa}


# ---------------------------------------------------------------------------
# 交付草稿：要「模板/清单」时不再输出一大段文字，而是生成可界面直填的
# 结构化表单；提交后自动成为材料走专家审核，通过即并入交付物（BUGFIX #78）。
# ---------------------------------------------------------------------------

_TEMPLATE_KEYWORDS = ("模板", "清单", "表格", "样表", "样例", "范本", "空白表")

_ALLOWED_FIELD_TYPES = ("text", "textarea", "select", "date", "file")


def _is_template_request(question: str) -> bool:
    return any(kw in question for kw in _TEMPLATE_KEYWORDS)


def _build_draft_template_messages(
    member: dict[str, Any] | None,
    question: str,
    materials: list[dict],
) -> list[dict[str, str]]:
    persona = (
        member.get("prompt") or f"你是{member.get('name', '业务专家')}，{member.get('role', '')}"
        if member
        else "你是银行运营业务专家。"
    )
    sys_parts = [
        persona,
        "当前场景：客户经理需要一份可在界面直接填写的交付草稿表单（模板/清单）。"
        "请只返回 JSON，不要输出 JSON 以外的任何内容：\n"
        '{"title": "表单标题", "fields": ['
        '{"name": "小写英文字段名", "label": "中文标签", "type": "text|textarea|select|date|file", '
        '"options": ["仅 type=select 时给出"], "hint": "填写提示（可空）", "required": true}]}\n'
        "要求：字段 5-15 个（优先精简到 8 个以内，只留客户必须提供的），覆盖用户请求的清单要点；需要客户提供的信息与银行内部核查信息分开设字段；"
        "需要的是证件/文件/扫描件/照片（如身份证、营业执照、合同、报表文件）时必须用 type=file；"
        "不确定具体值时不要编造，留空由用户填写。",
    ]
    if materials:
        names = "、".join(str(f.get("file_name", "")) for f in materials[:10])
        sys_parts.append(f"已向你提交的材料（可据此推断字段）：{names}")
    return [
        {"role": "system", "content": "\n\n".join(sys_parts)},
        {"role": "user", "content": question},
    ]


def _parse_draft_template(raw: str) -> tuple[str, list[dict]] | None:
    """解析 LLM 返回的表单 JSON；不合法/无有效字段返 None（调用方降级普通问答）。"""
    from agent.llm.json_discipline import extract_json

    data = extract_json(raw)
    if not isinstance(data, dict):
        return None
    title = str(data.get("title", "") or "").strip()
    raw_fields = data.get("fields")
    if not isinstance(raw_fields, list):
        return None
    fields: list[dict] = []
    seen: set[str] = set()
    for f in raw_fields:
        if not isinstance(f, dict):
            continue
        name = str(f.get("name", "") or "").strip()
        label = str(f.get("label", "") or "").strip()
        if not name or not label or name in seen:
            continue
        seen.add(name)
        ftype = str(f.get("type", "text") or "text").strip()
        if ftype not in _ALLOWED_FIELD_TYPES:
            ftype = "textarea" if ftype in ("longtext", "multiline") else "text"
        options = [str(x) for x in (f.get("options") or []) if str(x).strip()][:20]
        fields.append(
            {
                "name": name,
                "label": label,
                "type": "select" if ftype == "select" and options else ftype,
                "options": options,
                "hint": str(f.get("hint", "") or "")[:200],
                "required": bool(f.get("required", False)),
            }
        )
    if not fields:
        return None
    return (title or "交付草稿", fields[:20])


async def _try_create_draft(
    store: CaseStorage,
    req: CaseAskRequest,
    member: dict[str, Any] | None,
    materials: list[dict],
) -> dict | None:
    """模板请求 → LLM 产出结构化表单并存草稿；任何失败返 None（绝不阻塞问答主链路）。"""
    messages = _build_draft_template_messages(member, req.question, materials)
    try:
        llm_call = _make_summarize_llm()
        raw = await llm_call(messages)
    except Exception as e:
        logger.warning("[ops-case] draft template LLM unavailable: %s", e)
        return None
    parsed = _parse_draft_template(str(raw or ""))
    if parsed is None:
        logger.warning("[ops-case] draft template unparsable, fallback to plain ask")
        return None
    title, fields = parsed
    # 已上传材料的提取要素自动预填（BUGFIX #79：少打字，用户只核不改）
    values = _prefill_draft_values(materials, fields)
    try:
        return store.add_draft(
            case_id=req.case_id,
            team_id=req.team_id,
            member_key=req.member_key,
            title=title,
            template_json=dump_json(fields),
            values_json=dump_json(values),
        )
    except Exception as e:
        logger.warning("[ops-case] draft save failed: %s", e)
        return None


def _looks_like_checklist(text: str) -> bool:
    """回答是否为清单形态（≥3 条列表项且有一定长度）：草稿转换触发条件。"""
    import re

    items = re.findall(r"^\s*(?:[-*•]|\d+[.、)])\s+\S", text, flags=re.M)
    return len(items) >= 3 and len(text) >= 100


async def _try_convert_answer_to_draft(
    store: CaseStorage,
    req: CaseAskRequest,
    materials: list[dict],
    answer_text: str,
) -> dict | None:
    """长文清单回答 → 结构化表单草稿（BUGFIX #82）；任何失败返 None（保留原文）。"""
    messages = [
        {
            "role": "system",
            "content": (
                "请把下面的专家回答转换成可在界面直接填写的表单。只返回 JSON（不要代码块）："
                '{"title": "表单标题", "fields": [{"name": "小写英文字段名", "label": "中文标签", '
                '"type": "text|textarea|select|date|file", "options": ["仅 select 需要"], '
                '"hint": "填写提示（可空）", "required": true}]}。'
                "要求：把回答中需要客户提供的每项材料/信息转成一个字段，字段数控制在 8 个以内（抓重点，不要穷举）；"
                "需要客户填写的才建字段，纯说明性内容不要建；"
                "需要的是证件/文件/扫描件/照片（如身份证、营业执照、合同、报表文件）时必须用 type=file；"
                "不要编造回答里没有的内容。"
            ),
        },
        # 输入截短至 1500 字（BUGFIX #88）：转换只需清单主体，长文尾部噪声去掉可显著降低生成耗时
        {"role": "user", "content": answer_text[:1500]},
    ]
    try:
        llm_call = _make_summarize_llm()
        raw = await llm_call(messages)
    except Exception as e:
        logger.warning("[ops-case] answer→draft LLM unavailable: %s", e)
        return None
    parsed = _parse_draft_template(str(raw or ""))
    if parsed is None:
        logger.warning("[ops-case] answer→draft unparsable, keep plain answer")
        return None
    title, fields = parsed
    values = _prefill_draft_values(materials, fields)
    try:
        return store.add_draft(
            case_id=req.case_id,
            team_id=req.team_id,
            member_key=req.member_key,
            title=title,
            template_json=dump_json(fields),
            values_json=dump_json(values),
        )
    except Exception as e:
        logger.warning("[ops-case] converted draft save failed: %s", e)
        return None


def _match_fields_to_values(fields: list[dict], candidates: dict[str, str]) -> dict[str, str]:
    """候选键值 → 模板字段（name/label 精确 > label 包含候选 > 候选包含 label）。

    file 类型字段跳过（文件无法由文本填入）。
    """
    values: dict[str, str] = {}
    for field in fields:
        if str(field.get("type", "")) == "file":
            continue
        name = str(field.get("name", ""))
        label = str(field.get("label", ""))
        best: tuple[int, str] | None = None
        for cname, cvalue in candidates.items():
            if not cname or not cvalue:
                continue
            if cname in (name, label):
                score = 3
            elif len(cname) >= 2 and cname in label:
                score = 2
            elif len(label) >= 2 and label in cname:
                score = 1
            else:
                continue
            if best is None or score > best[0]:
                best = (score, cvalue)
        if best is not None:
            values[name] = best[1]
    return values


def _prefill_draft_values(materials: list[dict], fields: list[dict]) -> dict[str, str]:
    """把材料已提取要素映射到草稿字段（按 name/label 命中，取高置信优先）。"""
    candidates: dict[str, tuple[float, str]] = {}
    for f in materials:
        raw_fields = f.get("extracted_fields")
        if isinstance(raw_fields, str):
            try:
                raw_fields = json.loads(raw_fields or "[]")
            except (ValueError, TypeError):
                raw_fields = []
        for ef in raw_fields or []:
            if not isinstance(ef, dict):
                continue
            fname = str(ef.get("field", "")).strip()
            fvalue = str(ef.get("value", "")).strip()
            if not fname or not fvalue:
                continue
            try:
                conf = float(ef.get("confidence", 0.5))
            except (TypeError, ValueError):
                conf = 0.5
            if conf < 0.5:  # 低置信要素不预填，避免错值带入（员工只核标红项的原则延续）
                continue
            prev = candidates.get(fname)
            if prev is None or conf > prev[0]:
                candidates[fname] = (conf, fvalue)
    return _match_fields_to_values(fields, {k: v for k, (_c, v) in candidates.items()})


def _normalize_draft_row(row: dict | None) -> dict:
    """存储行 → API 响应：template_json/values_json 解析为对象。"""
    if row is None:
        return {}
    out = dict(row)
    for key, default in (("template_json", "[]"), ("values_json", "{}")):
        val = out.get(key)
        try:
            out[key.replace("_json", "")] = json.loads(val if isinstance(val, str) else default)
        except (ValueError, TypeError):
            out[key.replace("_json", "")] = [] if default == "[]" else {}
    return out


def _render_draft_markdown(title: str, template: list[dict], values: dict) -> str:
    """草稿 → md 文本（提交后作为材料文件落盘，审核通过并入交付物 zip）。"""
    lines = [f"# {title}", "", "> 本文件由运营工作台交付草稿生成，经专家验收后作为交付物。", ""]
    for f in template:
        name = str(f.get("name", ""))
        label = str(f.get("label", name))
        value = str(values.get(name, "") or "").strip()
        required_mark = "（必填）" if f.get("required") else ""
        lines.append(f"- **{label}**{required_mark}：{value or '【未填写】'}")
    lines += ["", f"生成时间：{time.strftime('%Y-%m-%d %H:%M:%S')}"]
    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------------------
# OCR 自动填入（2026-11-05）：所有专家的录入表单通用。
# 上传文件 → 本地文字识别（图片 RapidOCR / 文档解析 + PDF 扫描件 OCR 回退）
# → 模型把文本映射到表单字段 → 前端自动填入（用户核对后提交）。
# 数据不出域：OCR 全程端侧；字段抽取走本地 Ollama 优先的三级降级链。
# ---------------------------------------------------------------------------

_DRAFT_OCR_MAX_BYTES = 30 * 1024 * 1024
_DRAFT_OCR_DOC_SUFFIXES = frozenset(
    {".pdf", ".doc", ".docx", ".txt", ".md", ".csv", ".html", ".htm", ".xlsx", ".pptx"}
)


async def _extract_upload_text(
    file_name: str, content_base64: str
) -> tuple[str, str, dict[str, Any]]:
    """上传文件 → (文本, 引擎, 影像元数据)。图片走端侧 RapidOCR（带块坐标），
    文档走 doc_review 解析（无坐标，ocr_meta 为空 dict）。

    识别不出文字时返回 ("", "none", {})，由端点统一报友好错误。
    """
    try:
        raw = base64.b64decode(content_base64, validate=False)
    except Exception as exc:
        raise HTTPException(400, "文件内容不是合法的 base64") from exc
    if not raw:
        raise HTTPException(400, "文件为空，请重新选择")
    if len(raw) > _DRAFT_OCR_MAX_BYTES:
        raise HTTPException(413, "文件过大（上限 30MB），请压缩后重试")

    suffix = Path(file_name).suffix.lower()
    if not (is_image_file(file_name) or suffix in _DRAFT_OCR_DOC_SUFFIXES):
        raise HTTPException(
            415, f"暂不支持 {suffix or '该'} 格式（支持图片 / PDF / Word / Excel / PPT / 文本）"
        )

    with tempfile.TemporaryDirectory(prefix="eaide-ocr-") as tmp:
        path = Path(tmp) / f"upload{suffix or '.png'}"
        path.write_bytes(raw)
        if is_image_file(file_name):
            from agent.image_processing.ocr import ocr_image_meta

            meta = await asyncio.to_thread(ocr_image_meta, str(path))
            text = str(meta.get("text", "") or "")
            if text.strip():
                return text.strip(), "rapidocr", meta
            # 端侧 OCR 不可用 / 识别为空 → 本地视觉模型描述兑底（数据仍不出域）
            vision = await _vision_describe(str(path))
            if vision.strip():
                return vision.strip(), "vision", {}
            return "", "none", {}
        text = await asyncio.to_thread(_extract_material_text, str(path), file_name)
        return (text.strip(), "document", {}) if text.strip() else ("", "none", {})


_LABEL_VALUE_RE = re.compile(r"^\s*([^\s：:]{2,16})\s*[：:]\s*(.+?)\s*$")


def _extract_label_values(text: str, limit: int = 40) -> dict[str, str]:
    """OCR 文本里「标签：值」形态的键值对（模型不可用时的填表兑底）。"""
    pairs: dict[str, str] = {}
    for line in text.splitlines():
        m = _LABEL_VALUE_RE.match(line)
        if not m:
            continue
        key = m.group(1).strip(" -*•\t")
        value = m.group(2).strip()
        if not key or not value or len(value) > 120:
            continue
        pairs.setdefault(key, value)
        if len(pairs) >= limit:
            break
    return pairs


def _normalize_fill_value(field: dict, value: str) -> str:
    """识别值落到字段：压空白、截长、select 对齐选项、date 归一到 YYYY-MM-DD。"""
    v = " ".join(str(value or "").split()).strip(" ：:;；,，。")
    if not v:
        return ""
    if len(v) > 1000:
        v = v[:1000]
    ftype = str(field.get("type", ""))
    if ftype == "select":
        options = [str(o) for o in (field.get("options") or [])]
        if v in options:
            return v
        for opt in options:
            if opt and (v in opt or opt in v):
                return opt
        return ""
    if ftype == "date":
        m = re.search(r"(\d{4})\D{0,3}(\d{1,2})\D{0,3}(\d{1,2})", v)
        if m:
            month, day = int(m.group(2)), int(m.group(3))
            if 1 <= month <= 12 and 1 <= day <= 31:
                return f"{m.group(1)}-{month:02d}-{day:02d}"
    return v


def _build_ocr_fill_messages(
    member: dict[str, Any] | None,
    template: list[dict],
    text: str,
) -> list[dict[str, str]]:
    persona = (
        member.get("prompt") or f"你是{member.get('name', '业务专家')}，{member.get('role', '')}"
        if member
        else "你是银行运营业务专家。"
    )
    field_lines: list[str] = []
    for f in template:
        name = str(f.get("name", ""))
        if not name or str(f.get("type", "")) == "file":
            continue
        desc = f"- {name}（{f.get('label', name)}"
        options = [str(o) for o in (f.get("options") or [])]
        if options:
            desc += "，可选值：" + " / ".join(options)
        field_lines.append(desc + "）")
    return [
        {
            "role": "system",
            "content": "\n".join(
                [
                    persona,
                    "当前场景：客户经理上传了一份材料，需要把材料里的信息自动填入交付表单。",
                    '请只返回 JSON（不要 Markdown 代码块），格式：{"values": {"字段name": "字段值"}}。',
                    "硬性要求：只填材料中明确出现的信息，严禁编造；材料里没有的字段直接省略；"
                    "日期统一 YYYY-MM-DD；金额/编号保留材料原文；select 字段取值必须来自可选值；"
                    "文件类字段不要填。",
                    "表单字段：\n" + "\n".join(field_lines),
                ]
            ),
        },
        {"role": "user", "content": f"材料文本：\n{text[:12000]}"},
    ]


def _parse_ocr_fill_values(raw: str, template: list[dict]) -> dict[str, str]:
    """解析模型抽取结果（兼容包一层 values 的写法）；只保留模板内字段。"""
    from agent.llm.json_discipline import extract_json

    data = extract_json(raw)
    if isinstance(data, dict) and isinstance(data.get("values"), dict):
        data = data["values"]
    if not isinstance(data, dict):
        return {}
    allowed = {str(f.get("name", "")) for f in template}
    out: dict[str, str] = {}
    for k, v in data.items():
        name = str(k)
        if name not in allowed:
            continue
        val = str(v or "").strip()
        if val:
            out[name] = val
    return out


async def _ocr_fill_values(
    member: dict[str, Any] | None,
    template: list[dict],
    text: str,
) -> tuple[dict[str, str], str]:
    """文本 → 字段值：模型抽取优先，正则/标签键值兑底（模型不可用也不空手）。"""
    values: dict[str, str] = {}
    source = "regex"
    try:
        llm_call = _make_summarize_llm()
        raw = await llm_call(_build_ocr_fill_messages(member, template, text))
        parsed = _parse_ocr_fill_values(str(raw or ""), template)
        if parsed:
            values = parsed
            source = "llm"
    except Exception as exc:
        logger.warning("[ops-case] OCR fill LLM unavailable: %s", exc)

    candidates: dict[str, str] = {}
    for rf in _extract_fields_regex(text):
        candidates[str(rf.get("field", ""))] = str(rf.get("value", ""))
    # 标签键值对更精确，覆盖正则的同类候选
    candidates.update(_extract_label_values(text))
    for name, val in _match_fields_to_values(template, candidates).items():
        values.setdefault(name, val)

    normalized: dict[str, str] = {}
    for f in template:
        name = str(f.get("name", ""))
        raw_value = values.get(name)
        if not raw_value:
            continue
        clean = _normalize_fill_value(f, raw_value)
        if clean:
            normalized[name] = clean
    return normalized, source


@router.post("/case/drafts/{draft_id}/ocr-fill")
async def ocr_fill_case_draft(draft_id: str, req: DraftOcrFillRequest) -> dict:
    """上传文件 → 本地 OCR/文档解析 → 模型抽取字段 → 返回可合并的填写值。

    字段值不直接写库：前端合并到当前表单（已有值可撤销），用户核对后暂存/提交。
    但识别底图会落一张凭证影像（credential_images，不走审核链路）：
    没这张图，凭证预览面板就无法「点录入字段 → 放大高亮对应区域」。
    """
    store = _get_case_storage()
    row = store.get_draft(draft_id)
    if row is None:
        raise HTTPException(404, f"draft {draft_id} not found")
    if str(row.get("status", "")) == "passed":
        raise HTTPException(400, "草稿已验收通过，无需再识别填入")
    try:
        template = json.loads(str(row.get("template_json", "[]")))
    except (ValueError, TypeError):
        template = []
    if not isinstance(template, list) or not template:
        raise HTTPException(400, "草稿没有可填字段，请重新生成表单")

    text, engine, meta = await _extract_upload_text(req.file_name, req.content_base64)
    if not text:
        raise HTTPException(
            422,
            "没能从文件里识别出文字：请换一张清晰的图片/扫描件，或直接手动填写。"
            "（图片 OCR 依赖端侧模型，未安装时会退化为本地视觉描述）",
        )

    member = _resolve_member(str(row.get("team_id", "")), str(row.get("member_key", "")))
    values, source = await _ocr_fill_values(member, template, text)
    # 识别底图入库（失败不阻断填入主链路：填表比留档重要）
    image = await _register_ocr_source_image(store, draft_id, row, req, values, meta)
    await _audit(
        "ops_case_draft_ocr_fill",
        {
            "case_id": str(row.get("case_id", "")),
            "draft_id": draft_id,
            "file_name": req.file_name,
            "engine": engine,
            "source": source,
            "filled": sorted(values),
            "credential_image": image.get("id", "") if image else "",
        },
    )
    return {
        "values": values,
        "filled": sorted(values),
        "engine": engine,
        "source": source,
        "text_chars": len(text),
        "text_preview": text[:800],
        # 凭证预览用：这张底图的 id / 归属节点 / 能否做区域定位
        "credential_image": image,
    }


async def _register_ocr_source_image(
    store: CaseStorage,
    draft_id: str,
    draft: dict,
    req: DraftOcrFillRequest,
    values: dict[str, str],
    meta: dict[str, Any],
) -> dict[str, Any] | None:
    """把 OCR 用的原图登记为凭证底图，并顺手走一次关键词归类。

    任何 IO 失败只记日志返回 None（面板会显示“无底图”，不能拖死填表）。

    重发幂等（2026-11-09）：桌面端 ocr-fill 现在带响应体中断重试
    （commands/ops.rs::ops_case_draft_ocr_fill，最多 5 次），一次用户点击可能让
    本端点跑多遍。命中「同表单 + 同文件名」的既有底图时就地覆盖，不再插新行、
    不再另存文件副本 —— 否则会把凭证预览面板刷成一堆重复影像。
    """
    try:
        raw = base64.b64decode(req.content_base64, validate=False)
        case_id = str(draft.get("case_id", "") or "")
        team_id = str(draft.get("team_id", "") or "")
        member_key = str(draft.get("member_key", "") or "")
        prefix = "ocr-img" if is_image_file(req.file_name) else "ocr-doc"
        nodes = _credential_nodes_of(team_id).get(member_key) or []
        credential = _match_credential(nodes, req.file_name, str(meta.get("text", "") or ""))

        existing = store.find_image_by_draft_file(draft_id, req.file_name)
        if existing is not None:
            path = Path(str(existing.get("file_path", "") or ""))
            # 落盘目录已不在（人工删过 / 换机）→ 当新图重存，别留一个指向空路径的行
            if not path.parent.is_dir():
                existing = None

        if existing is None:
            path = store.save_named_upload(case_id, prefix, req.file_name, raw)
            row = store.add_image(
                case_id=case_id,
                team_id=team_id,
                member_key=member_key,
                draft_id=draft_id,
                file_name=req.file_name,
                file_path=str(path),
                credential=credential,
                source="ocr-fill",
                values_json=dump_json(values),
                ocr_meta=dump_json(meta) if meta else "{}",
            )
        else:
            path.write_bytes(raw)
            row = (
                store.update_image(
                    str(existing["id"]),
                    credential=credential,
                    values_json=dump_json(values),
                    ocr_meta=dump_json(meta) if meta else "{}",
                    team_id=team_id,
                    member_key=member_key,
                )
                or existing
            )
        return _asset_brief(row, ASSET_IMAGE)
    except Exception as exc:
        logger.warning("[ops-credential] 识别底图入库失败 draft=%s: %s", draft_id, exc)
        return None


@router.get("/case/drafts")
async def list_case_drafts(case_id: str = Query(..., min_length=1)) -> dict:
    store = _get_case_storage()
    return {"drafts": [_normalize_draft_row(d) for d in store.list_drafts(case_id)]}


class DirectDraftRequest(BaseModel):
    """POST /case/drafts/direct body：点交付物直开表单（零 LLM）。"""

    case_id: str = Field(min_length=1)
    team_id: str = Field(min_length=1)
    member_key: str = Field(min_length=1)
    output_name: str = Field(min_length=1)


@router.post("/case/drafts/direct")
async def create_direct_draft(req: DirectDraftRequest) -> dict:
    """交付物模板直开草稿（零 LLM，2026-08-14）。

    专家团 yaml 里已定义 member.output_forms[交付物名] 时，点交付物直接
    建一份结构化草稿，不再走「问专家 → LLM 生成表单」；幂等：同成员同
    交付物已有未通过草稿时直接返回它（不重复建空表单）。
    """
    from agent.expert_teams.api import _loader as team_loader

    team = team_loader.get(req.team_id)
    if team is None:
        raise HTTPException(404, f"专家团 {req.team_id} 不存在")
    member = next((m for m in team.members if m.name == req.member_key), None)
    if member is None:
        raise HTTPException(404, f"专家 {req.member_key} 不在团 {req.team_id} 中")
    if req.output_name not in member.outputs:
        raise HTTPException(400, f"{req.output_name} 不是 {req.member_key} 的交付物")
    raw_fields = member.output_forms.get(req.output_name)
    if not raw_fields:
        raise HTTPException(400, f"交付物「{req.output_name}」未定义表单模板，请继续用问专家生成")

    # 字段归一化复用 LLM 草稿同一套解析（类型白名单 / 去重 / 上限 20）
    parsed = _parse_draft_template(
        json.dumps({"title": req.output_name, "fields": raw_fields}, ensure_ascii=False)
    )
    if parsed is None:
        raise HTTPException(400, "表单模板定义不合法，请到 设置 → 专家团 修正")
    _, fields = parsed

    store = _get_case_storage()
    # 幂等：同成员同标题已有未通过草稿 → 直接返回，不重复建空表单
    for row in store.list_drafts(req.case_id):
        if (
            str(row.get("member_key", "")) == req.member_key
            and str(row.get("title", "")) == req.output_name
            and str(row.get("status", "")) != "passed"
        ):
            return {"draft": _normalize_draft_row(row), "reused": True}

    row = store.add_draft(
        case_id=req.case_id,
        team_id=req.team_id,
        member_key=req.member_key,
        title=req.output_name,
        template_json=dump_json(fields),
        values_json=dump_json({}),
    )
    return {"draft": _normalize_draft_row(row), "reused": False}


@router.put("/case/drafts/{draft_id}")
async def save_case_draft(draft_id: str, req: DraftSaveRequest) -> dict:
    store = _get_case_storage()
    row = store.get_draft(draft_id)
    if row is None:
        raise HTTPException(404, f"draft {draft_id} not found")
    if str(row.get("status", "")) == "passed":
        raise HTTPException(400, "草稿已验收通过，不能再修改")
    # 只保留模板里定义过的字段，值统一转字符串（防脏数据落盘）
    try:
        template = json.loads(str(row.get("template_json", "[]")))
    except (ValueError, TypeError):
        template = []
    allowed = {str(f.get("name", "")) for f in template if isinstance(f, dict)}
    clean = {str(k): str(v) for k, v in req.values.items() if k in allowed}
    updated = store.update_draft(draft_id, values_json=dump_json(clean))
    return _normalize_draft_row(updated)


@router.post("/case/drafts/{draft_id}/submit")
async def submit_case_draft(draft_id: str) -> dict:
    """提交草稿：校验必填 → 渲染 md 落盘为材料 → 自动 AI 审核；
    通过 → 草稿 passed（交付物）；打回 → 保留 submitted 可修改后重提。"""
    import base64

    store = _get_case_storage()
    row = store.get_draft(draft_id)
    if row is None:
        raise HTTPException(404, f"draft {draft_id} not found")
    status = str(row.get("status", ""))
    if status == "passed":
        raise HTTPException(400, "草稿已验收通过，无需重复提交")
    # 重提（上次被打回）：已有材料行则覆写内容重审，否则新建
    if status not in ("draft", "submitted"):
        raise HTTPException(400, f"unknown draft status: {status}")

    try:
        template = json.loads(str(row.get("template_json", "[]")))
        values = json.loads(str(row.get("values_json", "{}")))
    except (ValueError, TypeError):
        raise HTTPException(400, "草稿数据损坏，请重新生成")

    missing = [
        str(f.get("label", f.get("name", "")))
        for f in template
        if f.get("required") and not str(values.get(str(f.get("name", "")), "") or "").strip()
    ]
    if missing:
        raise HTTPException(400, "必填项未填写：" + "、".join(missing))

    title = str(row.get("title", "") or "交付草稿")
    md_text = _render_draft_markdown(title, template, values)
    file_name = f"{title}.md"
    case_id = str(row.get("case_id", ""))
    file_id = str(row.get("file_id", ""))
    existing = store.get_file(file_id) if file_id else None
    if existing is not None:
        # 覆写原文件内容，重置为待审，再走一遍审核
        Path(str(existing.get("file_path", ""))).write_text(md_text, encoding="utf-8")
        store.update_file(
            file_id,
            status=FILE_PENDING,
            review_note="",
            reviewed_by="",
            extracted_fields="[]",
            evidence="[]",
            reject_marks="[]",
        )
    else:
        content_b64 = base64.b64encode(md_text.encode("utf-8")).decode("ascii")
        path = store.save_upload(case_id, file_name, content_b64)
        new_file = store.add_file(
            case_id=case_id,
            team_id=str(row.get("team_id", "")),
            member_key=str(row.get("member_key", "")),
            file_name=file_name,
            file_path=str(path),
        )
        file_id = str(new_file["id"])
    store.update_draft(
        draft_id,
        status="submitted",
        file_id=file_id,
        # 快照 + 版本计数（BUGFIX #79）：打回后用户可对照上次提交内容修改
        last_snapshot=md_text,
        bump_submit_count=True,
    )
    await _audit(
        "ops_case_draft_submit",
        {"case_id": case_id, "draft_id": draft_id, "file_id": file_id, "title": title},
    )

    # 立即自动审核；审核不可用不阻塞提交（材料留待手动重审）
    file_out: dict = _normalize_file_row(store.get_file(file_id))
    try:
        file_out = await _do_review_file(store, file_id)
    except Exception as e:
        logger.warning("[ops-case] draft auto-review unavailable: %s", e)
    draft = store.get_draft(draft_id) or row
    if str(file_out.get("status", "")) == FILE_PASSED:
        draft = store.update_draft(draft_id, status="passed") or draft
    return {"draft": _normalize_draft_row(draft), "file": file_out}


def _build_export_md_rows(files: list[dict]) -> dict[str, list[dict]]:
    by_member: dict[str, list[dict]] = {}
    for f in files:
        by_member.setdefault(str(f.get("member_key", "未分组专家")), []).append(f)
    return by_member


_STATUS_LABEL = {
    FILE_PASSED: "✓ 通过",
    FILE_REJECTED: "✗ 打回",
    FILE_PENDING: "○ 待确认",
    FILE_REVIEWING: "⏳ 审核中",
}


@router.post("/case/export")
async def case_export(req: CaseExportRequest) -> dict:
    store = _get_case_storage()
    files = store.list_files(req.case_id)
    qa = store.list_qa(req.case_id)
    if not files:
        raise HTTPException(400, "Case 内没有任何交付文件，无法导出")

    target = Path(req.target_path)
    if not _path_parent_exists(target):
        raise HTTPException(400, f"导出目录不存在: {target.parent}")

    by_member = _build_export_md_rows(files)
    ts_str = time.strftime("%Y-%m-%d %H:%M:%S")
    # 多文档交叉比对 + 低置信清单（防呆提效，2026-08-10）
    inconsistencies, low_confidence = _crosscheck_fields(files)
    # 业务小结：优先 LLM，失败降级为确定性汇总（导出不被 LLM 阻塞）
    summary_md = _deterministic_summary(req, files, qa)
    try:
        llm_call = _make_summarize_llm()
        raw = await llm_call(
            [
                {
                    "role": "system",
                    "content": (
                        "你是银行运营工作台 AI。请根据下面的业务验收情况，写一段不超过 300 字的"
                        "业务小结（中文，客观克制，不要编造，直接输出正文）。"
                    ),
                },
                {"role": "user", "content": summary_md},
            ]
        )
        if raw.strip():
            summary_md = raw.strip()
    except Exception as e:
        logger.warning("[ops-case] export summary LLM fallback: %s", e)

    # 外部报告模板（2026-08-10）：专家团级 docx/md 模板 → 失败返 None 降级内置结构
    tpl_result = _render_external_template(
        req, by_member, qa, summary_md, ts_str, inconsistencies, low_confidence
    )

    with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as zf:
        _write_export_zip_contents(
            zf,
            req,
            by_member,
            qa,
            summary_md,
            ts_str,
            inconsistencies,
            low_confidence,
            tpl_result,
        )

    await _audit(
        "ops_case_export",
        {
            "case_id": req.case_id,
            "target_path": req.target_path,
            "file_count": len(files),
        },
    )
    # 业务办理完成 → 台账置「结束」（业务汇总，2026-11-09）
    try:
        _close_task_for_case(req.case_id, note="交付物已导出，业务办理结束")
    except Exception as e:  # pragma: no cover - 台账更新失败不阻断导出主链路
        logger.warning("[ops-task] close after export failed: %s", e)
    return {"ok": True, "path": str(target), "file_count": len(files)}


def _write_export_zip_contents(
    zf: zipfile.ZipFile,
    req: CaseExportRequest,
    by_member: dict[str, list[dict]],
    qa: list[dict],
    summary_md: str,
    ts_str: str,
    inconsistencies: list[dict],
    low_confidence: list[dict],
    tpl_result: tuple[str, bytes] | tuple[str, str] | None = None,
) -> None:
    """同步写入 zip 内容（交付文件/检查结果/问答记录/报告初稿/小结/清单）。"""
    # 1) 交付文件（按专家分目录）
    for member_key, member_files in by_member.items():
        for f in member_files:
            src = Path(str(f.get("file_path", "")))
            if src.is_file():
                zf.write(src, f"交付文件/{member_key}/{src.name}")
    # 2) 检查结果（每位专家一份 md）
    for member_key, member_files in by_member.items():
        lines = [f"# {member_key} 检查结果\n"]
        for f in member_files:
            label = _STATUS_LABEL.get(str(f.get("status", "")), str(f.get("status", "")))
            lines.append(f"## {f.get('file_name', '')} — {label}")
            note = str(f.get("review_note", "")).strip()
            lines.append(note if note else "（无审核意见）")
            lines.append("")
        zf.writestr(f"检查结果/{member_key}.md", "\n".join(lines))
    # 3) 问答记录
    if qa:
        qa_lines = ["# 专家问答记录\n"]
        for item in qa:
            qa_lines.append(f"## 问（→ {item.get('member_key', '')}）")
            qa_lines.append(str(item.get("question", "")))
            qa_lines.append("\n**答：**\n" + str(item.get("answer", "")))
            qa_lines.append("")
        zf.writestr("问答记录.md", "\n".join(qa_lines))
    # 4) 报告初稿（写八股文变改填空题，2026-08-10）：外部模板 → 内置 docx → 内置 md
    if tpl_result is not None:
        tpl_kind, tpl_content = tpl_result
        if tpl_kind == "docx":
            zf.writestr("尽调报告初稿.docx", tpl_content)
        else:
            zf.writestr("报告初稿.md", tpl_content)
    else:
        report_docx = _build_report_docx(
            req, by_member, qa, summary_md, ts_str, inconsistencies, low_confidence
        )
        if report_docx is not None:
            zf.writestr("尽调报告初稿.docx", report_docx)
        else:
            zf.writestr(
                "报告初稿.md",
                _build_report_markdown(
                    req, by_member, qa, summary_md, ts_str, inconsistencies, low_confidence
                ),
            )
    # 5) 业务小结 + 交付物清单
    zf.writestr("业务小结.md", f"# 业务小结\n\n{summary_md}\n")
    file_count = sum(len(v) for v in by_member.values())
    readme_lines = [
        "# 交付物清单\n",
        f"- 业务：{req.feature_name or req.case_id}",
        f"- 专家团：{req.team_name or '未指定'}",
        f"- 导出时间：{ts_str}",
        f"- 材料文件：{file_count} 份",
        "",
        "## 验收清单",
    ]
    if req.checklist:
        readme_lines += [f"- [x] {x}" for x in req.checklist]
    else:
        readme_lines.append("- （无）")
    zf.writestr("README.md", "\n".join(readme_lines))


def _deterministic_summary(req: CaseExportRequest, files: list[dict], qa: list[dict]) -> str:
    lines = [f"业务：{req.feature_name or req.case_id}"]
    passed = sum(1 for f in files if f.get("status") == FILE_PASSED)
    lines.append(f"材料共 {len(files)} 份，验收通过 {passed} 份。")
    for f in files:
        label = _STATUS_LABEL.get(str(f.get("status", "")), str(f.get("status", "")))
        lines.append(f"- {f.get('member_key', '')} / {f.get('file_name', '')}：{label}")
    if req.checklist:
        lines.append("验收清单：" + "；".join(req.checklist))
    if qa:
        lines.append(f"专家问答 {len(qa)} 条。")
    return "\n".join(lines)


def _build_template_context(
    req: CaseExportRequest,
    by_member: dict[str, list[dict]],
    qa: list[dict],
    summary_md: str,
    ts_str: str,
    inconsistencies: list[dict],
    low_confidence: list[dict],
):
    """组装外部报告模板的渲染上下文（占位符规范见 ops/report_template.py）。"""
    from .report_template import TemplateContext

    files = [f for group in by_member.values() for f in group]

    acceptance_lines: list[str] = []
    rows: list[dict[str, str]] = []
    for member_key, group in by_member.items():
        for f in group:
            label = _STATUS_LABEL.get(str(f.get("status", "")), str(f.get("status", "")))
            note = str(f.get("review_note", "")).strip()
            line = f"- {member_key} / {f.get('file_name', '')}：{label}"
            if note:
                line += f"（意见：{note[:120]}）"
            acceptance_lines.append(line)
            rows.append(
                {
                    "材料": str(f.get("file_name", "")),
                    "专家": member_key,
                    "状态": label,
                    "意见": note[:200],
                }
            )

    cross_lines: list[str] = []
    if inconsistencies:
        for inc in inconsistencies:
            detail = "；".join(f"{v['file']}={v['value']}" for v in inc["values"])
            cross_lines.append(f"- ⚠ 字段「{inc['field']}」不一致：{detail}")
    else:
        cross_lines.append("- 未发现跨材料要素不一致")

    qa_lines = [
        f"- 问（→ {item.get('member_key', '')}）：{item.get('question', '')} "
        f"答：{str(item.get('answer', ''))[:200]}"
        for item in qa
    ] or ["- （无问答记录）"]

    manual_lines = [f"- {x}" for x in _report_manual_items(files, low_confidence)]

    return TemplateContext(
        text={
            "业务名称": req.feature_name or req.case_id,
            "专家团": req.team_name or "未指定",
            "生成时间": ts_str,
            "材料数量": str(len(files)),
            "风险结论": summary_md,
        },
        lists={
            "材料验收清单": acceptance_lines,
            "交叉比对清单": cross_lines,
            "问答记录清单": qa_lines,
            "人工确认事项": manual_lines,
        },
        rows=rows,
    )


def _render_external_template(
    req: CaseExportRequest,
    by_member: dict[str, list[dict]],
    qa: list[dict],
    summary_md: str,
    ts_str: str,
    inconsistencies: list[dict],
    low_confidence: list[dict],
) -> tuple[str, bytes] | tuple[str, str] | None:
    """外部报告模板渲染：('docx', bytes) / ('md', str) / None（降级内置结构）。

    红线：模板是可选增强，任何异常都返 None，绝不阻塞导出。
    """
    if not req.team_id:
        return None
    try:
        from agent.expert_teams.api import get_loader
        from agent.expert_teams.templates import resolve_template_path

        team = get_loader().get(req.team_id)
        if team is None:
            return None
        tpl_path = resolve_template_path(team.id, team.report_template)
        if tpl_path is None:
            return None

        from .report_template import render_docx_template, render_markdown_template

        ctx = _build_template_context(
            req, by_member, qa, summary_md, ts_str, inconsistencies, low_confidence
        )
        if tpl_path.suffix.lower() == ".docx":
            data = render_docx_template(tpl_path, ctx)
            if data:
                return ("docx", data)
            return None
        return ("md", render_markdown_template(tpl_path, ctx))
    except Exception as e:
        logger.warning("[ops-case] external template render failed, fallback builtin: %s", e)
        return None


# ---------------------------------------------------------------------------
# 多文档交叉比对 + 报告初稿（2026-08-10）
# ---------------------------------------------------------------------------


def _loads_json_list(value: Any) -> list[dict]:
    """存储行的 JSON 文本 → list（兼容已解析的 list）。"""
    if isinstance(value, list):
        return [x for x in value if isinstance(x, dict)]
    if isinstance(value, str):
        try:
            data = json.loads(value or "[]")
            if isinstance(data, list):
                return [x for x in data if isinstance(x, dict)]
        except (ValueError, TypeError):
            pass
    return []


def _crosscheck_fields(files: list[dict]) -> tuple[list[dict], list[dict]]:
    """多文档交叉比对（人肉比对变自动交叉，2026-08-10）。

    返回 (inconsistencies, low_confidence)：
    - inconsistencies：同名字段在不同材料中取值不一致（如法人名字对不上）
    - low_confidence：置信度 < 0.6 的要素清单（需人工核对）
    """
    by_field: dict[str, list[dict]] = {}
    low: list[dict] = []
    for f in files:
        fname = str(f.get("file_name", ""))
        for item in _loads_json_list(f.get("extracted_fields")):
            name = str(item.get("field", "")).strip()
            value = str(item.get("value", "")).strip()
            if not name or not value:
                continue
            try:
                conf = float(item.get("confidence", 0.8))
            except (TypeError, ValueError):
                conf = 0.8
            by_field.setdefault(name, []).append(
                {"file": fname, "value": value, "confidence": conf}
            )
            if conf < 0.6:
                low.append({"file": fname, "field": name, "value": value, "confidence": conf})
    inconsistencies: list[dict] = []
    for name, occs in by_field.items():
        if len(occs) >= 2 and len({o["value"] for o in occs}) > 1:
            inconsistencies.append({"field": name, "values": occs})
    return inconsistencies, low


@router.get("/case/crosscheck")
async def case_crosscheck(case_id: str = Query(...)) -> dict:
    """同 Case 内多份材料的要素交叉比对（不一致标红，防退件）。"""
    store = _get_case_storage()
    files = store.list_files(case_id)
    inconsistencies, low_confidence = _crosscheck_fields(files)
    return {
        "case_id": case_id,
        "inconsistencies": inconsistencies,
        "low_confidence": low_confidence,
        "consistent": len(inconsistencies) == 0,
    }


def _report_manual_items(files: list[dict], low_confidence: list[dict]) -> list[str]:
    """报告「需人工确认」清单（人机边界：AI 干脏活，人干细活）。"""
    items: list[str] = []
    for f in files:
        status = str(f.get("status", ""))
        if status in (FILE_REJECTED, FILE_PENDING):
            label = _STATUS_LABEL.get(status, status)
            items.append(f"{f.get('file_name', '')}：验收状态为{label}，需人工复核后改判")
    for lc in low_confidence:
        items.append(
            f"{lc['file']}：要素「{lc['field']}」置信度仅 "
            f"{lc['confidence'] * 100:.0f}%（值：{lc['value']}），需人工核对"
        )
    items.append("报告中的主观判断与风险定性段落（标注【需人工填写】处）")
    items.append("签字确认：经办人 / 复核人 / 日期")
    return items


def _build_report_markdown(
    req: CaseExportRequest,
    by_member: dict[str, list[dict]],
    qa: list[dict],
    summary_md: str,
    ts_str: str,
    inconsistencies: list[dict],
    low_confidence: list[dict],
) -> str:
    """报告初稿的 md 降级版（python-docx 不可用时）。"""
    files = [f for group in by_member.values() for f in group]
    lines = [
        f"# {req.feature_name or req.case_id} 尽调报告（初稿）\n",
        "> 本报告为 AI 生成初稿：客观数据已自动填充；标注【需人工填写】处必须由人工确认补全。\n",
        "## 一、业务概况",
        f"- 业务：{req.feature_name or req.case_id}",
        f"- 专家团：{req.team_name or '未指定'}",
        f"- 生成时间：{ts_str}",
        f"- 材料：{len(files)} 份\n",
        "## 二、材料验收结果",
    ]
    for member_key, group in by_member.items():
        for f in group:
            label = _STATUS_LABEL.get(str(f.get("status", "")), str(f.get("status", "")))
            lines.append(f"- {member_key} / {f.get('file_name', '')}：{label}")
            note = str(f.get("review_note", "")).strip()
            if note:
                lines.append(f"  - 意见：{note}")
    lines += ["", "## 三、交叉比对结果"]
    if inconsistencies:
        for inc in inconsistencies:
            detail = "；".join(f"{v['file']}={v['value']}" for v in inc["values"])
            lines.append(f"- ⚠ 字段「{inc['field']}」不一致：{detail}")
    else:
        lines.append("- 未发现跨材料要素不一致")
    lines += ["", "## 四、风险与结论（AI 初稿，需人工审核）", summary_md, ""]
    lines += ["## 五、需人工确认事项"]
    lines += [f"- {x}" for x in _report_manual_items(files, low_confidence)]
    lines += ["", "经办人：【需人工填写】　复核人：【需人工填写】　日期：【需人工填写】"]
    return "\n".join(lines) + "\n"


def _build_report_docx(
    req: CaseExportRequest,
    by_member: dict[str, list[dict]],
    qa: list[dict],
    summary_md: str,
    ts_str: str,
    inconsistencies: list[dict],
    low_confidence: list[dict],
) -> bytes | None:
    """尽调报告初稿 docx（写八股文变改填空题，2026-08-10）。

    python-docx 不可用/生成失败 → 返 None，由调用方降级 md。
    人机边界：客观数据自动填充；低置信/待确认项标红；主观判断留「【需人工填写】」空位。
    """
    try:
        import io

        from docx import Document
        from docx.shared import RGBColor
    except Exception as e:
        logger.warning("[ops-case] python-docx unavailable, report fallback to md: %s", e)
        return None

    red = RGBColor(0xCD, 0x31, 0x31)
    files = [f for group in by_member.values() for f in group]
    try:
        doc = Document()
        doc.add_heading(f"{req.feature_name or req.case_id} 尽调报告（初稿）", level=0)
        meta = doc.add_paragraph()
        meta.add_run(
            "本报告为 AI 生成初稿：客观数据已自动填充；"
            "标注【需人工填写】处必须由人工确认补全，签字后方可提交。"
        ).italic = True

        doc.add_heading("一、业务概况", level=1)
        for line in (
            f"业务：{req.feature_name or req.case_id}",
            f"专家团：{req.team_name or '未指定'}",
            f"生成时间：{ts_str}",
            f"材料：{len(files)} 份",
        ):
            doc.add_paragraph(line)

        doc.add_heading("二、材料验收结果", level=1)
        table = doc.add_table(rows=1, cols=4)
        table.style = "Table Grid"
        for i, head in enumerate(("材料", "审核专家", "状态", "AI 意见")):
            table.rows[0].cells[i].text = head
        for member_key, group in by_member.items():
            for f in group:
                row = table.add_row().cells
                row[0].text = str(f.get("file_name", ""))
                row[1].text = member_key
                row[2].text = _STATUS_LABEL.get(str(f.get("status", "")), str(f.get("status", "")))
                row[3].text = str(f.get("review_note", ""))[:200]

        doc.add_heading("三、交叉比对结果", level=1)
        if inconsistencies:
            for inc in inconsistencies:
                p = doc.add_paragraph()
                run = p.add_run(
                    "⚠ 字段「"
                    + str(inc["field"])
                    + "」不一致："
                    + "；".join(f"{v['file']}={v['value']}" for v in inc["values"])
                )
                run.font.color.rgb = red
        else:
            doc.add_paragraph("未发现跨材料要素不一致。")

        doc.add_heading("四、专家问答记录", level=1)
        if qa:
            for item in qa:
                doc.add_paragraph(
                    f"问（→ {item.get('member_key', '')}）：{item.get('question', '')}"
                )
                doc.add_paragraph(f"答：{str(item.get('answer', ''))[:500]}")
        else:
            doc.add_paragraph("（无问答记录）")

        doc.add_heading("五、风险与结论（AI 初稿，需人工审核）", level=1)
        doc.add_paragraph(summary_md)

        doc.add_heading("六、需人工确认事项", level=1)
        for item in _report_manual_items(files, low_confidence):
            p = doc.add_paragraph(style="List Bullet")
            p.add_run(item)

        doc.add_paragraph("经办人：【需人工填写】　复核人：【需人工填写】　日期：【需人工填写】")

        buf = io.BytesIO()
        doc.save(buf)
        return buf.getvalue()
    except Exception as e:
        logger.warning("[ops-case] report docx build failed, fallback to md: %s", e)
        return None


# ---------------------------------------------------------------------------
# 凭证预览（2026-11-09）：凭证节点 · 影像识别元数据 · 自动归类
#
# 三个概念（缺一不可）：
#   凭证节点 = 专家团 member.credentials 预设「这笔业务要传哪几张图」
#   凭证资产 = 已上传材料（case_files，走专家审核）+ 识别底图
#              （credential_images，OCR 自动填入时留下的原图，不走审核，
#                避免「顺带上传」带来的二次审核噪声）
#   影像定位 = 端侧 RapidOCR 逐行四点框（ocr_meta.blocks）落库，前端拿
#              「录入值」匹配 block 文本 → 放大并高亮图片对应区域
#
# 归类（“做一次分类 → 自动挂载 → 可手动拖动”）：
#   1. keyword：节点 keywords / 名称核心词命中文件名或识别文本（零成本）
#   2. ai：一次 LLM 批量判定（本地优先三级降级，只接受白名单节点名）
#   3. 未命中：留空进「未归属」，前端拖动/下拉手动挂载（manual）
# ---------------------------------------------------------------------------

ASSET_FILE = "file"
ASSET_IMAGE = "image"

# 归类命中词里剔除的泛词：单独出现区分不出具体是哪张凭证
_CREDENTIAL_GENERIC_TERMS = frozenset(
    {"影像", "材料", "证明", "文件", "单据", "扫描件", "照片", "副本", "资料", "原件", "页"}
)
_TERM_SPLIT_RE = re.compile(r"[（）()【】\[\]·、,，/／\s]+")
_CREDENTIAL_CLASSIFY_MAX = 40


def _asset_kind(asset_id: str) -> str:
    """凭证资产 id → 类型：CF-* 已审材料，CI-* 识别底图。"""
    return ASSET_IMAGE if str(asset_id).startswith("CI-") else ASSET_FILE


def _load_ocr_meta(raw: object) -> dict[str, Any]:
    """ocr_meta 列 → dict（坏 JSON / 空 → {}）。"""
    if isinstance(raw, dict):
        return raw
    if not isinstance(raw, str) or not raw.strip():
        return {}
    try:
        data = json.loads(raw)
    except (ValueError, TypeError):
        return {}
    return data if isinstance(data, dict) else {}


def _credential_terms(node: dict) -> list[str]:
    """节点可用于命中的词：keywords 原样，名称拆词后丢泛词。"""
    terms: list[str] = []
    for kw in node.get("keywords") or []:
        s = str(kw).strip()
        if len(s) >= 2 and s not in terms:
            terms.append(s)
    name = str(node.get("name", "")).strip()
    for token in _TERM_SPLIT_RE.split(name):
        t = token.strip()
        if len(t) < 2 or t in _CREDENTIAL_GENERIC_TERMS or t in terms:
            continue
        terms.append(t)
    return terms


def _match_credential(nodes: list[dict], *texts: str) -> str:
    """按 texts 优先级（文件名 → 识别文本）找第一个命中的凭证节点名。"""
    corpus = [t for t in texts if t]
    if not corpus:
        return ""
    for text in corpus:
        for node in nodes:
            name = str(node.get("name", "")).strip()
            if not name:
                continue
            if any(term in text for term in _credential_terms(node)):
                return name
    return ""


def _credential_nodes_of(team_id: str) -> dict[str, list[dict]]:
    """团内成员 → 预设凭证节点（团不存在 / loader 未就绪 → 空）。"""
    if not team_id:
        return {}
    try:
        from agent.expert_teams.api import get_loader

        team = get_loader().get(team_id)
    except Exception as e:
        logger.warning("[ops-credential] team loader unavailable: %s", e)
        return {}
    if team is None:
        return {}
    return {m.name: [dict(c) for c in (m.to_dict().get("credentials") or [])] for m in team.members}


def _asset_ocr_brief(meta: dict[str, Any]) -> dict[str, Any]:
    """ocr_meta → 轻摘要（块坐标不进聚合响应，选中资产时再拉）。"""
    blocks = meta.get("blocks")
    return {
        "engine": str(meta.get("engine", "") or ""),
        "width": int(meta.get("width") or 0),
        "height": int(meta.get("height") or 0),
        "blocks": len(blocks) if isinstance(blocks, list) else 0,
        "confidence": float(meta.get("confidence") or 0.0),
        "elapsed_ms": int(meta.get("elapsed_ms") or 0),
        "error": str(meta.get("error", "") or ""),
    }


def _asset_brief(row: dict, kind: str) -> dict[str, Any]:
    """材料行 / 底图行 → 凭证资产摘要（带 ocr 轻摘要，供面板判断能否定位）。"""
    file_name = str(row.get("file_name", "") or "")
    meta = _load_ocr_meta(row.get("ocr_meta"))
    return {
        "kind": kind,
        "id": str(row.get("id", "") or ""),
        "case_id": str(row.get("case_id", "") or ""),
        "team_id": str(row.get("team_id", "") or ""),
        "member_key": str(row.get("member_key", "") or ""),
        "credential": str(row.get("credential", "") or ""),
        "classified_by": str(row.get("classified_by", "") or ""),
        "file_name": file_name,
        "status": str(row.get("status", "") or ""),
        "source": str(row.get("source", "") or "upload"),
        "draft_id": str(row.get("draft_id", "") or ""),
        "review_note": str(row.get("review_note", "") or ""),
        "is_image": kind == ASSET_IMAGE or is_image_file(file_name),
        "has_ocr": bool(meta.get("blocks")),
        "ocr": _asset_ocr_brief(meta),
        "created_at": int(row.get("created_at") or 0),
    }


def _norm_field(s: str) -> str:
    """字段名/别名归一：去空白括号标点、转小写（中英混排别名对齐用）。"""
    return re.sub(r"[\s（）()【】\[〕\]·、,，:：_/／-]+", "", str(s or "")).lower()


def _field_alias_match(a: str, b: str) -> bool:
    """字段名与要素名是否同一个东西（完全等 / 一方含另一方，至少 2 字）。"""
    x, y = _norm_field(a), _norm_field(b)
    if not x or not y:
        return False
    if x == y:
        return True
    shorter, longer = (x, y) if len(x) <= len(y) else (y, x)
    return len(shorter) >= 2 and shorter in longer


def _asset_capture_candidates(
    brief: dict[str, Any],
    *,
    fields: list[dict],
    source: str,
) -> list[dict[str, Any]]:
    """单个资产的值 → 采集候选（字段名/值/来源/资产 id）。

    fields 形如 [{name,label,value,confidence}]（审核要素或 OCR 填入值）；
    空值丢弃 —— 「填了才算采到」。
    """
    out: list[dict[str, Any]] = []
    for f in fields:
        if not isinstance(f, dict):
            continue
        name = str(f.get("name") or f.get("field") or "").strip()
        label = str(f.get("label") or "").strip()
        value = str(f.get("value") or "").strip()
        if not name or not value or value in ("【未填写】", "—"):
            continue
        try:
            conf = float(f.get("confidence") or 0.0)
        except (TypeError, ValueError):
            conf = 0.0
        out.append(
            {
                "name": name,
                "label": label or name,
                "value": value[:200],
                "source": source,
                "asset_id": brief["id"],
                "asset_kind": brief["kind"],
                "asset_credential": brief["credential"],
                "confidence": conf,
                "can_locate": bool(brief["has_ocr"]),
            }
        )
    return out


def _json_obj(raw: object) -> dict[str, Any]:
    """JSON 列 → dict（坏 JSON / 非对象 → {}）。"""
    if isinstance(raw, dict):
        return raw
    if not isinstance(raw, str) or not raw.strip():
        return {}
    try:
        data = json.loads(raw)
    except (ValueError, TypeError):
        return {}
    return data if isinstance(data, dict) else {}


def _json_list(raw: object) -> list[Any]:
    """JSON 列 → list（坏 JSON / 非数组 → []）。"""
    if isinstance(raw, list):
        return raw
    if not isinstance(raw, str) or not raw.strip():
        return []
    try:
        data = json.loads(raw)
    except (ValueError, TypeError):
        return []
    return data if isinstance(data, list) else []


def _credential_team_plan(team_id: str) -> dict[str, Any]:
    """团 → {team_name, members:[{name, credentials}] }（按成员定义顺序）。"""
    if not team_id:
        return {"team_name": "", "members": []}
    try:
        from agent.expert_teams.api import get_loader

        team = get_loader().get(team_id)
    except Exception as e:
        logger.warning("[ops-credential] team loader unavailable: %s", e)
        return {"team_name": "", "members": []}
    if team is None:
        return {"team_name": "", "members": []}
    members = [
        {
            "name": m.name,
            "credentials": [dict(c) for c in (m.to_dict().get("credentials") or [])],
        }
        for m in team.members
    ]
    return {"team_name": team.name, "members": members}


def _asset_evidence(row: dict, kind: str) -> str:
    """归类用的证据文本：识别原文 + 已采要素值（不重新解析文档）。"""
    parts: list[str] = [str(row.get("file_name", "") or "")]
    meta = _load_ocr_meta(row.get("ocr_meta"))
    text = str(meta.get("text", "") or "")
    if text:
        parts.append(text[:400])
    if kind == ASSET_IMAGE:
        parts += [str(v) for v in _json_obj(row.get("values_json")).values()][:12]
    else:
        parts += [
            f"{f.get('field', '')}:{f.get('value', '')}"
            for f in _json_list(row.get("extracted_fields"))[:12]
            if isinstance(f, dict)
        ]
    return " ".join(p for p in parts if p)[:900]


def _pick_field_candidate(
    field: dict[str, Any],
    candidates: list[dict[str, Any]],
    node_name: str,
) -> dict[str, Any] | None:
    """节点要采集的要素 → 最能说明来源的候选值。

    优先级：挂在同一凭证节点下 > 能在影像上定位 > 置信度高。
    """
    names = [str(field.get("name", "")), str(field.get("label", ""))]
    matched = [
        c
        for c in candidates
        if any(
            _field_alias_match(n, str(c["name"])) or _field_alias_match(n, str(c["label"]))
            for n in names
            if n
        )
    ]
    if not matched:
        return None

    def _rank(c: dict[str, Any]) -> tuple[int, int, int, float]:
        return (
            1 if c["asset_credential"] == node_name else 0,
            1 if c["can_locate"] else 0,
            1 if c["source"] == "ocr" else 0,
            float(c["confidence"]),
        )

    return max(matched, key=_rank)


def _member_image_values(row: dict, labels: dict[str, str]) -> list[dict]:
    """识别底图行的 values_json → 要素列表（label 从草稿模板补）。"""
    return [
        {"name": k, "label": labels.get(k, k), "value": v}
        for k, v in _json_obj(row.get("values_json")).items()
        if str(v or "").strip()
    ]


def _build_credential_view(
    store: CaseStorage,
    case_id: str,
    team_ids: list[str],
) -> dict[str, Any]:
    """凭证预览聚合：预设节点 + 实挂资产 + 要素采集情况 + 缺失统计。

    返回体不给块坐标与图片字节（体积不可控），面板选中某个资产时
    再调 /case/credential-asset/{id} 一次拿图 + blocks。
    """
    file_rows = store.list_files(case_id)
    image_rows = store.list_images(case_id)
    draft_rows = store.list_drafts(case_id)
    assets = [_asset_brief(r, ASSET_FILE) for r in file_rows] + [
        _asset_brief(r, ASSET_IMAGE) for r in image_rows
    ]
    wanted = [t for t in team_ids if t]
    if not wanted:
        seen = {a["team_id"] for a in assets if a["team_id"]}
        seen |= {str(d.get("team_id", "") or "") for d in draft_rows}
        wanted = sorted(seen - {""})

    # 草稿侧：填写值 + 字段 label（手动录入兼底展示，无影像可定位）
    draft_values: dict[str, dict[str, str]] = {}
    draft_labels: dict[str, dict[str, str]] = {}
    draft_member: dict[str, str] = {}
    for d in draft_rows:
        did = str(d.get("id", "") or "")
        if not did:
            continue
        values = {str(k): str(v).strip() for k, v in _json_obj(d.get("values_json")).items()}
        draft_values[did] = {k: v for k, v in values.items() if v}
        draft_labels[did] = {
            str(f.get("name", "")): str(f.get("label") or f.get("name", ""))
            for f in _json_list(d.get("template_json"))
            if isinstance(f, dict) and f.get("name")
        }
        draft_member[did] = str(d.get("member_key", "") or "")

    teams_out: list[dict[str, Any]] = []
    covered_ids: set[str] = set()
    stats_missing: list[dict[str, str]] = []
    required_total = required_ok = 0
    has_preset = False

    for tid in wanted:
        plan = _credential_team_plan(tid)
        nodes_by_member = {m["name"]: m["credentials"] for m in plan["members"]}
        members_out: list[dict[str, Any]] = []
        for member_name, nodes in nodes_by_member.items():
            if nodes:
                has_preset = True
            my_assets = [
                a for a in assets if a["member_key"] == member_name and a["team_id"] in (tid, "")
            ]
            covered_ids |= {a["id"] for a in my_assets}
            # 采集候选：材料审核要素 + 底图 OCR 填入值 + 草稿已填值（手动）
            candidates: list[dict[str, Any]] = []
            for a in my_assets:
                if a["kind"] == ASSET_IMAGE:
                    candidates += _asset_capture_candidates(
                        a,
                        fields=_member_image_values(
                            _row_of(image_rows, a["id"]),
                            draft_labels.get(a["draft_id"], {}),
                        ),
                        source="ocr",
                    )
                else:
                    fields = [
                        {
                            "name": f.get("field"),
                            "label": f.get("field"),
                            "value": f.get("value"),
                            "confidence": f.get("confidence"),
                        }
                        for f in _json_list(_row_of(file_rows, a["id"]).get("extracted_fields"))
                        if isinstance(f, dict)
                    ]
                    candidates += _asset_capture_candidates(a, fields=fields, source="ai")
            used_draft_ids = {a["draft_id"] for a in my_assets if a["kind"] == ASSET_IMAGE}
            for did, dmember in draft_member.items():
                if dmember != member_name or did in used_draft_ids:
                    continue
                stub = {
                    "id": did,
                    "kind": "draft",
                    "credential": "",
                    "has_ocr": False,
                }
                candidates += _asset_capture_candidates(
                    stub,  # type: ignore[arg-type]
                    fields=[
                        {"name": k, "label": draft_labels.get(did, {}).get(k, k), "value": v}
                        for k, v in draft_values.get(did, {}).items()
                    ],
                    source="manual",
                )

            credentials_out: list[dict[str, Any]] = []
            node_names = {str(n.get("name", "")) for n in nodes}
            for node in nodes:
                name = str(node.get("name", ""))
                node_assets = [a for a in my_assets if a["credential"] == name]
                field_values: list[dict[str, Any]] = []
                missing_fields: list[dict[str, str]] = []
                for field in node.get("fields") or []:
                    if not isinstance(field, dict):
                        continue
                    picked = _pick_field_candidate(field, candidates, name)
                    if picked is None:
                        flabel = str(field.get("label") or field.get("name", ""))
                        missing_fields.append({"name": str(field.get("name", "")), "label": flabel})
                        continue
                    field_values.append(picked)
                satisfied = bool(node_assets)
                if node.get("required"):
                    required_total += 1
                    if satisfied:
                        required_ok += 1
                    else:
                        stats_missing.append({"member_key": member_name, "credential": name})
                credentials_out.append(
                    {
                        "name": name,
                        "required": bool(node.get("required", False)),
                        "media": str(node.get("media", "any") or "any"),
                        "hint": str(node.get("hint", "") or ""),
                        "keywords": [str(k) for k in (node.get("keywords") or [])],
                        "satisfied": satisfied,
                        "assets": node_assets,
                        "field_values": field_values,
                        "fields_missing": missing_fields,
                    }
                )
            members_out.append(
                {
                    "member_key": member_name,
                    "has_preset": bool(nodes),
                    "credentials": credentials_out,
                    "unassigned": [a for a in my_assets if a["credential"] not in node_names],
                }
            )
        teams_out.append(
            {
                "team_id": tid,
                "team_name": plan["team_name"] or tid,
                "members": members_out,
            }
        )

    # 不属于任何已选团（团已取消选择 / 无 team_id）的资产也要可见，否则“已传但找不到”比缺失更误导
    leftover: dict[str, list[dict[str, Any]]] = {}
    for a in assets:
        if a["id"] in covered_ids:
            continue
        leftover.setdefault(a["member_key"] or "未指定专家", []).append(a)
    for member_name, items in sorted(leftover.items()):
        teams_out.append(
            {
                "team_id": "",
                "team_name": "未关联专家团",
                "members": [
                    {
                        "member_key": member_name,
                        "has_preset": False,
                        "credentials": [],
                        "unassigned": items,
                    }
                ],
            }
        )

    return {
        "case_id": case_id,
        "teams": teams_out,
        "stats": {
            "required_total": required_total,
            "required_ok": required_ok,
            "missing": stats_missing,
            "has_preset": has_preset,
            "asset_total": len(assets),
            "unassigned_total": sum(len(m["unassigned"]) for t in teams_out for m in t["members"]),
        },
    }


def _row_of(rows: list[dict], row_id: str) -> dict:
    """在行列表里按 id 取原始行（聚合时取未被摘要的列）。"""
    for r in rows:
        if str(r.get("id", "")) == row_id:
            return r
    return {}


# ---- 影像识别（后台补跑：上传不等 OCR，RapidOCR 冷启动可遮十几秒）--------

_OCR_BACKGROUNDS: set[asyncio.Task[Any]] = set()

_IMAGE_MIME_BY_SUFFIX = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".webp": "image/webp",
    ".bmp": "image/bmp",
    ".tif": "image/tiff",
    ".tiff": "image/tiff",
}


def _mime_of(file_name: str) -> str:
    return _IMAGE_MIME_BY_SUFFIX.get(Path(file_name).suffix.lower(), "application/octet-stream")


async def _recognize_asset(
    store: CaseStorage,
    asset_id: str,
    kind: str,
    file_path: str,
) -> dict[str, Any]:
    """端侧 OCR 一张影像并把元数据（含四点框）落库。"""
    from agent.image_processing.ocr import ocr_image_meta

    meta = await asyncio.to_thread(ocr_image_meta, file_path)
    raw = dump_json(meta)
    if kind == ASSET_IMAGE:
        store.update_image(asset_id, ocr_meta=raw)
    else:
        store.update_file(asset_id, ocr_meta=raw)
    return meta


def _schedule_asset_pipeline(store: CaseStorage, asset_id: str, kind: str, file_path: str) -> None:
    """后台补跑「影像识别 → 凭证归类」（不阻上传主链路）。

    识别完成同时能给归类提供文本证据，所以两步串在同一条后台任务里；
    任何异常只记日志（面板会显示「未识别」并给「立即识别」出口）。
    """

    async def _run() -> None:
        try:
            if is_image_file(file_path):
                await _recognize_asset(store, asset_id, kind, file_path)
            await _classify_assets(store, [(kind, asset_id)])
        except Exception as exc:
            logger.info("[ops-credential] 后台识别/归类失败 %s: %s", asset_id, exc)

    try:
        task = asyncio.create_task(_run())
    except RuntimeError:  # 无运行中的事件循环（同步单测环境）：静默略过
        return
    _OCR_BACKGROUNDS.add(task)
    task.add_done_callback(_OCR_BACKGROUNDS.discard)


# ---- 凭证归类：关键词一级 → LLM 二级→留给人工拖动 ---------------------


def _classify_targets(
    store: CaseStorage,
    case_id: str,
    *,
    force: bool = False,
) -> tuple[list[tuple[str, dict[str, Any], list[dict]]], list[dict[str, Any]]]:
    """收集待归类资产 → (关键词已命中列表, 需要 LLM 判定的分组)。

    分组按「团+成员」聚合（每个成员的候选节点不同），LLM 一次调用涵盖全部组。
    """
    hits: list[tuple[str, dict[str, Any], list[dict]]] = []
    pending_by_member: dict[tuple[str, str], dict[str, Any]] = {}
    for kind, rows in (
        (ASSET_FILE, store.list_files(case_id)),
        (ASSET_IMAGE, store.list_images(case_id)),
    ):
        for row in rows:
            asset_id = str(row.get("id", "") or "")
            team_id = str(row.get("team_id", "") or "")
            member_key = str(row.get("member_key", "") or "")
            nodes = _credential_nodes_of(team_id).get(member_key) or []
            if not nodes:
                continue  # 该专家没预设凭证节点，不花 LLM 钱
            if str(row.get("credential", "") or "") and not force:
                continue
            evidence = _asset_evidence(row, kind)
            hit = _match_credential(nodes, evidence)
            if hit:
                hits.append((kind, {**row, "credential": hit, "classified_by": "keyword"}, nodes))
                continue
            key = (team_id, member_key)
            group = pending_by_member.setdefault(
                key,
                {
                    "team_id": team_id,
                    "member_key": member_key,
                    "nodes": nodes,
                    "items": [],
                },
            )
            if len(group["items"]) < _CREDENTIAL_CLASSIFY_MAX:
                group["items"].append(
                    {
                        "id": asset_id,
                        "kind": kind,
                        "member_key": member_key,
                        "file_name": str(row.get("file_name", "") or ""),
                        "evidence": evidence[:300],
                    }
                )
    return hits, list(pending_by_member.values())


def _build_classify_messages(groups: list[dict[str, Any]]) -> list[dict[str, str]]:
    """归类提示词：候选节点逐成员列出，要求只返回白名单节点名。"""
    member_lines: list[str] = []
    item_lines: list[str] = []
    for g in groups:
        opts: list[str] = []
        for node in g["nodes"]:
            desc = f"{node.get('name')}"
            hint = str(node.get("hint", "") or "").strip()
            if hint:
                desc += f"（{hint}）"
            opts.append(desc)
        member_lines.append(
            f"- 专家「{g['member_key']}」的候选凭证：" + " / ".join(str(o) for o in opts)
        )
        for it in g["items"]:
            item_lines.append(
                f"- id={it['id']} 专家={it.get('member_key') or g['member_key']} "
                f"文件名={it['file_name']} 识别摘要={it['evidence']}"
            )
    return [
        {
            "role": "system",
            "content": "\n".join(
                [
                    "你是银行运营凭证归类助手，把客户经理上传的材料归到正确的凭证节点。",
                    "只能从该文件所属专家的候选凭证里选，候选里没有的不得臆造；",
                    "无法判断的文件直接省略，不要猜。",
                    "只返回 JSON（不要 Markdown 代码块）："
                    '{"assignments": [{"id": "材料id", "credential": "凭证节点名"}]}。',
                ]
            ),
        },
        {
            "role": "user",
            "content": "\n".join(
                ["各专家候选凭证：", *member_lines, "", "待归类材料：", *item_lines]
            ),
        },
    ]


async def _classify_by_llm(groups: list[dict[str, Any]]) -> dict[str, str]:
    """LLM 批量归类（本地优先三级降级）；只接受该组成员的候选节点名。"""
    from agent.llm.json_discipline import extract_json

    llm_call = _make_summarize_llm()
    try:
        raw = await llm_call(_build_classify_messages(groups))
    except Exception as exc:
        logger.info("[ops-credential] 归类 LLM 不可用: %s", exc)
        return {}
    data = extract_json(str(raw or ""))
    if isinstance(data, dict) and isinstance(data.get("assignments"), list):
        data = data["assignments"]
    if not isinstance(data, list):
        return {}
    allowed: dict[str, set[str]] = {}
    for g in groups:
        for it in g["items"]:
            allowed[str(it["id"])] = {str(n.get("name", "")) for n in g["nodes"]}
    out: dict[str, str] = {}
    for item in data:
        if not isinstance(item, dict):
            continue
        asset_id = str(item.get("id", "") or "").strip()
        credential = str(item.get("credential", "") or "").strip()
        if not asset_id or not credential:
            continue
        if credential in allowed.get(asset_id, set()):
            out[asset_id] = credential
    return out


async def _classify_assets(store: CaseStorage, refs: list[tuple[str, str]]) -> dict[str, int]:
    """归类指定资产（上传后的后台补跑用）：只走关键词，不调 LLM。

    LLM 归类留给面板上的「一键智能归类」（用户主动触发，可以等）。
    """
    stats = {"keyword": 0, "ai": 0, "pending": 0}
    for kind, asset_id in refs:
        row = store.get_image(asset_id) if kind == ASSET_IMAGE else store.get_file(asset_id)
        if row is None:
            continue
        nodes = (
            _credential_nodes_of(str(row.get("team_id", ""))).get(str(row.get("member_key", "")))
            or []
        )
        if not nodes or str(row.get("credential", "") or ""):
            continue
        hit = _match_credential(nodes, _asset_evidence(row, kind))
        if not hit:
            stats["pending"] += 1
            continue
        if kind == ASSET_IMAGE:
            store.update_image(asset_id, credential=hit)
        else:
            store.update_file(asset_id, credential=hit, classified_by="keyword")
        stats["keyword"] += 1
    return stats


async def _classify_case(store: CaseStorage, case_id: str, *, force: bool) -> dict[str, int]:
    """整个 Case 归类：先关键词落库，再一次 LLM 批量，剩下留人工。"""
    stats = {"keyword": 0, "ai": 0, "pending": 0}
    hits, groups = _classify_targets(store, case_id, force=force)
    for kind, row, _nodes in hits:
        asset_id = str(row.get("id", ""))
        if kind == ASSET_IMAGE:
            store.update_image(asset_id, credential=str(row.get("credential", "")))
        else:
            store.update_file(
                asset_id,
                credential=str(row.get("credential", "")),
                classified_by="keyword",
            )
        stats["keyword"] += 1
    if groups:
        assigned = await _classify_by_llm(groups)
        for asset_id, credential in assigned.items():
            if _asset_kind(asset_id) == ASSET_IMAGE:
                store.update_image(asset_id, credential=credential)
            else:
                store.update_file(asset_id, credential=credential, classified_by="ai")
        stats["ai"] = len(assigned)
        stats["pending"] = sum(len(g["items"]) for g in groups) - len(assigned)
    return stats


class CredentialAssignRequest(BaseModel):
    """手动挂载凭证节点（拖动 / 下拉改归属）。"""

    credential: str = Field(default="", max_length=64)


class CaseClassifyRequest(BaseModel):
    """一键智能归类 body。"""

    case_id: str = Field(min_length=1, max_length=512)
    team_ids: list[str] = Field(default_factory=list)
    force: bool = False


@router.get("/case/credentials")
async def case_credentials(
    case_id: str = Query(..., min_length=1),
    team_ids: str = Query(""),
) -> dict:
    """凭证预览聚合：该业务需要哪几张凭证、实传了什么、要素采到了哪里。"""
    store = _get_case_storage()
    ids = [t.strip() for t in team_ids.split(",") if t.strip()]
    return _build_credential_view(store, case_id, ids)


@router.post("/case/classify-credentials")
async def case_classify_credentials(req: CaseClassifyRequest) -> dict:
    """做一次凭证归类并自动挂载（关键词 → LLM → 剩下的留给人工拖动）。"""
    store = _get_case_storage()
    stats = await _classify_case(store, req.case_id, force=req.force)
    await _audit(
        "ops_case_classify_credentials", {"case_id": req.case_id, **stats, "force": req.force}
    )
    # classify_stats 单独命名：不跟聚合视图里的 stats（齐备度）撞名
    return {
        "ok": True,
        "classify_stats": stats,
        **_build_credential_view(store, req.case_id, req.team_ids),
    }


@router.put("/case/credential-asset/{asset_id}")
async def credential_asset_assign(asset_id: str, req: CredentialAssignRequest) -> dict:
    """手动改归属：只接受该成员预设节点名，空串 = 退回「未归属」。"""
    store = _get_case_storage()
    kind = _asset_kind(asset_id)
    row = store.get_image(asset_id) if kind == ASSET_IMAGE else store.get_file(asset_id)
    if row is None:
        raise HTTPException(404, f"凭证资产 {asset_id} 不存在")
    member_key = str(row.get("member_key", "") or "")
    nodes = _credential_nodes_of(str(row.get("team_id", ""))).get(member_key) or []
    name = req.credential.strip()
    known = {str(n.get("name", "")) for n in nodes}
    if name and known and name not in known:
        raise HTTPException(400, f"「{name}」不是专家「{member_key}」的预设凭证节点")
    if kind == ASSET_IMAGE:
        updated = store.update_image(asset_id, credential=name)
    else:
        updated = store.update_file(asset_id, credential=name, classified_by="manual")
    await _audit(
        "ops_case_credential_assign",
        {"asset_id": asset_id, "kind": kind, "case_id": row.get("case_id"), "credential": name},
    )
    return {"ok": True, "asset": _asset_brief(updated or {}, kind)}


@router.post("/case/credential-asset/{asset_id}/recognize")
async def credential_asset_recognize(asset_id: str) -> dict:
    """「立即识别影像」：同步跑端侧 OCR，返回完整元数据（含块坐标）。"""
    store = _get_case_storage()
    kind = _asset_kind(asset_id)
    row = store.get_image(asset_id) if kind == ASSET_IMAGE else store.get_file(asset_id)
    if row is None:
        raise HTTPException(404, f"凭证资产 {asset_id} 不存在")
    path = str(row.get("file_path", "") or "")
    if not Path(path).is_file():
        raise HTTPException(404, "影像文件已不存在（可能被清理）")
    if not is_image_file(str(row.get("file_name", ""))):
        raise HTTPException(400, "该凭证不是图片，无法做影像区域定位（可看文本要素）")
    meta = await _recognize_asset(store, asset_id, kind, path)
    if meta.get("engine") == "none":
        raise HTTPException(
            422,
            str(meta.get("error") or "")
            or "端侧 OCR 不可用：请确认已安装 rapidocr_onnxruntime，或换一张更清晰的图",
        )
    await _audit(
        "ops_case_credential_recognize",
        {"asset_id": asset_id, "kind": kind, "blocks": len(meta.get("blocks") or [])},
    )
    return {"ok": True, "asset_id": asset_id, "ocr": meta}


@router.get("/case/credential-asset/{asset_id}")
async def credential_asset_detail(asset_id: str) -> dict:
    """面板选中某个凭证资产：一次返回图片字节 + 影像元数据（blocks）。"""
    store = _get_case_storage()
    kind = _asset_kind(asset_id)
    row = store.get_image(asset_id) if kind == ASSET_IMAGE else store.get_file(asset_id)
    if row is None:
        raise HTTPException(404, f"凭证资产 {asset_id} 不存在")
    path = Path(str(row.get("file_path", "") or ""))
    if not path.is_file():
        raise HTTPException(404, "文件已不存在（可能被清理）")
    file_name = str(row.get("file_name", "") or "")
    raw = path.read_bytes()
    return {
        "kind": kind,
        "id": asset_id,
        "file_name": file_name,
        "mime": _mime_of(file_name),
        "content_base64": base64.b64encode(raw).decode("ascii"),
        "ocr": _load_ocr_meta(row.get("ocr_meta")),
    }


@router.delete("/case/credential-asset/{asset_id}")
async def credential_asset_delete(asset_id: str) -> dict:
    """删除凭证资产：CF-* 走材料删除，CI-* 只删识别底图（不动表单填写值）。"""
    store = _get_case_storage()
    kind = _asset_kind(asset_id)
    if kind == ASSET_IMAGE:
        removed = store.delete_image(asset_id)
        if removed is None:
            raise HTTPException(404, f"识别底图 {asset_id} 不存在")
    else:
        removed = store.delete_file(asset_id)
        if removed is None:
            raise HTTPException(404, f"材料 {asset_id} 不存在")
    await _audit(
        "ops_case_credential_delete",
        {"asset_id": asset_id, "kind": kind, "case_id": removed.get("case_id")},
    )
    return {"ok": True, "asset_id": asset_id, "kind": kind}


# ---------------------------------------------------------------------------
# 业务办理台账（业务汇总，2026-11-09）
#
# 与「业务记录卡片」（business_records）职责不同：
#   - business_records：业务**办完之后** AI 生成的小结（一条一笔，导出时插入）；
#   - business_tasks：业务**发起即建**、状态随办理流转的台账（一条业务一行）。
#
# 四态与触发点：
#   in_progress   正在办理 —— 上传第一份材料建档（默认）
#   suspended     挂起     —— 客户经理人工标记（界面只读冻结，恢复后接着办）
#   manual_review 人工确认 —— 审核异常（打回 / 超时 / 模型不可用）自动转入
#   closed        结束     —— 全部验收通过并导出交付物后自动转入
# ---------------------------------------------------------------------------


class TaskStartRequest(BaseModel):
    """建档请求（前端在上传第一份材料时调用；幂等）。"""

    case_id: str = Field(min_length=1, max_length=512)
    project_name: str = ""
    feature_id: str = ""
    feature_label: str = ""
    category: str = ""
    category_label: str = ""
    title: str = ""
    operator: str = ""


class TaskStatusRequest(BaseModel):
    """人工状态操作：suspend 挂起 / resume 恢复办理。"""

    action: str = Field(min_length=1, max_length=32)
    note: str = ""


def _pending_items(files: list[dict]) -> list[dict[str, Any]]:
    """待人工接入项：被打回的材料 + 审核异常（超时 / 模型不可用）落回待审的材料。"""
    items: list[dict[str, Any]] = []
    for f in files:
        status = str(f.get("status", ""))
        note = str(f.get("review_note", "") or "")
        if status == FILE_REJECTED:
            items.append(
                {
                    "file_id": str(f.get("id", "")),
                    "file_name": str(f.get("file_name", "")),
                    "member_key": str(f.get("member_key", "")),
                    "kind": "rejected",
                    "note": note or "专家打回，需人工处理",
                }
            )
        elif status == FILE_PENDING and note:
            items.append(
                {
                    "file_id": str(f.get("id", "")),
                    "file_name": str(f.get("file_name", "")),
                    "member_key": str(f.get("member_key", "")),
                    "kind": "review_error",
                    "note": note,
                }
            )
    return items


def _ensure_task_for_case(case_id: str) -> BusinessTask | None:
    """建档（幂等）：已有台账则只补标签，没有则建一行。

    已结束的台账又收到新材料 → 重新进入「正在办理」（客户经理补件后接着办）。
    """
    if not case_id:
        return None
    tasks = _get_task_storage()
    task = tasks.get_by_case(case_id)
    if task is not None:
        if task.status == TASK_CLOSED:
            return tasks.update(
                task.id,
                status=TASK_IN_PROGRESS,
                status_note="",
                pending_items=[],
                clear_closed=True,
            )
        return task
    return tasks.ensure(case_id=case_id)


def _sync_task_status(case_id: str, *, store: CaseStorage | None = None) -> BusinessTask | None:
    """按 Case 当前材料状态重算台账状态（挂起 / 已结束不动）。"""
    if not case_id:
        return None
    tasks = _get_task_storage()
    task = tasks.get_by_case(case_id)
    if task is None:
        return None
    if task.status in (TASK_SUSPENDED, TASK_CLOSED):
        return task
    case_store = store or _get_case_storage()
    files = case_store.list_files(case_id)
    items = _pending_items(files)
    status = TASK_MANUAL_REVIEW if items else TASK_IN_PROGRESS
    note = ""
    if items:
        names = "；".join(f"{i.get('file_name', '')}" for i in items[:3])
        note = f"{len(items)} 项待人工处理：{names}"
    return tasks.update(task.id, status=status, status_note=note, pending_items=items)


def _close_task_for_case(case_id: str, *, note: str = "") -> BusinessTask | None:
    """业务结束：导出交付物后自动置「结束」。"""
    if not case_id:
        return None
    tasks = _get_task_storage()
    task = tasks.get_by_case(case_id)
    if task is None:
        return None
    if task.status == TASK_SUSPENDED:
        # 挂起期间不自动改状态：客户经理先恢复或直接手动结束
        return task
    return tasks.set_closed(task.id, note=note)


@router.post("/tasks/start")
async def task_start(req: TaskStartRequest) -> dict[str, Any]:
    """业务发起建档（幂等）：上传第一份材料时调用，带业务大类 / 小类标签。"""
    tasks = _get_task_storage()
    task = tasks.ensure(
        case_id=req.case_id,
        project_name=req.project_name,
        feature_id=req.feature_id,
        feature_label=req.feature_label,
        category=req.category,
        category_label=req.category_label,
        title=req.title,
        operator=req.operator,
    )
    await _audit(
        "ops_task_start",
        {"task_id": task.id, "case_id": task.case_id, "feature_id": task.feature_id},
    )
    return task.to_dict()


@router.get("/tasks")
async def list_tasks(
    status: str | None = Query(None),
    category: str | None = Query(None),
    feature_id: str | None = Query(None),
    project_name: str | None = Query(None),
    case_id: str | None = Query(None),
    date_from: int | None = Query(None),
    date_to: int | None = Query(None),
    keyword: str | None = Query(None),
    limit: int = Query(200),
) -> dict[str, Any]:
    """业务台账查询（业务汇总）：多维度筛选 + 各状态计数。"""
    tasks = _get_task_storage()
    statuses = [s.strip() for s in status.split(",") if s.strip()] if status else None
    rows = tasks.list_tasks(
        statuses=statuses,
        limit=limit,
        category=category,
        feature_id=feature_id,
        project_name=project_name,
        case_id=case_id,
        date_from=date_from,
        date_to=date_to,
        keyword=keyword,
    )
    counts = tasks.counts(
        category=category,
        feature_id=feature_id,
        project_name=project_name,
        case_id=case_id,
        date_from=date_from,
        date_to=date_to,
        keyword=keyword,
    )
    return {
        "tasks": [r.to_dict() for r in rows],
        "total": len(rows),
        "counts": counts,
        "status_labels": dict(TASK_STATUS_LABEL),
    }


@router.get("/tasks/{task_id}")
async def get_task(task_id: str) -> dict[str, Any]:
    """台账详情：待处理项 + 材料进度 + 关联的业务小结。"""
    tasks = _get_task_storage()
    task = tasks.get(task_id)
    if task is None:
        raise HTTPException(404, f"task {task_id} not found")
    store = _get_case_storage()
    files = store.list_files(task.case_id) if task.case_id else []
    file_stats: dict[str, int] = {}
    for f in files:
        key = str(f.get("status", ""))
        file_stats[key] = file_stats.get(key, 0) + 1
    record = None
    if task.record_id:
        rec = _get_storage().get(task.record_id)
        record = rec.to_dict() if rec else None
    # 凭证摘要（凭证预览，2026-11-09）：汇总页明细行不拉全量也能看到缺哪几张；
    # 失败降级为空摘要，不能拖死台账详情。
    credentials: dict[str, Any] = {}
    if task.case_id:
        try:
            credentials = _build_credential_view(store, task.case_id, [])["stats"]
        except Exception as exc:  # pragma: no cover - 聚合异常不影响台账主链路
            logger.warning("[ops-task] 凭证摘要失败 case=%s: %s", task.case_id, exc)
    return {
        "task": task.to_dict(),
        "pending_items": task.pending_items,
        "file_stats": file_stats,
        "file_total": len(files),
        "record": record,
        "credentials": credentials,
    }


@router.post("/tasks/{task_id}/status")
async def set_task_status(task_id: str, req: TaskStatusRequest) -> dict[str, Any]:
    """人工状态操作：suspend 挂起（界面冻结） / resume 恢复办理。"""
    tasks = _get_task_storage()
    task = tasks.get(task_id)
    if task is None:
        raise HTTPException(404, f"task {task_id} not found")
    action = req.action.strip().lower()
    updated: BusinessTask | None = None
    if action == "suspend":
        if task.status == TASK_CLOSED:
            raise HTTPException(400, "业务已结束，无需挂起")
        updated = tasks.set_suspended(task_id, note=req.note or "客户经理手动挂起")
    elif action == "resume":
        tasks.update(
            task_id,
            status=TASK_IN_PROGRESS,
            status_note="",
            pending_items=[],
            clear_suspended=True,
        )
        updated = _sync_task_status(task.case_id)
    else:
        raise HTTPException(400, f"invalid action: {req.action}")
    await _audit("ops_task_status", {"task_id": task_id, "action": action})
    return (updated or task).to_dict()
