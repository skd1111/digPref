"""biznav.extractor —— FeatureExtractor 4 阶段管道（Phase 2G V1.1）。

4 阶段：
    1. scan: 调 codenav.language_registry.get_supported_extensions() 过滤文件
    2. group: 启发式按目录名分类（API 入口 / 业务逻辑 / 数据库操作 / API 路由）
    3. LLM:   每组一次 LLM 调用（kind='biznav_extract'，红线强制走 Ollama）
    4. persist: storage.upsert + emit audit FEATURE_EXTRACT

设计要点：
- LLM client 必须在 __init__ 注入（TestMock）；不在模块顶层 import LMRouter
  避免循环依赖
- 并发用 asyncio.Semaphore(max_concurrent_llm_calls)
- 私有 helper：_build_llm_prompt / _parse_llm_json / _json_or_text
- 失败兜底：单组失败 → 记录 error_message，不中断整批
"""

from __future__ import annotations

import asyncio
import logging
import re
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from agent.llm.json_discipline import extract_json, parse_with_retry
from agent.llm.prompts import load_prompt, render_prompt

from .audit import EVT_FEATURE_EXTRACT
from .models import (
    BusinessRule,
    CandidateFileGroup,
    Feature,
    RelatedApi,
    RelatedFile,
    RelatedTable,
)
from .storage import FeatureStorage, now

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# 启发式分组规则 —— 路径 → role
# ---------------------------------------------------------------------------

# 顺序很重要：先匹配先赢
_GROUP_RULES: list[tuple[str, str]] = [
    (r"[/\\]controller[s]?[/\\]", "API 入口"),
    (r"[/\\]service[s]?[/\\]", "业务逻辑"),
    (r"[/\\]mapper[s]?[/\\]", "数据库操作"),
    (r"[/\\]repository[/\\]", "数据库操作"),
    (r"[/\\]pages[/\\]api[/\\]", "API 路由"),
    (r"[/\\]app[/\\]api[/\\]", "API 路由"),
    (r"[/\\]api[/\\]", "API 路由"),
    (r"[/\\]model[s]?[/\\]", "数据模型"),
    (r"[/\\]entity[/\\]", "数据模型"),
    (r"[/\\]component[s]?[/\\]", "前端组件"),
    (r"[/\\]view[s]?[/\\]", "前端视图"),
    (r"[/\\]page[s]?[/\\]", "前端页面"),
    (r"[/\\]util[s]?[/\\]", "工具函数"),
    (r"[/\\]helper[s]?[/\\]", "工具函数"),
]

_DEFAULT_GROUP_ROLE = "其它"

# 每组最多几个文件（避免 LLM 上下文爆炸）；同 role 超过此数会切多个组
_FILES_PER_GROUP = 8
# 单次提取任务的组数安全上限：超大工程避免 LLM 调用数失控（按 role 字典序取前 N 组）
_MAX_GROUPS = 40


@dataclass
class ExtractionResult:
    """单次 extract_all() 调用结果。"""

    total_files: int = 0
    processed_files: int = 0
    features_generated: int = 0
    job_id: int = 0
    errors: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# LLM 抽象：不直接引用 LMRouter，调用方注入
# ---------------------------------------------------------------------------


class LLMClientLike:
    """extractor 需要的最小接口（Protocol 风格；不强制 isinstance）。

    实际注入 LMRouter.route_request 返回的对象（await 后 .content / .text）。
    V1.1 期望接口：
        await llm_client(messages: list[dict], kind: str) -> str
    或：
        llm_client 调用方传 (kind, messages) → 返回 single string
    """

    async def __call__(self, kind: str, messages: list[dict]) -> str:  # pragma: no cover
        raise NotImplementedError


# ---------------------------------------------------------------------------
# FeatureExtractor
# ---------------------------------------------------------------------------


