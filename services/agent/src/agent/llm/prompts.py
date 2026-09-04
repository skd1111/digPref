"""Loads prompt templates from agent/llm/prompts/*.md at runtime.

Why files instead of constants? Prompts need iteration; keeping them as
.md files means non-engineers (PMs, domain experts) can edit them via PR.

Phase 17：prompt 版本化 —— 每个资产有稳定版本号；修改 .md 后 bump 版本并
失效 L1 缓存（防止旧答案误命中）。缓存 key / 审计可引用 prompt_version。
"""

from __future__ import annotations

from datetime import datetime
from functools import lru_cache
from pathlib import Path

_PROMPTS_DIR = Path(__file__).parent / "prompts"

# 主链路 prompt 资产版本表（未登记的资产默认 v1.0.0）。
# 修改对应 .md 时必须 bump 版本（新增小版本），否则缓存可能误命中旧答案。
PROMPT_VERSIONS: dict[str, str] = {
    "system": "v1.0.0",
    # v1.0.1 (2026-08-17): 时间纪律（禁止编造日期）+ Current time 注入说明（BUGFIX #112）
    "summarise": "v1.0.1",
    "intent": "v1.0.0",
    # v1.0.1 (2026-08-14): 新增 model_onboard / conn_test 细分类型 + 操作类 few-shot
    # v1.0.2 (2026-08-31): 动态 Few-Shot 注入纪律（参考历史案例段仅参考不照搬）
    "intent_router": "v1.0.2",
    "planner": "v1.0.0",
    "repair": "v1.0.0",
    "judge": "v1.0.0",
    # v1.0.1 (2026-08-14): 提问纪律（带方案一次确认）+ 模型接入槽位表 + PAGE_CONTEXT
    "decompose": "v1.0.1",
    "subagent_execution": "v1.0.0",
    # v1.0.1 (2026-08-17): 新增编排决策交接（DECISION_HINT），确认后循环不丢上下文
    # v1.0.2 (2026-08-17): final_answer/ask_user_message 语言约束（随用户语言，中文提问中文答，BUGFIX #114）
    "tool_orchestrate": "v1.0.2",
}


def prompt_version(name: str) -> str:
    """查询 prompt 资产版本（未登记返 v1.0.0）。"""
    return PROMPT_VERSIONS.get(name, "v1.0.0")


def bump_prompt_version(name: str) -> str:
    """bump 小版本号并失效 L1 精确缓存（主动失效，防旧答案误命中）。

    Returns: 新版本号（如 v1.0.0 → v1.0.1）。
    """
    cur = PROMPT_VERSIONS.get(name, "v1.0.0")
    try:
        nums = [int(p) for p in cur.lstrip("v").split(".")]
    except ValueError:
        nums = [1, 0, 0]
    nums += [0] * (3 - len(nums))
    nums[2] += 1
    new = "v" + ".".join(str(n) for n in nums[:3])
    PROMPT_VERSIONS[name] = new
    # 主动失效：prompt 变了，基于旧 prompt 的精确缓存全部作废。
    # 延迟导入避免循环依赖（router 在导入链下游）。
    try:
        from agent.llm.router import get_l1_cache

        get_l1_cache().clear()
    except Exception:
        pass
    return new


@lru_cache(maxsize=64)
def load_prompt(name: str) -> str:
    """Load a prompt template; ``name`` supports subpaths like "biznav/extract".

    Security: reject absolute paths and any path segment equal to ".." to
    prevent directory traversal.
    """
    if not name or name.startswith(("/", "\\")) or ".." in Path(name).parts:
        raise ValueError(f"invalid prompt name: {name!r}")
    root = _PROMPTS_DIR.resolve()
    path = (root / f"{name}.md").resolve()
    if not str(path).startswith(str(root)):
        raise ValueError(f"prompt path escapes prompts dir: {name!r}")
    if not path.exists():
        raise FileNotFoundError(f"prompt not found: {path}")
    return path.read_text(encoding="utf-8")


def render_prompt(template: str, **values: str) -> str:
    """Render a template by replacing {{KEY}} placeholders.

    Uses str.replace (not str.format) so literal JSON braces in the template
    are preserved.
    """
    out = template
    for key, value in values.items():
        out = out.replace("{{" + key + "}}", str(value))
    return out


_WEEKDAY_CN = ("星期一", "星期二", "星期三", "星期四", "星期五", "星期六", "星期日")


def current_time_text() -> str:
    """系统本地当前时间（含星期）—— summarise 注入的唯一时间基准。

    模型对「今天」没有可靠感知（BUGFIX #112）：不注入时本地模型会凭训练
    知识编造日期。用本地时区（astimezone）与 datetime_now 工具的口径一致。
    """
    now = datetime.now().astimezone()
    return f"{now.strftime('%Y-%m-%d %H:%M:%S %z')} {_WEEKDAY_CN[now.weekday()]}"


