"""Phase 5 V2 · 简化版 —— 导入文档后直接审计。

对应 docs/CLAUDE.md 索引表「审核专家模式」（V2 简化路径）。

设计哲学：
  - **不是规则引擎、不写复杂 schema** —— 只做一件事：把 knowledge-base/fiscal-tax/ 下的 .md 法规文档
    导入到审核模型，作为知识库；然后审计任意业务问题：
      1. 加载 .md → 按 ## 章节切分
      2. 完整 BM25（TF 饱和 + IDF + 长度归一，零依赖内联实现）找最相关法规片段
      3. 把法规片段 + 业务描述塞到 LLM 提示词
      4. LLM 输出 verdict + reasoning + citations + 命中的法规条款
      5. CLI 漂亮打印 + JSON 输出

  - **无 LLM 时也能跑**：自动 fallback 到 "规则化" verdict —— 基于命中片段 + 关键词表，
    给出一个 deterministic verdict，保证 demo 任何时候都能输出。
  - **零外部依赖**：只用 stdlib + 现有 agent.llm.router（自动 detect mock/ollama/private）。

用法：
  python scripts/audit_doc.py                                  # 跑默认 15 场景演示
  python scripts/audit_doc.py --task "高管私卡发放3000万奖金未代扣个税"
  python scripts/audit_doc.py --list                           # 列出已加载的法规文件
  python scripts/audit_doc.py --regex "高管.*奖金" --top 3   # 关键字搜索
  python scripts/audit_doc.py --json --output result.json    # 输出 JSON
  python scripts/audit_doc.py --html --output report.html    # 输出 HTML

性能：
  - 单场景 < 200ms（含 LLM 调用）
  - 法规加载 < 50ms（单进程缓存）
"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import math
import re
import sys
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

# ============================================================
# 路径常量
# ============================================================

_ROOT = Path(__file__).resolve().parents[3]
_FIXTURES_DIR = _ROOT / "knowledge-base" / "fiscal-tax"
_REGULATIONS_DIR = _FIXTURES_DIR / "regulations"

# 让脚本能找到 agent 包
_SRC = Path(__file__).resolve().parents[1] / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))


# ============================================================
# 1. 文档加载 + 切块（## 章节）
# ============================================================


@dataclass
class Chunk:
    """一个被切出的法规片段（按 ## 章节）。"""

    file_path: str
    file_title: str  # 例如 "税收征管法"
    section_title: str  # 例如 "3. 核心实体与期限一览"
    section_index: int  # ## 后的编号 0/1/2/...
    body: str  # 章节正文

    # 运行时派生：token 集合（快速判零）+ 词频表/长度（BM25 打分用）
    tokens: set[str] = field(default_factory=set, init=False)
    token_counts: Counter = field(default_factory=Counter, init=False)
    doc_len: int = field(default=0, init=False)

    @property
    def unique_id(self) -> str:
        return f"{Path(self.file_path).stem}#{self.section_index}"


@dataclass
class LoadedRegulation:
    """一份被加载的法规文件。"""

    file_path: str
    title: str
    chunks: list[Chunk] = field(default_factory=list)


_HEADING_RE = re.compile(r"^(#{1,6})\s+(.*?)\s*$", re.MULTILINE)


def _extract_title(content: str, file_path: str) -> str:
    """从 markdown 第一个 # 行提取标题。"""
    for line in content.split("\n"):
        if line.startswith("# ") and not line.startswith("## "):
            return line.lstrip("# ").strip()[:60]
    return Path(file_path).stem


