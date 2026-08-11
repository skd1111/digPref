"""doc_review · 知识库引用（grep 式关键词匹配）。

用途：审核产出的每条 Finding 附带知识库/案例库引用（KbCitation），
前端展示"为什么有风险"的依据。

设计（纯文本 grep，零外部依赖 / 零网络 IO）：
  - 知识库目录 = settings.doc_review_kb_dir（默认 knowledge-base/）
  - 风险类型 → 对应知识文件 + 案例库：
      compliance    → 01-合规风险.md     + 90-案例库.md
      legal         → 02-法律风险.md     + 90-案例库.md
      data_security → 03-数据安全风险.md + 90-案例库.md
      financial     → 04-资金风险.md     + 90-案例库.md
  - md 按标题（# ~ ####）切分为 section；section 文本预切 CJK 2-4 字 n-gram
  - Finding 的 title/description/evidence_text 抽 CJK n-gram 作查询词，
    按 tf-idf 加权对 section 打分，取 Top-N 并截取命中处上下文作 excerpt
  - 目录/文件 mtime 变化时自动重建缓存；目录不存在时返 []（best-effort）
"""

from __future__ import annotations

import logging
import math
import re
import sys
import time
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

from pydantic import BaseModel, Field

from agent.config import settings

logger = logging.getLogger(__name__)

# 风险类型 → 知识文件（案例库对所有类型都参与匹配，按 [风险维度] 标签自然过滤）
_RISK_KB_FILES: dict[str, list[str]] = {
    "compliance": ["01-合规风险.md", "90-案例库.md"],
    "legal": ["02-法律风险.md", "90-案例库.md"],
    "data_security": ["03-数据安全风险.md", "90-案例库.md"],
    "financial": ["04-资金风险.md", "90-案例库.md"],
}

_CASE_BOOK = "90-案例库.md"

_CJK_RE = re.compile(r"[\u4e00-\u9fff\u3400-\u4dbf]+")
_HEADING_RE = re.compile(r"^(#{1,4})\s+(.+?)\s*$")

_GRAM_SIZES = (4, 3, 2)

# 引用来源提取：优先用正文里真实的法规/案例名（《民法典》就写《民法典》），
# 不展示内部文件名（文件名编号对用户无意义且可能误导）
_LAW_NAME_RE = re.compile(r"《([^《》]{2,40})》")

# 未提取到法规名时的友好模块名兜底（同样不露文件名）
_KB_DISPLAY_NAMES: dict[str, str] = {
    "01-合规风险.md": "合规风险红线清单",
    "02-法律风险.md": "法律风险清单",
    "03-数据安全风险.md": "数据安全风险清单",
    "04-资金风险.md": "资金风险清单",
    "90-案例库.md": "风险案例库",
}


def _law_names_near(
    text: str, term: str, *, before: int = 60, after: int = 160, max_names: int = 2
) -> list[str]:
    """从命中词附近正文提取法规/文件名称（《...》），按出现顺序去重。

    窗口与 _excerpt 保持一致，保证提取出的法规名在展示的摘录里看得见。
    """
    pos = text.find(term)
    if pos < 0:
        pos = 0
    start = max(0, pos - before)
    end = min(len(text), pos + len(term) + after)
    names: list[str] = []
    for m in _LAW_NAME_RE.finditer(text, start, end):
        name = m.group(1).strip()
        if name and name not in names:
            names.append(name)
        if len(names) >= max_names:
            break
    return names


class KbCitation(BaseModel):
    """单条知识库引用（镜像 shared-protocol DocKbRef）。"""

    source: str
    heading: str
    excerpt: str = ""
    matched_terms: list[str] = Field(default_factory=list)


@dataclass
class _KbSection:
    file: str
    heading: str  # 标题路径，如 "一、合同格式条款 > 1.2 条款红线清单"
    text: str
    grams: Counter[str] = field(default_factory=Counter)


def _resolve_kb_dir(kb_dir: str | None) -> Path:
    """知识库目录解析：显式参数 > cwd 配置路径 > PyInstaller _MEIPASS 内置副本。

    打包后 exe 可能在任意工作目录启动，spec datas 已将 knowledge-base
    打进 _MEIPASS；cwd 下配置路径不存在时回退到内置副本。
    """
    if kb_dir:
        return Path(kb_dir)
    base = Path(settings.doc_review_kb_dir)
    if base.is_dir():
        return base
    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        bundled = Path(meipass) / settings.doc_review_kb_dir
        if bundled.is_dir():
            return bundled
    return base