# LangChain BaseMessage 的 .type → 会话 role 映射。
# 根治 BUGFIX #163：state["messages"] 带 add_messages reducer，LangGraph 会把
# 入图时的 {"role": ..., "content": ...} dict 统一转成 HumanMessage / AIMessage
# 对象，而 BaseMessage **没有 .role 属性**（只有 .type，取值 human / ai / system）。
# 此前全仓 6 处消费点各自手写 `getattr(h, "role", None)`，对象形态一律取不到：
# 4 处静默丢弃整条历史（终答/意图/planner/原生工具循环看不见前文），
# 2 处 fallback 成默认 "user"（assistant 回复被误标成用户提问，比丢弃更隐蔽）。
_LC_TYPE_TO_ROLE = {
    "human": "user",
    "ai": "assistant",
    "system": "system",
    # 少数场景下 BaseMessage 子类直接以目标名命名，原样透传避免二次映射丢失
    "user": "user",
    "assistant": "assistant",
}


def normalize_message(msg: object) -> tuple[str, str] | None:
    """把一条会话消息归一成 ``(role, content)``，不认识的形态返回 ``None``。

    这是全仓消费 ``state["messages"]`` 的**唯一**解析入口（根治 BUGFIX #163）。
    支持三种形态：

    - LangChain ``BaseMessage``：读 ``.type`` 并按 ``_LC_TYPE_TO_ROLE`` 映射
    - ``dict``：读 ``role`` / ``content`` 键
    - 其他：返回 ``None``

    调用方**必须**把 ``None`` 当作「跳过这一条」，不要 fallback 成默认 role ——
    把 assistant 误标成 user 会让模型看到一段全是用户自言自语的对话。
    """
    if isinstance(msg, dict):
        raw_role = msg.get("role")
        content = msg.get("content")
    else:
        # BaseMessage 优先读 .type；个别自定义对象可能真带 .role，兼容之
        raw_role = getattr(msg, "type", None) or getattr(msg, "role", None)
        content = getattr(msg, "content", None)
    role = _LC_TYPE_TO_ROLE.get(str(raw_role or "").strip().lower())
    if role is None:
        return None
    text = str(content or "").strip()
    if not text:
        return None
    return role, text


def format_rag_block(rag_context: str | None) -> str:
    """把 RAG 检索到的「知识库参考」片段包装成注入终答 prompt 的段落。

    背景（根因修复 2026-09-04）：聊天链路 rag_retrieve 检索到的召回此前只写入
    ``state.system_prompt_addon``，却从未被 summarise / 终答链路读取——模型作答时
    根本看不到知识库内容，于是「BM25 命中 80 条却弹 A/B/C 澄清」「跑去 shell
    翻全盘自己找文档」。这里把召回片段作为一等上下文注入终答 prompt，并附
    据库作答 + 溯源纪律。空片段返 ""（调用方据此决定是否注入段落）。
    """
    text = (rag_context or "").strip()
    if not text:
        return ""
    return (
        "本地知识库检索结果（若与问题相关，必须优先依据下列资料作答，并按资料中的"
        "编号标注来源，禁止编造不存在的条款/页码/文件名；资料确实不足以回答时如实"
        "说明并询问用户，绝不要用 shell/dir/glob/find 等文件系统命令去别处翻找）：\n\n"
        f"{text}\n\n"
    )


def format_history_brief(
    history: list | None,
    *,
    max_messages: int = 8,
    per_message_chars: int = 400,
) -> str:
    """把会话历史（BaseMessage 或 dict）压成注入终答 prompt 的简报。

    背景（BUGFIX #135）：终答链路（summarise）此前只拿到当轮 user_prompt，
    跨轮追问（如上一轮「做介绍你自己的 PPT」+ 本轮「是你自己这个智能体客户端」）
    模型看不见前文 → 反问「没有明确任务指令」。把最近几轮 user/assistant
    原文（单条截断）拼进 summarise 用户消息恢复上下文连贯。
    空历史返 ""（调用方据此决定是否注入段落）。

    根治 BUGFIX #163：改走 :func:`normalize_message`，BaseMessage 形态不再被
    静默过滤。同时放行 ``system`` 角色 —— stream.py 会把「前段对话摘要」与
    「任务台账锚点」以 system 消息注入 messages 头部，那正是跨轮上下文里
    信息密度最高的两条，此前一并被丢掉了。
    """
    if not history:
        return ""
    lines: list[str] = []
    for h in history:
        parsed = normalize_message(h)
        if parsed is None:
            continue
        role, text = parsed
        if len(text) > per_message_chars:
            text = text[:per_message_chars] + "…（已截断）"
        lines.append(f"[{role}] {text}")
    return "\n".join(lines[-max_messages:])
