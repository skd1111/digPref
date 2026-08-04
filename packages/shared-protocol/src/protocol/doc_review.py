"""文档风险合规审核契约 (Pydantic 镜像, 字段与 ts/doc-review.ts 一致)."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

DocFormat = Literal["pdf", "docx", "txt", "md"]
DocCategory = Literal["contract", "internal_policy", "announcement", "bidding", "other"]
DocRiskType = Literal["compliance", "legal", "data_security", "financial"]
DocRiskLevel = Literal["low", "medium", "high", "critical"]
DocRunStatus = Literal["queued", "classifying", "analyzing", "done", "failed"]


class DocBlock(BaseModel):
    block_id: str
    text: str
    start: int = Field(ge=0)
    end: int = Field(ge=0)


class DocPage(BaseModel):
    page_no: int = Field(ge=1)
    blocks: list[DocBlock]


class DocPosition(BaseModel):
    page_no: int = Field(ge=1)
    block_id: str
    start: int = Field(ge=0)
    end: int = Field(ge=0)


class DocFinding(BaseModel):
    finding_id: str
    risk_type: DocRiskType
    risk_level: DocRiskLevel
    title: str
    description: str = ""
    suggestion: str = ""
    rule_ref: str | None = None
    evidence_text: str = ""
    positions: list[DocPosition] = Field(default_factory=list)


class DocSummary(BaseModel):
    doc_id: str
    file_name: str
    format: DocFormat
    page_count: int = Field(ge=1)
    status: DocRunStatus | Literal["none"]
    overall_risk_level: DocRiskLevel | None = None
    created_at: str


class DocDetail(DocSummary):
    file_path: str
    doc_category: DocCategory | None = None
    risk_types: list[DocRiskType] = Field(default_factory=list)
    summary: str | None = None
    pages: list[DocPage] = Field(default_factory=list)
    findings: list[DocFinding] = Field(default_factory=list)