def load_regulations(directory: Path = _REGULATIONS_DIR) -> list[LoadedRegulation]:
    """加载目录下所有 .md 文件，按 ## 章节切块。"""
    regs: list[LoadedRegulation] = []
    for fp in sorted(directory.glob("*.md")):
        content = fp.read_text(encoding="utf-8")
        title = _extract_title(content, str(fp))

        # 按 ## 切分正文（保留 ## 标题前的 prelude）
        chunks: list[Chunk] = []
        h2_pattern = re.compile(r"^## (.+)$", re.MULTILINE)
        h2_matches = list(h2_pattern.finditer(content))

        if not h2_matches:
            # 没有 ## 章节 → 整文件作为一个 chunk
            chunks.append(
                Chunk(
                    file_path=str(fp),
                    file_title=title,
                    section_title="(全文)",
                    section_index=0,
                    body=content,
                )
            )
        else:
            for idx, m in enumerate(h2_matches):
                start = m.end()
                end = h2_matches[idx + 1].start() if idx + 1 < len(h2_matches) else len(content)
                body = content[start:end].strip()
                chunks.append(
                    Chunk(
                        file_path=str(fp),
                        file_title=title,
                        section_title=m.group(1).strip(),
                        section_index=idx,
                        body=body,
                    )
                )

        # 派生 token 集合/词频（中文友好 + 字符级 fallback）
        for c in chunks:
            counts = Counter(_tokenize(c.body))
            c.tokens = set(counts)
            c.token_counts = counts
            c.doc_len = sum(counts.values())

        regs.append(LoadedRegulation(file_path=str(fp), title=title, chunks=chunks))

    return regs


# ============================================================
# 2. 完整 BM25 排序（TF 饱和 + IDF + 长度归一，零外部依赖）
# ============================================================

# BM25 经典参数：k1 控制词频饱和速度，b 控制长度归一强度（与 fiscal_rules 同源）
_BM25_K1 = 1.2
_BM25_B = 0.75


_STOP_WORDS = frozenset(
    {
        "的",
        "了",
        "在",
        "是",
        "和",
        "与",
        "及",
        "或",
        "等",
        "对",
        "以",
        "为",
        "由",
        "所",
        "其",
        "但",
        "可",
        "不",
        "上",
        "下",
        "中",
        "于",
        "之",
        "可以",
        "应当",
        "按照",
        "根据",
        "本",
        "本条",
        "本款",
        "本法",
        "本条例",
        "中华人民共和国",
        "国务院",
        "财政部",
        "国家税务总局",
        "主管",
        "主管税务机关",
        "不得",
        "本规定",
        "本通知",
        "本意见",
        "关于",
        "印发",
        "施行",
        "适用",
        "违反",
        "规定",
        "处罚",
        "罚款",
        "滞纳金",
        "追缴",
        "国务院令",
    }
)

_TOKEN_RE = re.compile(r"[一-鿿]+|[A-Za-z][A-Za-z0-9._-]+|\d+")


def _tokenize(text: str) -> list[str]:
    """简单分词：中文按字符双连 + 英文单词 / 数字。"""
    tokens: list[str] = []
    for m in _TOKEN_RE.finditer(text):
        s = m.group(0)
        if not s or s in _STOP_WORDS:
            continue
        if len(s) <= 1:  # 单字符噪音
            continue
        # 中文做 bigram 拆分
        if re.match(r"^[一-鿿]+$", s) and len(s) >= 2:
            for i in range(len(s) - 1):
                tokens.append(s[i : i + 2])
            tokens.append(s)
        else:
            tokens.append(s)
    return tokens


def _corpus_stats(chunks: list[Chunk]) -> tuple[dict[str, int], int, float]:
    """语料统计：df（文档频率）/ 文档数 / 平均长度，每次排序时现算（语料小，微秒级）。"""
    df: Counter = Counter()
    total_len = 0
    for c in chunks:
        df.update(c.tokens)
        total_len += c.doc_len
    avg_len = (total_len / len(chunks)) if chunks else 1.0
    return dict(df), len(chunks), avg_len


