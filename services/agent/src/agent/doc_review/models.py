"""文档风险合规审核 —— 数据模型。"""
from __future__ import annotations

import uuid
from enum import Enum

from pydantic import BaseModel, Field


class DocFormat(str, Enum):
    PDF = "pdf"
    DOCX = "docx"
    TXT = "txt"
    MD = "md"


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


class ClassificationResult(BaseModel):
    doc_category: DocCategory
    risk_types: list[RiskType]
    reason: str = ""
    confidence: dict[str, float] = Field(default_factory=dict)


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
    model: dict = Field(default_factory=dict)
    created_at: str = ""
