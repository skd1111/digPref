"""doc_review · 财税规则库（RuleProvider V1 实现，混合检索）。

把 knowledge-base/fiscal-tax/ 下的财税法规/案例/准则素材加载为审核规则，
按文档内容相关性挑选后注入 analyze_*.yaml 的 {{rules}} 槽位，
让文档审核对财税场景给出有条款依据的判定（而不是纯靠模型常识）。
无需人工指定文档类型：检索得分决定规则是否注入。

设计（与 knowledge.py 同源：纯文本、零网络 IO，向量通道仅本地）：
  - 目录 = settings.doc_review_fiscal_dir（默认 knowledge-base/fiscal-tax）
  - 子目录 → 风险维度映射（test-scenarios 是场景脚本，不参与）：
      regulations         → compliance + financial
      cases               → compliance
      accounting-standards→ financial
      audit-standards     → compliance
      internal-control    → compliance
  - md 按 ## 章节切块；正文截断防止提示词膨胀
  - 混合检索：关键词（bigram 词元上的完整 BM25：TF 饱和 + IDF + 长度归一）
    + 语义（本地 embedding 余弦）加权；得分归一到 [0,1] 便于两通道混合；
    embedding 未配置/不可达时自动退化为纯关键词
  - 得分全低于阈值时返回 []，避免无关规则干扰模型
  - 文件 mtime/size 变化自动重建缓存（含 BM25 语料统计）；向量按目录签名懒加载缓存
"""

from __future__ import annotations

import logging
import math
import re
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from agent.config import settings
from agent.doc_review.models import RiskType
from agent.doc_review.rules import PolicyRule

logger = logging.getLogger(__name__)

# 子目录 → 该素材归属的风险维度
_DIR_RISK_MAP: dict[str, tuple[RiskType, ...]] = {
    "regulations": (RiskType.COMPLIANCE, RiskType.FINANCIAL),
    "cases": (RiskType.COMPLIANCE,),
    "accounting-standards": (RiskType.FINANCIAL,),
    "audit-standards": (RiskType.COMPLIANCE,),
    "internal-control": (RiskType.COMPLIANCE,),
}

# 单条规则正文截断长度（6 条 × 400 字 ≈ 2.4K，提示词体积可控）
_RULE_BODY_MAX = 400
# 相关性打分用的查询文本上限（超长文档取头部即可代表主题）
_QUERY_MAX_CHARS = 20000
# BM25 经典参数：k1 控制词频饱和速度，b 控制长度归一强度
_BM25_K1 = 1.2
_BM25_B = 0.75

_CJK_RE = re.compile(r"[\u4e00-\u9fff]+|[A-Za-z][A-Za-z0-9._-]+|\d+")


@dataclass
class _RuleCandidate:
    """一个候选规则（= 一份素材文件的一个 ## 章节）。"""

    rule_id: str
    source: str  # 展示用来源，如 "增值税法（含跨税种抵扣）-摘要"
    risk_types: tuple[RiskType, ...]
    body: str

    # 运行时派生：token 集合（快速判零）+ 词频表/长度（BM25 打分用）
    tokens: frozenset[str] = field(default_factory=frozenset)
    token_counts: Counter[str] = field(default_factory=Counter)
    doc_len: int = 0


@dataclass
class _CorpusStats:
    """BM25 语料统计：加载时一次性构建，随素材签名缓存失效。"""

    df: dict[str, int]  # token → 出现在多少个章节里
    n_docs: int
    avg_len: float


def _tokenize(text: str) -> list[str]:
    """CJK 双连 + 英文词/数字（与 audit_doc.py 同源，中文关键词友好）。"""
    tokens: list[str] = []
    for m in _CJK_RE.finditer(text):
        s = m.group(0)
        if len(s) < 2:
            continue
        if re.fullmatch(r"[\u4e00-\u9fff]+", s):
            tokens.extend(s[i : i + 2] for i in range(len(s) - 1))
        else:
            tokens.append(s)
    return tokens


def _extract_title(content: str, fallback: str) -> str:
    for line in content.split("\n"):
        if line.startswith("# ") and not line.startswith("## "):
            return line.lstrip("# ").strip()[:60]
    return fallback


def _split_sections(content: str) -> list[tuple[str, str]]:
    """按 ## 切章节，返回 (章节标题, 正文)；无章节时整篇作一段。"""
    pattern = re.compile(r"^## (.+)$", re.MULTILINE)
    matches = list(pattern.finditer(content))
    if not matches:
        return [("(全文)", content.strip())]
    out: list[tuple[str, str]] = []
    for idx, m in enumerate(matches):
        start = m.end()
        end = matches[idx + 1].start() if idx + 1 < len(matches) else len(content)
        body = content[start:end].strip()
        if body:
            out.append((m.group(1).strip(), body))
    return out