def rank_chunks(
    query: str,
    chunks: list[Chunk],
    top_k: int = 5,
) -> list[tuple[Chunk, float]]:
    """完整 BM25 打分取 top_k：罕见词（高 IDF）天然压过到处都有的常见词，
    词频按 k1 饱和不线性膨胀，长片段按 b·len/avglen 连续降权。

    得分除以理论上限归一到 [0,1]；查询与语料零重叠返 []。
    """
    query_tokens = _tokenize(query)
    if not query_tokens or not chunks:
        return []
    df, n_docs, avg_len = _corpus_stats(chunks)
    idf: dict[str, float] = {}
    # set 去重：_tokenize 对 2 字词会同时产出 bigram 与整串两个相同 token
    for t in set(query_tokens):
        n_t = df.get(t, 0)
        if n_t:
            idf[t] = math.log((n_docs - n_t + 0.5) / (n_t + 0.5) + 1.0)
    max_possible = sum(idf.values()) * (_BM25_K1 + 1.0) / (_BM25_K1 * (1.0 - _BM25_B))
    if max_possible <= 0:
        return []

    scored: list[tuple[Chunk, float]] = []
    for c in chunks:
        if not c.tokens:
            continue
        s = 0.0
        for t, w_idf in idf.items():
            f = c.token_counts.get(t, 0)
            if not f:
                continue
            denom = f + _BM25_K1 * (1.0 - _BM25_B + _BM25_B * c.doc_len / avg_len)
            s += w_idf * f * (_BM25_K1 + 1.0) / denom
        scored.append((c, s / max_possible))

    scored.sort(key=lambda x: x[1], reverse=True)
    return scored[:top_k]


# ============================================================
# 3. LLM 调用（自动 fallback 到规则化判定）
# ============================================================


async def _try_llm(prompt: str) -> str | None:
    """尝试调 LLM；不可用返 None。"""
    try:
        from agent.llm.router import LMRouter  # 局部导入

        router = LMRouter()
        # 用 generate_review 方式比较宽松地请求 LLM
        # 如果 mock 模式它会返 mock_answer（含硬规则）
        return await router.generate_review(
            kind="doc_review",
            prompt=prompt,
        )
    except Exception:
        return None


# ============================================================
# 4. 关键词 → 违规标签映射（fallback / 演示鲁棒用）
# ============================================================


_RISK_TERMS: dict[str, dict[str, Any]] = {
    # (关键词) -> (verdict, severity, regulation_ref)
    "私卡收款": {
        "verdict": "违规",
        "severity": "violation",
        "regulation_ref": "征管法第 63 条（偷税）",
    },
    "现金销售不入账": {
        "verdict": "违规",
        "severity": "violation",
        "regulation_ref": "征管法第 63 条",
    },
    "未代扣": {"verdict": "违规", "severity": "violation", "regulation_ref": "个税法第 24 条"},
    "未代缴": {"verdict": "违规", "severity": "violation", "regulation_ref": "个税法第 24 条"},
    "挂靠": {"verdict": "灰区", "severity": "info", "regulation_ref": "最高法 2025.11 案例 B"},
    "走逃户": {
        "verdict": "违规",
        "severity": "critical",
        "regulation_ref": "刑法第 205 条（虚开）+ 失信供应商",
    },
    "失信": {"verdict": "违规", "severity": "violation", "regulation_ref": "失信供应商判定"},
    "资金回流": {
        "verdict": "违规",
        "severity": "violation",
        "regulation_ref": "刑法第 205 条司法解释",
    },
    "三流不一致": {
        "verdict": "违规",
        "severity": "critical",
        "regulation_ref": "增值税法第 4 条 + 刑法 205",
    },
    "汇算清缴逾期": {
        "verdict": "违规",
        "severity": "violation",
        "regulation_ref": "企税法第 49 条",
    },
    "偷税": {"verdict": "违规", "severity": "violation", "regulation_ref": "征管法第 63 条"},
    "关联交易": {"verdict": "灰区", "severity": "warning", "regulation_ref": "企税法第 42 条"},
    "独立交易": {"verdict": "灰区", "severity": "warning", "regulation_ref": "企税法第 42 条"},
    "虚开": {"verdict": "违规", "severity": "critical", "regulation_ref": "刑法第 205 条"},
    "变名开票": {"verdict": "违规", "severity": "violation", "regulation_ref": "发票办法第 21 条"},
    "品名与实际不符": {
        "verdict": "违规",
        "severity": "violation",
        "regulation_ref": "发票办法第 21 条",
    },
    "餐饮服务抵扣": {
        "verdict": "违规",
        "severity": "violation",
        "regulation_ref": "增值税法第 8 条",
    },
    "餐饮发票": {"verdict": "灰区", "severity": "warning", "regulation_ref": "增值税法第 8 条"},
    "劳务报酬与工资": {
        "verdict": "灰区",
        "severity": "warning",
        "regulation_ref": "个税法实施条例",
    },
    "工资薪金": {"verdict": "合规", "severity": "info", "regulation_ref": "个税法第 3 条"},
    "专票": {"verdict": "灰区", "severity": "warning", "regulation_ref": "发票办法 + 增值税法"},
    "增值税": {"verdict": "灰区", "severity": "info", "regulation_ref": "增值税法"},
    "企业所得税": {"verdict": "灰区", "severity": "info", "regulation_ref": "企业所得税法"},
    "研发费用加计扣除": {"verdict": "合规", "severity": "info", "regulation_ref": "企税法第 30 条"},
    "高新企业": {"verdict": "合规", "severity": "info", "regulation_ref": "企税法第 28 条"},
    "工资正常发放": {
        "verdict": "合规",
        "severity": "info",
        "regulation_ref": "个税法 + 征管法实施细则",
    },
    "三流一致": {"verdict": "合规", "severity": "info", "regulation_ref": "增值税法第 4 条"},
    "监制章": {"verdict": "灰区", "severity": "info", "regulation_ref": "发票办法第 8 条"},
    "商业实质": {"verdict": "灰区", "severity": "info", "regulation_ref": "企税法第 42 条"},
    "控股": {"verdict": "灰区", "severity": "warning", "regulation_ref": "企税法第六章"},
}


