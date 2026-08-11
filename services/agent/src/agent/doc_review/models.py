"""文档风险合规审核 —— 数据模型。"""

from __future__ import annotations

import uuid
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field, field_validator


class DocFormat(str, Enum):
    PDF = "pdf"
    DOCX = "docx"
    # Word 97-2003 旧格式：解析时经 Word/WPS COM 转 docx（Windows）
    DOC = "doc"
    TXT = "txt"  # txt / md / csv 统一按纯文本处理
    MD = "md"
    HTML = "html"
    XLSX = "xlsx"
    PPTX = "pptx"


class DocCategory(str, Enum):
    CONTRACT = "contract"
    INTERNAL_POLICY = "internal_policy"
    ANNOUNCEMENT = "announcement"
    BIDDING = "bidding"
    OTHER = "other"


class RiskType(str, Enum):
    COMPLIANCE = "compliance"
    LEGAL = "legal"
    DATA_SECURITY = "data_security"
    FINANCIAL = "financial"


class RiskLevel(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class RunStatus(str, Enum):
    QUEUED = "queued"
    CLASSIFYING = "classifying"
    ANALYZING = "analyzing"
    DONE = "done"
    FAILED = "failed"


RISK_ORDER: dict[str, int] = {"low": 0, "medium": 1, "high": 2, "critical": 3}


def generate_id() -> str:
    return uuid.uuid4().hex


class Block(BaseModel):
    block_id: str
    text: str
    start: int = Field(ge=0)
    end: int = Field(ge=0)


class Page(BaseModel):
    page_no: int = Field(ge=1)
    blocks: list[Block] = Field(default_factory=list)


class ParsedDocument(BaseModel):
    doc_id: str
    file_name: str
    file_path: str
    format: DocFormat
    page_count: int = Field(ge=1)
    pages: list[Page]
    full_text: str


class Position(BaseModel):
    page_no: int = Field(ge=1)
    block_id: str
    start: int = Field(ge=0)
    end: int = Field(ge=0)


def _is_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _normalize_confidence(value: Any) -> dict[str, float]:
    """宽容归一化模型输出的 confidence。

    兼容三种形态（本地模型输出不稳定，均不报错）：
    1. 扁平 {"compliance": 0.97, ...} → 原样保留
    2. 嵌套 {"risk_types": {"compliance": 0.97, ...}} → 递归提取最深层数值 dict
    3. 混合 {"overall": 0.9, "risk_types": {...}} → 数值项保留，嵌套项展开合并
    """
    if not isinstance(value, dict):
        return {}
    flat: dict[str, float] = {}
    for key, item in value.items():
        if _is_number(item):
            flat[key] = float(item)
        elif isinstance(item, dict):
            for k, v in _normalize_confidence(item).items():
                flat.setdefault(k, v)
    return flat


class ClassificationResult(BaseModel):
    doc_category: DocCategory
    risk_types: list[RiskType]
    reason: str = ""
    confidence: dict[str, float] = Field(default_factory=dict)

    @field_validator("confidence", mode="before")
    @classmethod
    def _flatten_confidence(cls, value: Any) -> Any:
        # 模型输出结构不稳定：扁平/嵌套/混合都要能解析，绝不因结构偏差报错
        if isinstance(value, dict):
            return _normalize_confidence(value)
        return {} if value is not None else value


class Finding(BaseModel):
    finding_id: str
    risk_type: RiskType
    risk_level: RiskLevel
    title: str = Field(min_length=1)
    description: str = ""
    suggestion: str = ""
    rule_ref: str | None = None
    evidence_text: str = ""
    positions: list[Position] = Field(default_factory=list)


class AnalysisResult(BaseModel):
    doc_id: str
    doc_category: DocCategory
    risk_types: list[RiskType]
    overall_risk_level: RiskLevel
    summary: str = ""
    findings: list[Finding]
    model: dict[str, Any] = Field(default_factory=dict)
    created_at: str = ""