class FeatureExtractor:
    """4 阶段业务功能点提取器。"""

    def __init__(
        self,
        storage: FeatureStorage,
        llm_client: Callable[..., Any],
        project_root: str,
        project_name: str,
        max_concurrent_llm_calls: int = 3,
        job_id: int | None = None,
    ):
        self.storage = storage
        self.llm_client = llm_client
        self.project_root = project_root
        self.project_name = project_name
        self.max_concurrent = max(1, int(max_concurrent_llm_calls))
        self._pre_created_job_id = job_id

    # ---- 公开 -----------------------------------------------------------

    async def extract_all(self) -> ExtractionResult:
        """完整 4 阶段。失败兜底：单组失败不中断，最后汇总。"""
        result = ExtractionResult()
        # 0. 写 job（若调用方已预创建则复用）
        try:
            if self._pre_created_job_id is not None:
                job_id = self._pre_created_job_id
            else:
                job_id = self.storage.create_job(self.project_name, self.project_root)
            result.job_id = job_id
        except Exception as e:
            logger.error("[biznav] create_job failed: %s", e)
            result.errors.append(f"create_job: {e}")
            return result

        # 1. scan
        try:
            groups = self._scan_candidate_files(result)
        except Exception as e:
            logger.error("[biznav] scan failed: %s", e)
            result.errors.append(f"scan: {e}")
            self.storage.update_job(job_id, status="failed", error_message=str(e), finished=True)
            return result

        total_files = sum(len(g.files) for g in groups)
        result.total_files = total_files
        self.storage.update_job(job_id, status="scanning", total_files=total_files)

        # 2/3. group + LLM（concurrent）
        self.storage.update_job(job_id, status="extracting")
        sem = asyncio.Semaphore(self.max_concurrent)

        # 进度增量：每组完成后累加其文件数并写回 job，
        # 前端轮询 processed_files / total_files 算百分比。
        progress_files = 0

        async def _run_one(g: CandidateFileGroup) -> list[Feature]:
            nonlocal progress_files
            try:
                async with sem:
                    return await self._generate_feature_for_group(g)
            except Exception as e:
                logger.warning("[biznav] group %s failed: %s", g.group_key, e)
                result.errors.append(f"group {g.group_key}: {e}")
                return []
            finally:
                progress_files += len(g.files)
                try:
                    self.storage.update_job(job_id, processed_files=progress_files)
                except Exception as e:
                    logger.warning("[biznav] progress update failed: %s", e)

        tasks = [asyncio.create_task(_run_one(g)) for g in groups]
        produced = await asyncio.gather(*tasks, return_exceptions=False)
        used_ids: set[str] = set()
        for feats in produced:
            for f in feats:
                if not self._is_valid_feature(f):
                    result.errors.append(f"feature validation failed: {f.id}")
                    continue
                # 跨组 id 去重：upsert 以 (id, project_name) 为主键，重名会互盖
                if f.id in used_ids:
                    f.id = f"{f.id}-{uuid.uuid4().hex[:6]}"
                used_ids.add(f.id)
                try:
                    self.storage.upsert(f)
                    result.features_generated += 1
                except Exception as e:
                    logger.warning("[biznav] upsert %s failed: %s", f.id, e)
                    result.errors.append(f"upsert {f.id}: {e}")
                result.processed_files += 1

        # 3.5 旧数据替换：本次有新产出时，软删除该项目上一次提取遗留的
        #     AI 功能点（不在 used_ids 里且 source='ai'）——重提取是「整批替换」
        #     语义，避免旧提示词产出的「工具/数据」等分类残留；手动维护的保留。
        if result.features_generated > 0:
            try:
                for old in self.storage.list_by_project(self.project_name):
                    if old.source == "ai" and old.id not in used_ids:
                        self.storage.soft_delete(old.id, self.project_name)
            except Exception as e:
                logger.warning("[biznav] stale AI features cleanup failed: %s", e)

        # 4. 收尾：扫到了文件却 0 产出 → 标 failed，前端才能把原因展示给用户
        # （此前静默 done + error_message=None → 界面只剩「暂无业务功能点」）。
        unique_errors = list(dict.fromkeys(result.errors))
        error_message = "; ".join(unique_errors) if unique_errors else None
        if result.features_generated == 0 and total_files > 0:
            final_status = "failed"
            if not error_message:
                error_message = "未能生成任何功能点（LLM 返回为空）"
        else:
            final_status = "done"
        self.storage.update_job(
            job_id,
            status=final_status,
            processed_files=progress_files,
            features_generated=result.features_generated,
            error_message=(error_message[:800] if error_message else None),
            finished=True,
        )

        # emit audit
        try:
            from agent.audit.store import audit  # 局部 import 避免 e2e 测试污染

            await audit(
                EVT_FEATURE_EXTRACT,
                {
                    "project_name": self.project_name,
                    "project_root": self.project_root,
                    "total_files": result.total_files,
                    "features_generated": result.features_generated,
                    "errors": result.errors[:10],
                },
            )
        except Exception as e:
            logger.warning("[biznav] audit emit failed: %s", e)

        return result

    # ---- 私有：扫描 ------------------------------------------------------

    def _scan_candidate_files(self, result: ExtractionResult) -> list[CandidateFileGroup]:
        """读支持的文件后缀 → 走 project_root → 按启发式分组。

        result 仅用于超大工程截断时留痕（errors）。
        任何 IO 异常（目录不存在/无权限） → 抛回 extract_all 兜底。
        """
        from agent.codenav import language_registry  # 局部 import，防止 codenav 缺失时 import 失败

        extensions = language_registry.get_supported_extensions()
        root = Path(self.project_root)
        if not root.exists():
            raise FileNotFoundError(f"project_root not found: {self.project_root}")

        buckets: dict[str, list[str]] = {}
        for path in root.rglob("*"):
            if not path.is_file():
                continue
            if path.suffix.lower() not in extensions:
                continue
            # 跳过常见排除目录
            if any(
                part in path.parts
                for part in ("node_modules", ".git", "dist", "build", "__pycache__", ".eaide")
            ):
                continue
            try:
                rel = str(path.relative_to(root)).replace("\\", "/")
            except ValueError:
                rel = str(path)
            role = self._classify_role(rel)
            buckets.setdefault(role, []).append(rel)

        groups: list[CandidateFileGroup] = []
        for role, files in sorted(buckets.items()):
            ordered = sorted(files)
            total_chunks = (len(ordered) + _FILES_PER_GROUP - 1) // _FILES_PER_GROUP
            # 每 _FILES_PER_GROUP 个文件切一组：大工程同一 role 切成多组，
            # 保证第 8 个之后的文件也能被分析（此前只取前 8 个，大项目覆盖极低）
            for idx in range(0, len(ordered), _FILES_PER_GROUP):
                chunk = ordered[idx : idx + _FILES_PER_GROUP]
                seq = idx // _FILES_PER_GROUP + 1
                key = f"{role}({seq}/{total_chunks})" if total_chunks > 1 else role
                groups.append(CandidateFileGroup(group_key=key, role=role, files=chunk))
        # 超大工程安全上限：超出时按 role 字典序保留前 _MAX_GROUPS 组并留痕
        if len(groups) > _MAX_GROUPS:
            result.errors.append(f"工程过大：共 {len(groups)} 组，仅分析前 {_MAX_GROUPS} 组")
            groups = groups[:_MAX_GROUPS]
        return groups

    @staticmethod
    def _classify_role(rel_path: str) -> str:
        for pattern, role in _GROUP_RULES:
            if re.search(pattern, rel_path):
                return role
        return _DEFAULT_GROUP_ROLE

    # ---- 私有：LLM -------------------------------------------------------

    def _build_llm_prompt(self, group: CandidateFileGroup) -> list[dict]:
        """生成 prompt messages。文本片段截断 2000 字符/文件。"""
        root = Path(self.project_root)
        snippets: list[str] = []
        for rel in group.files:
            p = root / rel
            try:
                content = p.read_text(encoding="utf-8", errors="ignore")
            except Exception:
                content = ""
            if len(content) > 2000:
                content = content[:2000] + "\n... (truncated)"
            snippets.append(f"// file: {rel}\n{content}")
        user = render_prompt(
            load_prompt("biznav/extract"),
            PROJECT_NAME=self.project_name,
            GROUP_ROLE=group.role,
            FILES="\n\n".join(snippets),
        )
        return [
            {
                "role": "system",
                "content": "你是 EAIDE 业务功能点分析助手。根据代码文件识别业务功能点并输出 JSON。",
            },
            {"role": "user", "content": user},
        ]

    async def _generate_feature_for_group(self, group: CandidateFileGroup) -> list[Feature]:
        base_messages = self._build_llm_prompt(group)

        async def _call(hint: str, last: str) -> str:
            messages = list(base_messages)
            if hint:
                messages = [*messages, {"role": "user", "content": hint}]
            return str(await self.llm_client("biznav_extract", messages))

        data = await parse_with_retry(_call, lambda t: extract_json(t, want="array"))
        if not isinstance(data, list):
            return []
        ts = now()
        out: list[Feature] = []
        # V1.2 (2026-08-05)：采纳 LLM 返回的全部有效元素（此前只取第一个，
        # 导致大工程功能点数量被人为卡死）；关联 API/表/规则也如实解析入库
        for item in data:
            if not isinstance(item, dict):
                continue
            fid = str(item.get("id") or "").strip()
            if not fid:
                fid = f"{group.group_key}-{uuid.uuid4().hex[:8]}"
            name = str(item.get("name") or "").strip() or fid
            f = Feature(
                id=fid,
                name=name,
                description=str(item.get("description") or ""),
                category=str(item.get("category") or "未分类"),
                project_name=self.project_name,
                project_root=self.project_root,
                related_files=[
                    RelatedFile(
                        path=str(rf.get("path", "")),
                        role=str(rf.get("role", "")),
                    )
                    for rf in (item.get("related_files") or [])
                    if isinstance(rf, dict) and rf.get("path")
                ],
                related_apis=[
                    RelatedApi(
                        method=str(a.get("method", "")),
                        path=str(a.get("path", "")),
                        description=str(a.get("description", "")),
                    )
                    for a in (item.get("related_apis") or [])
                    if isinstance(a, dict) and a.get("path")
                ],
                related_tables=[
                    RelatedTable(
                        name=str(t.get("name", "")),
                        description=str(t.get("description", "")),
                    )
                    for t in (item.get("related_tables") or [])
                    if isinstance(t, dict) and t.get("name")
                ],
                business_rules=[
                    BusinessRule(
                        text=str(r.get("text", "")),
                        structured=r.get("structured"),
                    )
                    for r in (item.get("business_rules") or [])
                    if isinstance(r, dict) and r.get("text")
                ],
                source="ai",
                ai_confidence=0.7,
                version=1,
                created_at=ts,
                updated_at=ts,
                deleted_at=None,
            )
            out.append(f)
        return out

    @staticmethod
    def _parse_llm_json(response_text: str) -> Any:
        """共享容错解析：处理围栏、think、前后缀（spec §4.5）。"""
        return extract_json(response_text, want="array")

    @staticmethod
    def _is_valid_feature(f: Feature) -> bool:
        return bool(f.id and f.name and f.project_name and f.category)