def _cjk_grams(text: str, *, max_terms: int = 240) -> list[str]:
    """抽 CJK 连续段的 4/3/2 字 n-gram（长 gram 优先，去重保序）。"""
    grams: dict[str, None] = {}
    for run in _CJK_RE.findall(text):
        for n in _GRAM_SIZES:
            if len(run) < n:
                continue
            for i in range(len(run) - n + 1):
                grams.setdefault(run[i : i + n], None)
                if len(grams) >= max_terms:
                    return list(grams)
    return list(grams)


def _split_sections(file_name: str, text: str) -> list[_KbSection]:
    """按 markdown 标题切分；维护 ## / ### / #### 标题路径。"""
    sections: list[_KbSection] = []
    stack: list[tuple[int, str]] = []
    buf: list[str] = []

    def flush() -> None:
        body = "\n".join(buf).strip()
        if body and stack:
            heading = " > ".join(title for _, title in stack)
            sections.append(
                _KbSection(
                    file=file_name,
                    heading=heading,
                    text=body,
                    grams=Counter(_cjk_grams(body, max_terms=10_000)),
                )
            )
        buf.clear()

    for line in text.splitlines():
        m = _HEADING_RE.match(line)
        if m:
            flush()
            level, title = len(m.group(1)), m.group(2)
            # 一级标题 = 文件标题（与文件名重复），仅作分界不入路径
            if level == 1:
                stack.clear()
                continue
            while stack and stack[-1][0] >= level:
                stack.pop()
            stack.append((level, title))
        else:
            buf.append(line)
    flush()
    return sections


# ---- 模块级缓存（(dir, 文件集签名) → {file: sections}）----
# 注意：不同风险类型的文件组合不同 → 签名不同，若只留最新会导致缓存
# 反复失效重建；改为多键共存 + 上限淘汰（LRU 简化版）

_CACHE: dict[tuple[str, str], dict[str, list[_KbSection]]] = {}
_CACHE_MAX_ENTRIES = 8


def _dir_signature(kb_dir: Path, files: list[str]) -> str | None:
    parts: list[str] = []
    for name in files:
        p = kb_dir / name
        if not p.is_file():
            continue
        st = p.stat()
        parts.append(f"{name}:{st.st_mtime_ns}:{st.st_size}")
    return "|".join(parts) if parts else None


def _load_sections(kb_dir: Path, files: list[str]) -> dict[str, list[_KbSection]]:
    sig = _dir_signature(kb_dir, files)
    if sig is None:
        return {}
    key = (str(kb_dir), sig)
    cached = _CACHE.get(key)
    if cached is not None:
        return cached
    # 缓存未命中：读文件 + 切 section + n-gram 预算（grep 式匹配的真正开销）
    t0 = time.perf_counter()
    out: dict[str, list[_KbSection]] = {}
    for name in files:
        p = kb_dir / name
        if not p.is_file():
            continue
        try:
            out[name] = _split_sections(name, p.read_text(encoding="utf-8"))
        except Exception as exc:
            logger.warning("doc_review kb load failed file=%s: %s", name, exc)
    n_sections = sum(len(v) for v in out.values())
    logger.info(
        "doc_review kb cache rebuilt dir=%s files=%d sections=%d elapsed=%.1fms",
        kb_dir,
        len(out),
        n_sections,
        (time.perf_counter() - t0) * 1000,
    )
    # 超上限淘汰最早插入的条目（其他风险类型的文件组合缓存保留）
    if len(_CACHE) >= _CACHE_MAX_ENTRIES:
        _CACHE.pop(next(iter(_CACHE)), None)
    _CACHE[key] = out
    return out


