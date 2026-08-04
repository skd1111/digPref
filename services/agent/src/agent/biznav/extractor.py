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
import json
import logging
import re
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Optional

from .audit import EVT_FEATURE_EXTRACT
from .models import CandidateFileGroup, Feature, RelatedFile
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
            groups = self._scan_candidate_files()
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

        async def _run_one(g: CandidateFileGroup) -> Optional[Feature]:
            async with sem:
                try:
                    return await self._generate_feature_for_group(g)
                except Exception as e:
                    logger.warning("[biznav] group %s failed: %s", g.group_key, e)
                    result.errors.append(f"group {g.group_key}: {e}")
                    return None

        tasks = [asyncio.create_task(_run_one(g)) for g in groups]
        produced = await asyncio.gather(*tasks, return_exceptions=False)
        for f in produced:
            if f is None:
                continue
            try:
                if self._is_valid_feature(f):
                    self.storage.upsert(f)
                    result.features_generated += 1
                else:
                    result.errors.append(f"feature validation failed: {f.id}")
            except Exception as e:
                logger.warning("[biznav] upsert %s failed: %s", f.id, e)
                result.errors.append(f"upsert {f.id}: {e}")
            result.processed_files += 1

        # 4. 收尾
        self.storage.update_job(
            job_id,
            status="done" if not result.errors else "done",
            processed_files=result.processed_files,
            features_generated=result.features_generated,
            error_message=("; ".join(result.errors)) if result.errors else None,
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

    def _scan_candidate_files(self) -> list[CandidateFileGroup]:
        """读支持的文件后缀 → 走 project_root → 按启发式分组。

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
            if any(part in path.parts for part in ("node_modules", ".git", "dist", "build", "__pycache__", ".eaide")):
                continue
            try:
                rel = str(path.relative_to(root)).replace("\\", "/")
            except ValueError:
                rel = str(path)
            role = self._classify_role(rel)
            buckets.setdefault(role, []).append(rel)

        groups: list[CandidateFileGroup] = []
        for role, files in sorted(buckets.items()):
            # 每组最多 8 个文件（避免 LLM 上下文爆炸）
            groups.append(
                CandidateFileGroup(
                    group_key=f"{role}",
                    role=role,
                    files=sorted(files)[:8],
                )
            )
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
        user = (
            f"分析以下项目路径分组（role='{group.role}'），输出一组业务功能点 JSON。\n"
            f"【项目名】{self.project_name}\n"
            f"【文件清单】\n" + "\n\n".join(snippets) + "\n\n"
            "【输出格式】仅输出 JSON 数组，不要任何额外文字。\n"
            "每个元素字段：\n"
            '  id: "<group.role>-<类别>-<index>"（例如 "API 入口-认证-1"）\n'
            '  name: "<功能点名称>"（中文，< 30 字）\n'
            '  description: "<业务说明 >"（中文，1-2 句）\n'
            '  category: "<分类>"（路由 / 业务 / 数据 / 工具 四选一）\n'
            '  related_files: [{"path": "<相对路径>", "role": "<说明>"}]\n'
            '  related_apis: [{"method": "GET/POST/...", "path": "<API 路径>", "description": ""}]（如无则空数组）\n'
            '  related_tables: [{"name": "<表名>", "description": ""}]（如无则空数组）\n'
            '  business_rules: [{"text": "<单条规则>", "structured": null}]（如无则空数组）\n'
            "若无法识别出有价值的功能点，返回空数组 []。"
        )
        return [
            {
                "role": "system",
                "content": "你是 EAIDE 业务功能点分析助手。根据代码文件识别业务功能点并输出 JSON。"
            },
            {"role": "user", "content": user},
        ]

    async def _generate_feature_for_group(self, group: CandidateFileGroup) -> Optional[Feature]:
        messages = self._build_llm_prompt(group)
        # 期望注入：llm_client 是 async callable，签名 (kind, messages) -> str
        text = await self.llm_client("biznav_extract", messages)
        if not text:
            return None
        data = self._parse_llm_json(text)
        if not isinstance(data, list):
            return None
        ts = now()
        # 取第一个有效元素（V1.1 一组 = 一个功能点）
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
                related_apis=[],
                related_tables=[],
                business_rules=[],
                source="ai",
                ai_confidence=0.7,
                version=1,
                created_at=ts,
                updated_at=ts,
                deleted_at=None,
            )
            return f
        return None

    @staticmethod
    def _parse_llm_json(response_text: str) -> Any:
        """处理 ```json ... ``` 围栏、嵌套 JSON、容错。"""
        if not response_text:
            return None
        text = response_text.strip()
        # 去掉 ```json 围栏
        fence = re.search(r"```(?:json)?\s*([\s\S]*?)```", text)
        if fence:
            text = fence.group(1).strip()
        # 截取第一个 [ 或 { 到末尾
        bracket = text.find("[")
        brace = text.find("{")
        if bracket == -1 and brace == -1:
            return None
        if bracket == -1:
            start = brace
        elif brace == -1:
            start = bracket
        else:
            start = min(bracket, brace)
        # 找对应的末尾（粗暴切到最后一个 } 或 ]）
        end_bracket = text.rfind("]")
        end_brace = text.rfind("}")
        end = max(end_bracket, end_brace)
        if end < start:
            return None
        text = text[start : end + 1]
        return _json_or_text(text, default=None)

    @staticmethod
    def _is_valid_feature(f: Feature) -> bool:
        return bool(f.id and f.name and f.project_name and f.category)


# ---------------------------------------------------------------------------
# 鲁棒 JSON 解析
# ---------------------------------------------------------------------------


def _json_or_text(text: str, default: Any = None) -> Any:
    try:
        return json.loads(text)
    except (ValueError, TypeError):
        return default
