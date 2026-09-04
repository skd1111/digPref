"""knowledge.tokenizer —— 中文分词（喂 SQLite FTS5 原生 BM25）。

FTS5 默认 unicode61 分词器把连续 CJK 串当作单个 token，直接索引中文会导致
「违约金上限」整串成一个 token、MATCH '违约金' 命不中。因此索引期与查询期都
先用 jieba 切词、空格拼接后再交给 FTS5，让每个词成为独立 token。

红线：jieba 缺失（未打包/导入失败）时退化 CJK bigram 分词，BM25 通道仍可用，
功能不中断（与 sqlite-vec 缺失退化纯文本同思路）。
"""

from __future__ import annotations

import logging
import re
from typing import Any

logger = logging.getLogger("agent.knowledge.tokenizer")

_JIEBA: Any = None
_JIEBA_TRIED = False

# bigram 兜底用：CJK 连续段 / 英数（含 GB/T、22239 类条款编号）
_TOKEN_RE = re.compile(r"[\u4e00-\u9fff]+|[A-Za-z0-9][A-Za-z0-9._/\-]*")
_CJK_FULL = re.compile(r"[\u4e00-\u9fff]+")


def _get_jieba() -> Any:
    """懒加载 jieba 单例；不可用返 None（退化 bigram）。"""
    global _JIEBA, _JIEBA_TRIED
    if not _JIEBA_TRIED:
        _JIEBA_TRIED = True
        try:
            import jieba  # type: ignore[import-untyped]

            try:
                jieba.setLogLevel(logging.ERROR)  # 静默「Building prefix dict」噪音
            except Exception:  # pragma: no cover - 老版本无该 API
                pass
            _JIEBA = jieba
        except Exception as exc:  # pragma: no cover - 打包缺失时才走
            logger.warning("jieba unavailable, fallback to CJK bigram: %s", exc)
            _JIEBA = None
    return _JIEBA


def _bigram_fallback(text: str) -> list[str]:
    toks: list[str] = []
    for m in _TOKEN_RE.finditer(text):
        s = m.group(0)
        if _CJK_FULL.fullmatch(s):
            if len(s) == 1:
                toks.append(s)
            else:
                toks.extend(s[i : i + 2] for i in range(len(s) - 1))
        else:
            toks.append(s.lower())
    return toks


def tokenize(text: str) -> list[str]:
    """文本 -> 词元列表（jieba 搜索引擎模式；缺失退化 bigram）。"""
    if not text:
        return []
    jb = _get_jieba()
    if jb is not None:
        try:
            return [str(w).strip().lower() for w in jb.cut_for_search(text) if str(w).strip()]
        except Exception as exc:  # pragma: no cover - jieba 内部异常兜底
            logger.debug("jieba cut failed, fallback bigram: %s", exc)
    return _bigram_fallback(text)


def tokens_to_fts(text: str) -> str:
    """文本 -> FTS5 索引串（空格分隔的词元）。"""
    return " ".join(tokenize(text))


def build_match_query(text: str) -> str:
    """文本 -> FTS5 MATCH 表达式（各词元加引号 OR 连接，保召回，bm25 负责排序）。

    返回空串表示无有效检索词（全为分隔符/停用符），调用方应跳过 BM25 通道。
    """
    toks: list[str] = []
    seen: set[str] = set()
    for t in tokenize(text):
        # 过滤纯标点/分隔符（FTS unicode61 会丢弃，避免空 MATCH 语法错误）
        if not re.search(r"[\w\u4e00-\u9fff]", t):
            continue
        if t in seen:
            continue
        seen.add(t)
        toks.append(t)
    if not toks:
        return ""
    return " OR ".join('"' + t.replace('"', '""') + '"' for t in toks)
