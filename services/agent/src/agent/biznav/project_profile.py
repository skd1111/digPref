"""biznav.project_profile —— init 风格项目画像（2026-08-05）。

仿 Claude Code `/init` / Codex AGENTS.md 的做法：导入工程时把项目读一遍，
沉淀成一段简洁的「项目画像」文本，存进 biznav.db（project_profiles 表）。
后续 chat 发送时前端把画像前置注入 prompt，模型预先知道项目是什么、
用什么语言/框架、目录怎么组织 —— 不再反问「哪个项目 / 什么语言」。

生成策略（两级降级，永不阻塞导入任务）：
    1. 启发式采集确定性事实（语言分布 / 依赖清单 / README 摘要 / 目录结构）；
    2. LLM 可用 → 把事实浓缩成 init 风格画像（走 biznav 降级链
       ollama → 内网 → 云端）；不可用 / 失败 → 直接用事实 Markdown 兜底。
"""

from __future__ import annotations

import json
import logging
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# 画像文本硬上限（注入 prompt 用，太长会挤占上下文）
MAX_PROFILE_CHARS = 3500
# 扫描文件数上限（大仓库保护）
MAX_FILES_SCANNED = 8000

_IGNORED_DIRS = {
    ".git",
    ".svn",
    ".hg",
    ".idea",
    ".vscode",
    ".qoder",
    "node_modules",
    "__pycache__",
    ".venv",
    "venv",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    "dist",
    "build",
    "target",
    "out",
    ".next",
    ".nuxt",
    "coverage",
    ".tox",
}

_EXT_LANG = {
    ".py": "Python",
    ".java": "Java",
    ".go": "Go",
    ".rs": "Rust",
    ".ts": "TypeScript",
    ".tsx": "TypeScript(React)",
    ".js": "JavaScript",
    ".jsx": "JavaScript(React)",
    ".vue": "Vue",
    ".cs": "C#",
    ".cpp": "C++",
    ".c": "C",
    ".kt": "Kotlin",
    ".swift": "Swift",
    ".php": "PHP",
    ".rb": "Ruby",
    ".sql": "SQL",
    ".sh": "Shell",
    ".ps1": "PowerShell",
    ".html": "HTML",
    ".css": "CSS",
}


def collect_project_facts(project_root: str) -> dict[str, Any]:
    """启发式采集项目事实 —— 纯本地 IO，不依赖任何外部服务。"""
    root = Path(project_root)
    facts: dict[str, Any] = {"root": str(root)}

    # ---- 语言分布（扩展名统计） ----
    lang_count: dict[str, int] = {}
    scanned = 0
    for _dirpath, dirnames, filenames in root.walk():
        dirnames[:] = [d for d in dirnames if d not in _IGNORED_DIRS]
        for fn in filenames:
            if scanned >= MAX_FILES_SCANNED:
                break
            scanned += 1
            lang = _EXT_LANG.get(Path(fn).suffix.lower())
            if lang:
                lang_count[lang] = lang_count.get(lang, 0) + 1
        if scanned >= MAX_FILES_SCANNED:
            break
    facts["languages"] = sorted(lang_count.items(), key=lambda kv: kv[1], reverse=True)[:8]
    facts["files_scanned"] = scanned

    # ---- 顶层目录结构 ----
    try:
        top = sorted(
            [
                p.name + ("/" if p.is_dir() else "")
                for p in root.iterdir()
                if p.name not in _IGNORED_DIRS and not p.name.startswith(".")
            ],
        )[:30]
    except OSError:
        top = []
    facts["top_level"] = top

    # ---- 依赖清单（只取名字，不取版本，压体积） ----
    deps: list[str] = []
    pkg = root / "package.json"
    if pkg.exists():
        try:
            data = json.loads(pkg.read_text(encoding="utf-8", errors="ignore"))
            deps += list((data.get("dependencies") or {}).keys())[:15]
            if data.get("name"):
                facts["package_name"] = str(data["name"])
        except (json.JSONDecodeError, OSError):
            pass
    pyproj = root / "pyproject.toml"
    if pyproj.exists():
        deps += _grep_deps(pyproj.read_text(encoding="utf-8", errors="ignore"))
    req = root / "requirements.txt"
    if req.exists():
        for line in req.read_text(encoding="utf-8", errors="ignore").splitlines()[:15]:
            name = line.split("==")[0].split(">=")[0].strip()
            if name and not name.startswith("#"):
                deps.append(name)
    for marker in ("pom.xml", "go.mod", "Cargo.toml", "build.gradle"):
        if (root / marker).exists():
            deps.append(f"[{marker} 项目]")
    facts["dependencies"] = deps[:25]

    # ---- 已有说明文档（init 指令同思路：优先复用项目自己的描述） ----
    for name in ("README.md", "README.rst", "README.txt", "README"):
        p = root / name
        if p.exists():
            facts["readme_head"] = _read_head(p, 1200)
            break
    for name in ("AGENTS.md", "CLAUDE.md"):
        p = root / name
        if p.exists():
            facts["agents_md_head"] = _read_head(p, 1200)
            break

    return facts