def _clean_excerpt(snippet: str) -> str:
    """清洗摘录：知识库多为 markdown 表格，列分隔竖线换成中文分号更可读。"""
    snippet = snippet.replace("\n", " ")
    # 连续竖线（|| / |||）合并，再把单个竖线替换为分号（列边界→语义分隔）
    snippet = re.sub(r"\|{2,}", "|", snippet)
    snippet = snippet.replace("|", "；")
    snippet = re.sub(r"(；\s*){2,}", "；", snippet)  # 含空格的连续分号（空表格列）合并
    snippet = snippet.strip(" ；\t")
    snippet = re.sub(r"\s{2,}", " ", snippet)
    return snippet


def _excerpt(section_text: str, term: str, *, before: int = 60, after: int = 160) -> str:
    pos = section_text.find(term)
    if pos < 0:
        return _clean_excerpt(section_text[: after + before])
    start = max(0, pos - before)
    end = min(len(section_text), pos + len(term) + after)
    snippet = section_text[start:end]
    prefix = "…" if start > 0 else ""
    suffix = "…" if end < len(section_text) else ""
    return f"{prefix}{_clean_excerpt(snippet)}{suffix}"


def find_kb_refs(
    *,
    risk_type: str,
    title: str,
    description: str = "",
    evidence_text: str = "",
    kb_dir: str | None = None,
    max_refs: int = 3,
) -> list[KbCitation]:
    """为单条 Finding 匹配知识库引用（Top-N）。

    查询词取自 title + description + evidence_text；知识库缺失/损坏时返 []。
    带耗时日志：单条 >100ms 时 info 输出，定位 grep 是否为瓶颈。
    """
    t_start = time.perf_counter()
    base = _resolve_kb_dir(kb_dir)
    files = list(_RISK_KB_FILES.get(risk_type, []))
    if _CASE_BOOK not in files:
        files.append(_CASE_BOOK)
    try:
        sections_by_file = _load_sections(base, files)
    except Exception as exc:
        logger.warning("doc_review kb load failed dir=%s: %s", base, exc)
        return []
    sections = [s for secs in sections_by_file.values() for s in secs]
    if not sections:
        return []

    query_text = f"{title} {description} {evidence_text}"
    terms = _cjk_grams(query_text)
    if not terms:
        return []

    # idf：高频词（出现在大多数 section）降权，避免"合同"类词把所有 section 拉高分
    n_sections = len(sections)
    df: dict[str, int] = {}
    for t in terms:
        df[t] = sum(1 for s in sections if s.grams.get(t))

    scored: list[tuple[float, _KbSection, list[tuple[float, str]]]] = []
    for sec in sections:
        total = 0.0
        hits: list[tuple[float, str]] = []
        for t in terms:
            count = sec.grams.get(t, 0)
            if not count:
                continue
            weight = math.log2((n_sections + 1) / (df[t] + 1)) + 1.0
            contrib = count * weight * len(t)
            total += contrib
            hits.append((contrib, t))
        if total <= 0:
            continue
        hits.sort(key=lambda x: (-x[0], x[1]))
        scored.append((total, sec, hits))
    if not scored:
        return []
    scored.sort(key=lambda x: -x[0])

    citations: list[KbCitation] = []
    for score, sec, hits in scored[:max_refs]:
        # 命中过弱（仅单个低权 2 字词）视为噪声
        if score < 2.0 or not hits:
            continue
        matched = [t for _, t in hits[:6]]
        # 来源标签：在各命中词附近找真实法规名（如《民法典》），取最先找到的；
        # 命中词可能落在标题处（附近无法规名），逐个尝试；均无则用模块友好名。
        # 不暴露内部文件名
        law_names: list[str] = []
        for t in matched:
            law_names = _law_names_near(sec.text, t)
            if law_names:
                break
        source_label = (
            "、".join(f"《{n}》" for n in law_names)
            if law_names
            else _KB_DISPLAY_NAMES.get(sec.file, sec.file.removesuffix(".md"))
        )
        citations.append(
            KbCitation(
                source=source_label,
                heading=sec.heading,
                excerpt=_excerpt(sec.text, matched[0]),
                matched_terms=matched,
            )
        )
    elapsed_ms = (time.perf_counter() - t_start) * 1000
    if elapsed_ms > 100:
        logger.info(
            "doc_review kb match slow risk=%s title=%.20s sections=%d refs=%d elapsed=%.1fms",
            risk_type,
            title,
            n_sections,
            len(citations),
            elapsed_ms,
        )
    return citations