def _resolve_fiscal_dir() -> Path | None:
    """目录解析：cwd 配置路径 > PyInstaller _MEIPASS 内置副本 > 仓库根推导。

    开发态 Agent 不一定从仓库根启动（cwd 不可靠），相对路径找不到时
    再从模块自身位置向上推导仓库根；都不存在返 None。
    """
    import sys

    base = Path(settings.doc_review_fiscal_dir)
    if base.is_dir():
        return base
    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        bundled = Path(meipass) / settings.doc_review_fiscal_dir
        if bundled.is_dir():
            return bundled
    if not base.is_absolute():
        # services/agent/src/agent/doc_review/fiscal_rules.py → 仓库根 = parents[5]
        repo_root = Path(__file__).resolve().parents[5]
        derived = repo_root / settings.doc_review_fiscal_dir
        if derived.is_dir():
            return derived
    return None


def _dir_signature(base: Path) -> str | None:
    parts: list[str] = []
    for sub in sorted(_DIR_RISK_MAP):
        d = base / sub
        if not d.is_dir():
            continue
        for fp in sorted(d.glob("*.md")):
            st = fp.stat()
            parts.append(f"{fp.name}:{st.st_mtime_ns}:{st.st_size}")
    return "|".join(parts) if parts else None


_CACHE: dict[str, tuple[list[_RuleCandidate], _CorpusStats]] = {}
_CACHE_MAX_ENTRIES = 4


def _build_stats(candidates: list[_RuleCandidate]) -> _CorpusStats:
    """从候选列表构建 df / 文档数 / 平均长度（BM25 分母素材）。"""
    df: Counter[str] = Counter()
    total_len = 0
    for c in candidates:
        df.update(c.tokens)  # frozenset 迭代 = 每个 token 计一次（文档频率）
        total_len += c.doc_len
    return _CorpusStats(
        df=dict(df),
        n_docs=len(candidates),
        avg_len=(total_len / len(candidates)) if candidates else 1.0,
    )


def _load_corpus(
    fiscal_dir: Path | None = None,
) -> tuple[list[_RuleCandidate], _CorpusStats | None]:
    """加载全部候选规则 + BM25 语料统计（带 mtime 缓存）。目录缺失/为空返空。"""
    base = fiscal_dir or _resolve_fiscal_dir()
    if base is None:
        return [], None
    sig = _dir_signature(base)
    if sig is None:
        return [], None
    key = str(base)
    cached = _CACHE.get(key)
    if cached is not None and cached[0] and cached[0][0].rule_id.startswith(f"@{sig[:32]}@"):
        return cached

    candidates: list[_RuleCandidate] = []
    for sub, risk_types in _DIR_RISK_MAP.items():
        d = base / sub
        if not d.is_dir():
            continue
        for fp in sorted(d.glob("*.md")):
            try:
                content = fp.read_text(encoding="utf-8")
            except OSError as exc:
                logger.warning("doc_review fiscal rule load failed %s: %s", fp, exc)
                continue
            title = _extract_title(content, fp.stem)
            for sec_idx, (heading, body) in enumerate(_split_sections(content)):
                trimmed = body[:_RULE_BODY_MAX] + ("…" if len(body) > _RULE_BODY_MAX else "")
                cand = _RuleCandidate(
                    rule_id=f"@{sig[:32]}@{fp.stem}#{sec_idx}",
                    source=f"{title} · {heading}",
                    risk_types=risk_types,
                    body=trimmed,
                )
                counts = Counter(_tokenize(body))
                cand.tokens = frozenset(counts)
                cand.token_counts = counts
                cand.doc_len = sum(counts.values())
                candidates.append(cand)
    if len(_CACHE) >= _CACHE_MAX_ENTRIES:
        _CACHE.pop(next(iter(_CACHE)))
    entry = (candidates, _build_stats(candidates))
    _CACHE[key] = entry
    logger.info("doc_review fiscal rules loaded dir=%s candidates=%d", base, len(candidates))
    return entry


def load_candidates(fiscal_dir: Path | None = None) -> list[_RuleCandidate]:
    """加载全部候选规则（带 mtime 缓存）。目录缺失/为空返 []。"""
    return _load_corpus(fiscal_dir)[0]


# ============================================================
# 混合检索：关键词 + 语义向量（本地 embedding，不可用时退化）
# ============================================================

# 候选章节向量缓存：key = 目录路径，value = (目录签名, 与 candidates 等长的向量列表)
_VEC_CACHE: dict[str, tuple[str, list[list[float]]]] = {}


