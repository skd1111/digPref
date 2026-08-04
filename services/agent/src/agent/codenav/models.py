"""Phase 2F 数据类（Symbol / JumpResult / IndexStatus）。"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional


@dataclass
class Symbol:
    name: str
    kind: str  # class | method | function | interface | field | enum
    file_path: str  # 绝对路径
    start_line: int
    end_line: int
    signature: Optional[str] = None
    parent_class: Optional[str] = None
    language: str = "unknown"


@dataclass
class JumpResult:
    file_path: str
    line: int
    confidence: float  # 1.0 = 本地索引命中, <1.0 = AI 推断
    source: str  # "local_index" | "not_found"  (V1 移除 ai_inference)
    note: Optional[str] = None


@dataclass
class IndexStatus:
    total_files: int
    total_symbols: int
    last_full_scan: Optional[float]  # timestamp
    last_incremental: Optional[float]
    is_scanning: bool

    def to_dict(self) -> dict:
        return {
            "total_files": self.total_files,
            "total_symbols": self.total_symbols,
            "last_full_scan": self.last_full_scan,
            "last_incremental": self.last_incremental,
            "is_scanning": self.is_scanning,
        }