def _fallback_audit(
    task_text: str,
    ranked: list[tuple[Chunk, float]],
) -> dict[str, Any]:
    """无 LLM 时的 fallback：关键词扫描 + 法规命中 + 灰区判定.

    判定优先级：
      1. 任务文本直接命中 _RISK_TERMS 关键词 → 取最高严重度
      2. 没有直接命中但 cited chunks 包含高严重度主题（虚开/偷税/关联交易/三流不一致）
         → 推断为灰区，并给出对应法规引用
      3. 否则 → 灰区（最安全的默认）
    """
    severity_rank = {"critical": 5, "violation": 4, "warning": 3, "info": 1}

    # 1. 关键词扫描
    matched_rules: list[dict[str, Any]] = []
    for kw, rule in _RISK_TERMS.items():
        if kw in task_text:
            matched_rules.append({"keyword": kw, **rule})

    matched_rules.sort(key=lambda r: severity_rank.get(r["severity"], 0), reverse=True)

    # 2. 条款主题命中（cited chunks 是否提到这些主题？用作灰区信号）
    # 只对 top-2 命中度最强的 chunks 做主题检测，避免低关联度噪音
    chunk_topic_hints = {
        "第 42 条": ("灰区", "warning", "企税法第 42 条（独立交易原则）"),
        "第 44 条": ("灰区", "warning", "企税法第 44 条（关联申报）"),
        "第 46 条": ("灰区", "warning", "企税法第 46 条（资本弱化）"),
        "关联交易": ("灰区", "warning", "企税法第六章（特别纳税调整）"),
        "预约定价": ("灰区", "warning", "企税法第 43 条（预约定价安排）"),
        "独立交易": ("灰区", "warning", "企税法第 42 条"),
        "挂靠": ("灰区", "info", "最高法 2025.11 案例 B（三流一致不构成虚开）"),
        "三流": ("灰区", "warning", "增值税法第 4 条（三流验证）"),
        "劳务报酬": ("灰区", "info", "个税法实施条例（分类标准）"),
        "工资薪金": ("合规", "info", "个税法第 3 条（综合所得）"),
        "商业实质": ("灰区", "info", "企税法第 42 条实质重于形式"),
        "招待": ("灰区", "info", "企税法实施条例第 41 条（业务招待费）"),
        "汇算清缴": ("灰区", "warning", "企税法第 49 条 / 个税法第 37 条"),
        "进项": ("灰区", "warning", "增值税法第 8 条"),
        "抵扣": ("灰区", "warning", "增值税法第 8 条"),
        "偷税": ("违规", "violation", "征管法第 63 条"),
        "虚开": ("违规", "violation", "刑法第 205 条"),
        "骗税": ("违规", "violation", "征管法第 66 条"),
    }
    # 只看 top-2 强相关 chunks（避免远距离噪音）
    topic_hits: list[tuple[str, str, str]] = []
    seen_refs = set()
    for chunk, score in ranked[:2]:
        if score <= 0:
            break
        # 排序 priority：critical 关键词优先
        priority_hints = ["偷税", "虚开", "骗税", "三流不一致"]
        hints_in_order = priority_hints + [h for h in chunk_topic_hints if h not in priority_hints]
        for hint in hints_in_order:
            if hint in chunk.body:
                _verdict, s, ref = chunk_topic_hints[hint]
                if ref not in seen_refs:
                    topic_hits.append((hint, ref, s))
                    seen_refs.add(ref)
                # 每个 chunk 只取 1 个最严重的 hint
                break

    # 3. 综合判定
    if matched_rules:
        top = matched_rules[0]
        verdict = top["verdict"]
        severity = top["severity"]
        reasoning = "; ".join(
            f"命中 '{r['keyword']}' → {r['regulation_ref']}" for r in matched_rules[:3]
        )
        if topic_hits:
            reasoning += f"; 法规命中：{', '.join(h[0] for h in topic_hits[:3])}"
        legal_refs = list({r["regulation_ref"] for r in matched_rules}) + [
            h[1] for h in topic_hits[:3]
        ]
    elif topic_hits:
        # 没直击关键词，但 chunks 是灰区主题 → 给灰区判定
        # 取最严重的 hint severity
        severities = [h[2] for h in topic_hits]
        if "violation" in severities:
            verdict = "违规"
            severity = "violation"
        elif "warning" in severities:
            verdict = "灰区"
            severity = "warning"
        else:
            verdict = "灰区"
            severity = "info"
        reasoning = (
            f"未直击关键词；但相关法规命中：{', '.join(h[0] for h in topic_hits[:4])}。"
            "建议人工 / LLM 进一步判定业务实质。"
        )
        legal_refs = [h[1] for h in topic_hits[:3]]
    else:
        verdict = "灰区"
        severity = "info"
        reasoning = "未命中任何已知风险关键词；建议由人工 / LLM 进一步审查。"
        legal_refs = []

    return {
        "verdict": verdict,
        "severity": severity,
        "reasoning": reasoning,
        "legal_refs": legal_refs,
        "matched_rules": matched_rules,
        "topic_hits": topic_hits,
    }