def _cosine(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    if na == 0 or nb == 0:
        return 0.0
    return dot / (na * nb)


def _bm25_scores(
    query_tokens: set[str],
    candidates: list[_RuleCandidate],
    stats: _CorpusStats,
) -> list[float]:
    """关键词通道：bigram 词元上的完整 BM25（TF 饱和 + IDF + 连续长度归一）。

    原始 BM25 无上界；这里除以单词贡献的理论上限
    idf·(k1+1)/(k1·(1-b))（词频→∞ 且文档极短时的极限）的查询词求和，
    保证得分 ∈ [0,1]，与语义余弦同量纲后才能做 (1-w)·kw + w·sem 加权混合。
    """
    if not query_tokens:
        return [0.0] * len(candidates)
    idf: dict[str, float] = {}
    for t in query_tokens:
        n_t = stats.df.get(t, 0)
        if n_t:
            idf[t] = math.log((stats.n_docs - n_t + 0.5) / (n_t + 0.5) + 1.0)
    max_possible = sum(idf.values()) * (_BM25_K1 + 1.0) / (_BM25_K1 * (1.0 - _BM25_B))
    if max_possible <= 0:
        return [0.0] * len(candidates)

    scores: list[float] = []
    for c in candidates:
        if not c.tokens:
            scores.append(0.0)
            continue
        s = 0.0
        for t, w_idf in idf.items():
            f = c.token_counts.get(t, 0)
            if not f:
                continue
            denom = f + _BM25_K1 * (1.0 - _BM25_B + _BM25_B * c.doc_len / stats.avg_len)
            s += w_idf * f * (_BM25_K1 + 1.0) / denom
        scores.append(s / max_possible)
    return scores


async def _semantic_scores(
    candidates: list[_RuleCandidate],
    query_text: str,
    client: Any,
    cache_key: str,
    sig: str,
) -> list[float] | None:
    """语义通道：本地 embedding 余弦相似度（截到 ≥0）；不可用返 None。

    返 None 表示语义通道缺席（未配置/不可达/零向量），调用方退化纯关键词。
    """
    if client is None:
        return None
    qvec = await client.embed(query_text)
    if not any(qvec):
        return None
    cached = _VEC_CACHE.get(cache_key)
    if cached is not None and cached[0] == sig:
        vectors = cached[1]
    else:
        vectors = await client.embed_batch([c.body for c in candidates])
        if not any(any(v) for v in vectors):
            return None  # 全部失败（服务掉线）→ 退化
        _VEC_CACHE[cache_key] = (sig, vectors)
    return [max(_cosine(qvec, v), 0.0) for v in vectors]


class FiscalTaxRuleProvider:
    """从财税法规素材库按混合检索挑选相关规则注入审核提示词。

    embedding 客户端可注入（测试替身）；缺省时走统一入口（进程内 ONNX 优先，
    显式配置走外置 HTTP），模型不可用时全程纯关键词。
    """

    def __init__(self, *, embedding: Any | None = None) -> None:
        self._embedding = embedding

    def _get_embedding_client(self) -> Any | None:
        if self._embedding is not None:
            return self._embedding
        from agent.llm.embedding import build_default_embedding_client

        self._embedding = build_default_embedding_client()
        return self._embedding

    async def search(self, sample_text: str) -> dict[RiskType, list[PolicyRule]]:
        """一次检索全部维度，按风险维度分组返回（类型自动判定的依据）。

        sample_text 为空 → 每个维度顺序取前 N 条兜底（CLI/直调场景）。
        """
        corpus, stats = _load_corpus()
        if not corpus or stats is None:
            return {}
        candidates = corpus
        max_rules = settings.doc_review_fiscal_max_rules
        if not sample_text:
            out: dict[RiskType, list[PolicyRule]] = {}
            for rt in RiskType:
                picked = [c for c in candidates if rt in c.risk_types][:max_rules]
                if picked:
                    out[rt] = [self._to_rule(c, rt) for c in picked]
            return out

        query = sample_text[:_QUERY_MAX_CHARS]
        query_tokens = set(_tokenize(query))
        kw = _bm25_scores(query_tokens, candidates, stats)
        client = self._get_embedding_client()
        # 目录签名（rule_id 内嵌）用作向量缓存键，素材变更自动失效
        sig = candidates[0].rule_id.split("@", 2)[1]
        sem = await _semantic_scores(candidates, query, client, cache_key=sig, sig=sig)

        w = settings.doc_review_fiscal_sem_weight
        min_score = settings.doc_review_fiscal_min_score
        sem_min = settings.doc_review_fiscal_sem_min
        scored: list[tuple[_RuleCandidate, float]] = []
        for c, k, s in zip(candidates, kw, sem if sem is not None else [0.0] * len(candidates)):
            score = ((1.0 - w) * k + w * s) if sem is not None else k
            # 关键词命中始终入选（不退化旧行为）；语义通道只负责扩召回：
            # 需同时过独立语义下限（挡无关文本基线余弦）与融合分阈值。
            sem_ok = sem is not None and s >= sem_min and score >= min_score
            if k > 0 or sem_ok:
                scored.append((c, score))
        scored.sort(key=lambda x: x[1], reverse=True)
        logger.info(
            "doc_review fiscal hybrid search query_chars=%d semantic=%s picked=%d",
            len(query),
            sem is not None,
            len(scored),
        )

        out = {}
        for rt in RiskType:
            picked = [c for c, _ in scored if rt in c.risk_types][:max_rules]
            if picked:
                out[rt] = [self._to_rule(c, rt) for c in picked]
        return out

    @staticmethod
    def _to_rule(c: _RuleCandidate, risk_type: RiskType) -> PolicyRule:
        return PolicyRule(
            rule_id=c.rule_id.split("@", 2)[-1],
            source=c.source,
            risk_type=risk_type,
            content=c.body,
        )

    async def get_rules(
        self, *, doc_category: str, risk_type: RiskType, sample_text: str = ""
    ) -> list[PolicyRule]:
        return (await self.search(sample_text)).get(risk_type, [])
