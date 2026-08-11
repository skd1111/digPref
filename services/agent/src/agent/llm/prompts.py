"""Loads prompt templates from agent/llm/prompts/*.md at runtime.

Why files instead of constants? Prompts need iteration; keeping them as
.md files means non-engineers (PMs, domain experts) can edit them via PR.

Phase 17：prompt 版本化 —— 每个资产有稳定版本号；修改 .md 后 bump 版本并
失效 L1 缓存（防止旧答案误命中）。缓存 key / 审计可引用 prompt_version。
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

_PROMPTS_DIR = Path(__file__).parent / "prompts"

# 主链路 prompt 资产版本表（未登记的资产默认 v1.0.0）。
# 修改对应 .md 时必须 bump 版本（新增小版本），否则缓存可能误命中旧答案。
PROMPT_VERSIONS: dict[str, str] = {
    "system": "v1.0.0",
    "summarise": "v1.0.0",
    "intent": "v1.0.0",
    "intent_router": "v1.0.0",
    "planner": "v1.0.0",
    "repair": "v1.0.0",
    "judge": "v1.0.0",
    "decompose": "v1.0.0",
    "subagent_execution": "v1.0.0",
    "tool_orchestrate": "v1.0.0",
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