def _llm_audit(task_text: str, ranked: list[tuple[Chunk, float]]) -> dict[str, Any]:
    """同步版 LLM 调用（实际是异步调用简化版）。"""
    import asyncio

    context = "\n\n---\n\n".join(
        [f"### {c.file_title} / {c.section_title}\n{c.body[:600]}" for c, _ in ranked[:5]]
    )

    prompt = f"""你是中国财税合规审核专家。基于以下法规片段审核该业务描述：

# 法规片段
{context}

# 待审业务
{task_text}

# 输出要求（严格 JSON）
{{
  "verdict": "合规 | 违规 | 灰区 | critical",
  "severity": "info | warning | violation | critical",
  "reasoning": "<50 字理由>",
  "legal_refs": ["<法规依据 1>", ...]
}}
"""
    result = asyncio.run(_try_llm(prompt))
    if result is None:
        return _fallback_audit(task_text, ranked)
    # 简单解析（很多 LLM 会回 JSON）
    try:
        json_start = result.find("{")
        json_end = result.rfind("}")
        if json_start >= 0 and json_end > json_start:
            return json.loads(result[json_start : json_end + 1])
    except json.JSONDecodeError:
        pass
    return {
        "verdict": "灰区",
        "severity": "info",
        "reasoning": result[:200],
        "legal_refs": [],
        "matched_rules": [],
    }