def facts_to_markdown(project_name: str, facts: dict[str, Any]) -> str:
    """把采集的事实渲染成确定性 Markdown（LLM 不可用时的兜底画像）。"""
    lines: list[str] = [f"【项目画像 · {project_name}】"]
    langs = facts.get("languages") or []
    if langs:
        lines.append("- 主要语言：" + "、".join(f"{lang}({n} 文件)" for lang, n in langs))
    if facts.get("dependencies"):
        lines.append("- 关键依赖：" + ", ".join(facts["dependencies"]))
    if facts.get("top_level"):
        lines.append("- 顶层结构：" + ", ".join(facts["top_level"]))
    if facts.get("readme_head"):
        lines.append("- 项目自述（节选）：")
        lines.append(facts["readme_head"])
    if facts.get("agents_md_head"):
        lines.append("- 项目约定（AGENTS/CLAUDE.md 节选）：")
        lines.append(facts["agents_md_head"])
    lines.append("（以上为导入工程时自动采集的项目事实，回答时请直接采用，不要反问用户。）")
    return "\n".join(lines)[:MAX_PROFILE_CHARS]


_INIT_PROMPT = (
    "你是项目初始化助手（类似 Claude Code 的 /init 指令）。"
    "下面是对一个工程扫描得到的事实，请据此写一份简明的「项目画像」，供 AI 编程助手"
    "在后续对话中作为背景知识使用。\n"
    "要求：\n"
    "1. 用 Markdown，800 字以内；\n"
    "2. 包含：项目用途（能推断多少写多少）、技术栈与主要语言、目录结构要点、"
    "构建/测试方式（如已知）、开发约定（如已知）；\n"
    "3. 只依据给定事实，不要编造；信息不足的维度直接省略；\n"
    "4. 结尾加一句：回答该项目相关问题时直接采用以上背景，不要反问用户项目名/语言等已知信息。\n"
    "5. 直接输出画像正文，不要任何解释或前缀。\n\n"
    "项目名：{project_name}\n\n扫描事实：\n{facts}"
)


async def generate_profile(
    project_name: str,
    project_root: str,
    llm_client: Callable[[str, list[dict]], Awaitable[str]],
) -> str:
    """生成项目画像：LLM 浓缩优先，失败回退确定性事实 Markdown。

    llm_client 与 biznav api._make_llm_client 同签名：
    async (kind, messages) -> str，内部自带 ollama → 内网 → 云端降级。
    """
    facts = collect_project_facts(project_root)
    fallback = facts_to_markdown(project_name, facts)
    try:
        facts_json = json.dumps(facts, ensure_ascii=False, default=str)[:6000]
        prompt = _INIT_PROMPT.format(project_name=project_name, facts=facts_json)
        text = str(
            await llm_client("project_profile", [{"role": "user", "content": prompt}]) or ""
        ).strip()
        if len(text) >= 40:  # 太短视为无效输出 → 用兜底
            return text[:MAX_PROFILE_CHARS]
        logger.warning(
            "[biznav] profile LLM output too short (%d chars), use facts fallback", len(text)
        )
    except Exception as exc:
        logger.warning("[biznav] profile LLM unavailable (%s), use facts fallback", exc)
    return fallback


# ---- helpers ---------------------------------------------------------------


def _read_head(path: Path, chars: int) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="ignore")[:chars].strip()
    except OSError:
        return ""


def _grep_deps(pyproject_text: str) -> list[str]:
    """从 pyproject.toml 粗提 dependencies 列表名（不做 TOML 解析，避免引依赖）。"""
    deps: list[str] = []
    in_deps = False
    for line in pyproject_text.splitlines():
        stripped = line.strip()
        if stripped.startswith("dependencies"):
            in_deps = True
        if in_deps:
            if stripped.startswith('"') and '"' in stripped[1:]:
                name = stripped.strip('",').split(">=")[0].split("==")[0].split("<")[0].strip()
                if name:
                    deps.append(name)
            if stripped.endswith("]"):
                in_deps = False
        if len(deps) >= 15:
            break
    return deps