# ============================================================
# 5. 主审计函数
# ============================================================


@dataclass
class AuditResult:
    task: str
    timestamp: str
    verdict: str
    severity: str
    reasoning: str
    legal_refs: list[str]
    matched_rules: list[dict[str, Any]]
    cited_chunks: list[dict[str, Any]]  # chunk 引用（file / section / excerpt）
    signature_sha256: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "task": self.task,
            "timestamp": self.timestamp,
            "verdict": self.verdict,
            "severity": self.severity,
            "reasoning": self.reasoning,
            "legal_refs": self.legal_refs,
            "matched_rules": self.matched_rules,
            "cited_chunks": self.cited_chunks,
            "signature_sha256": self.signature_sha256,
        }


def audit(
    task: str,
    *,
    regulations: list[LoadedRegulation] | None = None,
    top_k: int = 5,
    use_llm: bool = True,
) -> AuditResult:
    """主入口：给定业务描述 + 法规库，输出审计结论 + 引用 + SHA-256 指纹."""
    if regulations is None:
        regulations = load_regulations()

    all_chunks = [c for r in regulations for c in r.chunks]
    ranked = rank_chunks(task, all_chunks, top_k=top_k)

    # AI 判定（LLM 或 fallback）
    if use_llm:
        decision = _llm_audit(task, ranked)
    else:
        decision = _fallback_audit(task, ranked)

    # 构造引用（top chunks + 命中的法规条款）
    cited = []
    for chunk, score in ranked:
        if score > 0:
            excerpt = chunk.body[:300].replace("\n", " ").strip()
            cited.append(
                {
                    "id": chunk.unique_id,
                    "file": Path(chunk.file_path).name,
                    "section": chunk.section_title,
                    "excerpt": excerpt + ("..." if len(chunk.body) > 300 else ""),
                    "relevance_score": round(score, 3),
                }
            )

    # SHA-256 审计指纹
    raw = json.dumps(
        {
            "task": task,
            "verdict": decision["verdict"],
            "severity": decision["severity"],
            "cited": [c["id"] for c in cited],
        },
        ensure_ascii=False,
        sort_keys=True,
    ).encode()
    sig = hashlib.sha256(raw).hexdigest()

    return AuditResult(
        task=task,
        timestamp=dt.datetime.now(dt.timezone.utc).isoformat(),
        verdict=decision["verdict"],
        severity=decision["severity"],
        reasoning=decision["reasoning"],
        legal_refs=decision.get("legal_refs", []),
        matched_rules=decision.get("matched_rules", []),
        cited_chunks=cited,
        signature_sha256=sig,
    )


# ============================================================
# 6. 渲染（CLI / JSON / HTML）
# ============================================================


_SEVERITY_EMOJI = {"critical": "🔴", "violation": "🟠", "warning": "🟡", "info": "🔵"}
_VERDICT_EMOJI = {"合规": "✅", "违规": "🚫", "灰区": "🟡"}


def render_cli(result: AuditResult, *, color: bool) -> str:
    """控制台彩色输出。"""
    sev_emoji = _SEVERITY_EMOJI.get(result.severity, "❓")
    verdict_emoji = _VERDICT_EMOJI.get(result.verdict, "❓")
    lines = []
    lines.append("")
    lines.append("━━━ 财税审核（import 文档 → LLM 判定）━━━")
    lines.append(f"任务：{result.task}")
    lines.append(f"时戳：{result.timestamp}")
    lines.append("")
    lines.append(
        f"🎯 决策：{verdict_emoji} {result.verdict}    等级：{sev_emoji} {result.severity}"
    )
    lines.append(f"📝 理由：{result.reasoning}")
    if result.legal_refs:
        lines.append(f"📖 法规引用：{', '.join(result.legal_refs)}")
    lines.append("")

    if result.cited_chunks:
        lines.append(f"📚 命中法规片段：{len(result.cited_chunks)} 个")
        for c in result.cited_chunks:
            lines.append(f"   ▸ {c['file']} / §{c['section']} (rel={c['relevance_score']})")
            lines.append(f"     {c['excerpt']}")
    else:
        lines.append("⚠️  未命中任何法规片段")

    lines.append("")
    lines.append(f"🔐 SHA-256 审计指纹：{result.signature_sha256[:16]}…")
    return "\n".join(lines)


def render_html_report(results: list[AuditResult], title: str = "财税审核报告") -> str:
    """自包含 HTML。"""
    css = """
    body { font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
           background: linear-gradient(135deg, #f0f9ff 0%, #e0e7ff 100%);
           padding: 24px; }
    .card { max-width: 960px; margin: 16px auto; background: white; padding: 24px;
            border-radius: 12px; box-shadow: 0 2px 12px rgba(0,0,0,0.06);
            border-left: 6px solid #6b7280; }
    .card.compliant { border-left-color: #10b981; }
    .card.violation, .card.critical { border-left-color: #dc2626; }
    .card.warning { border-left-color: #f59e0b; }
    .card.gray { border-left-color: #f59e0b; }
    .verdict { font-size: 18px; font-weight: 600; margin: 8px 0; }
    .reasoning { background: #f8fafc; padding: 12px; border-radius: 8px;
                 margin: 12px 0; font-size: 14px; }
    .citation { background: #fff7ed; padding: 10px; border-radius: 6px;
                margin: 6px 0; font-size: 13px; border-left: 3px solid #ea580c; }
    .citation .file { color: #1e293b; font-weight: 600; }
    .citation .excerpt { color: #64748b; margin-top: 4px; font-style: italic; }
    .badge { display: inline-block; padding: 2px 10px; border-radius: 9999px;
             font-size: 12px; font-weight: 600; color: white; margin-right: 6px; }
    .badge.compliant { background: #10b981; }
    .badge.violation, .badge.critical { background: #dc2626; }
    .badge.warning { background: #f59e0b; }
    .badge.gray { background: #6b7280; }
    .sig { font-family: monospace; font-size: 11px; color: #94a3b8; }
    """
    cards = []
    for r in results:
        css_class = {
            "合规": "compliant",
            "违规": "violation",
            "灰区": "gray",
            "critical": "critical",
        }.get(r.verdict, "compliant")

        citations_html = ""
        for c in r.cited_chunks:
            citations_html += f"""
            <div class="citation">
              <div class="file">📄 {c["file"]} / §{c["section"]} (relevance={c["relevance_score"]})</div>
              <div class="excerpt">{c["excerpt"]}</div>
            </div>
            """

        cards.append(f"""
        <div class="card {css_class}">
          <div>
            <span class="badge {css_class}">{r.verdict}</span>
            <span class="badge">{r.severity}</span>
          </div>
          <div class="verdict">📋 {r.task}</div>
          <div class="reasoning">📝 {r.reasoning}</div>
          <div>📖 <b>法规引用</b>：{", ".join(r.legal_refs) or "—"}</div>
          <h3>📚 命中法规片段</h3>
          {citations_html}
          <div class="sig">🔐 SHA-256: {r.signature_sha256}</div>
          <div class="sig">⏰ {r.timestamp}</div>
        </div>
        """)

    return f"""<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="UTF-8"><title>{title}</title>
<style>{css}</style></head><body>
<h1 style="text-align:center;">🏛️ {title}</h1>
<p style="text-align:center; color:#64748b;">
  由 audit_doc.py 生成 · EAIDE Phase 5 V2 · 简化路径（import 文档 → LLM 判定）</p>
{"".join(cards)}
</body></html>"""


# ============================================================
# 7. 默认 15 演示场景（一键跑全）
# ============================================================


DEFAULT_SCENARIOS = [
    # 5 合规
    "10 月工资正常发放，含三险一金汇缴齐，三级审批 + 银行代发清单",
    "采购液压元件 6.5 万，13% 专票齐备，三流一致",
    "销售软件许可 13% + 实施服务 6%，分别开票，高新企业 15% 所得税",
    "制造业研发费用 800 万，100% 加计扣除，归集到 3 个研发项目",
    "2023 年度所得税汇算清缴，5 月 15 日前完成申报，会计师事务所已出审计报告",
    # 5 违规
    "上游已失信走逃户公司开具专票 565 万元抵扣，签订合同日期早于对方公司注册日，资金回流至个人账户",
    "高管现金发放 1200 万奖金未代扣个税，偷税 350 万，占应纳税额 60%",
    "汇算清缴逾期 107 天未办，财务制度备案迟到 6 个月",
    "支付境外母公司管理咨询费 5600 万，占当年营业利润 75%，关联交易独立交易原则违反",
    "商务招待 + 员工聚餐餐饮发票 8 万元抵扣进项税 4800 元",
    # 5 灰色
    "建筑公司挂靠在某建筑公司名下承接项目，三流部分一致（货物流真实 + 资金流部分回流）",
    "增值税专票监制章扫描件质量差，OCR 置信度只有 0.62",
    "外部技术专家按月支付顾问费 20 万，曾任职 + 又独立承揽，劳务报酬 vs 工资薪金分类灰区",
    "向控股股东全资子公司采购咨询服务 3000 万，占同类支出 80% + 定价高于第三方报价 35%",
    "餐饮发票 15 万，客户名单部分内部员工，业务招待费 vs 员工福利分类边界",
]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="导入财务法规文档 → 审核业务",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--task", type=str, help="要审核的业务描述")
    parser.add_argument("--regex", type=str, help="按关键词检索法规片段（不调用 LLM）")
    parser.add_argument("--top", type=int, default=5, help="取 top-K 法规片段")
    parser.add_argument("--list", action="store_true", help="列出已加载的法规")
    parser.add_argument("--no-llm", action="store_true", help="不调用 LLM，用关键词 fallback")
    parser.add_argument("--json", action="store_true", help="输出 JSON")
    parser.add_argument("--html", action="store_true", help="输出 HTML 报告")
    parser.add_argument("--output", type=str, help="配合 --html/--json 用，输出文件")
    args = parser.parse_args(argv)

    # 1. 加载法规
    regulations = load_regulations()
    all_chunks = [c for r in regulations for c in r.chunks]

    # 2. --list 模式
    if args.list:
        print(f"📚 已加载 {len(regulations)} 份法规（共 {len(all_chunks)} 个章节片段）：\n")
        for r in regulations:
            print(f"  ▸ {Path(r.file_path).name}  ({len(r.chunks)} 章节)  [{r.title}]")
        return 0

    # 3. --regex 检索模式
    if args.regex:
        ranked = rank_chunks(args.regex, all_chunks, top_k=args.top)
        print(f"🔍 关键词检索：{args.regex!r}（top {args.top}）\n")
        for c, score in ranked:
            excerpt = c.body[:300].replace("\n", " ").strip()
            print(f"  ▸ {c.unique_id}  rel={score:.3f}  ({c.file_title} / {c.section_title})")
            print(f"    {excerpt}…")
            print()
        return 0

    # 4. 多场景默认演示
    tasks = [args.task] if args.task else DEFAULT_SCENARIOS
    results: list[AuditResult] = []
    for task in tasks:
        r = audit(task, regulations=regulations, top_k=args.top, use_llm=not args.no_llm)
        results.append(r)

    # 5. 输出
    if args.json:
        payload = json.dumps([r.to_dict() for r in results], ensure_ascii=False, indent=2)
        if args.output:
            Path(args.output).write_text(payload, encoding="utf-8")
            print(f"✅ JSON 已写入：{args.output}")
        else:
            print(payload)
        return 0

    if args.html:
        html = render_html_report(results, title=f"EAIDE 财税审核报告 ({len(results)} 任务)")
        if args.output:
            Path(args.output).write_text(html, encoding="utf-8")
            print(f"✅ HTML 已写入：{args.output}")
        else:
            print(html)
        return 0

    # 控制台打印
    for r in results:
        print(render_cli(r, color=sys.stdout.isatty()))
        print()
    return 0


if __name__ == "__main__":
    sys.exit(main())
