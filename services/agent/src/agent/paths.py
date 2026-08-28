"""paths —— 运行时数据根的统一解析（BUGFIX #98：资产/运行库收敛到安装目录）。

用户要求：所有用户可见资产（skills / expert_teams）与运行库（*.db）
统一落在安装目录（与 config/ 同父级），不再散落 %APPDATA%\\eaide。

解析优先级：
  1. $EAIDE_DATA_ROOT —— Tauri 生产启动注入
     （Windows = 安装目录；macOS = ~/Library/Application Support/eaide）
  2. %APPDATA%\\eaide —— 未注入时的兜底（开发模式 / 独立运行）

旧 %APPDATA%\\eaide 数据已由用户手动清理，不做自动迁移（不留搬运代码）。

工作空间（workspace_dir）—— 底层规则（用户要求 2026-08-17）：
  智能体运行中创建的任何文件默认都落到当前工作空间内，并按类型自动
  分类建目录；仅当用户显式指定了输出目录时才尊重用户指定。
  解析优先级：$EAIDE_WORKSPACE_DIR > workspace.json 自定义 > 数据根/workspace。

任务级工作目录（task_dir，用户要求 2026-08-26）：
  一个聊天页签 = 一个任务文件夹：运行期产生的任何文件都落到
  workspace/tasks/<yyyymmdd-HHMMSS>_<首问摘要>/ 内（分类子目录保留在任务目录内）。
  模型自造的绝对路径（未出现在用户对话原文中）不再豁免，一并重定向到任务目录；
  仅当路径在用户 prompt/history 中原样出现过才视为用户显式指定。
"""

from __future__ import annotations

import json
import os
import re
import threading
from datetime import datetime
from pathlib import Path


def data_root() -> Path:
    """运行时数据根目录（资产 + 运行库的统一父级）。"""
    if root := os.environ.get("EAIDE_DATA_ROOT"):
        return Path(root).expanduser()
    appdata = os.environ.get("APPDATA")
    if appdata:
        return Path(appdata) / "eaide"
    return Path.home() / ".eaide"


# ---------------------------------------------------------------------------
# 工作空间
# ---------------------------------------------------------------------------


def _workspace_config_path() -> Path:
    """自定义工作空间配置的持久化文件（与 llm-config.json 同机制）。

    Tauri 拉起 Agent 时注入 EAIDE_CONFIG_DIR=<安装目录>/config；
    开发模式 cwd = 项目根（config/ 同样存在）。
    """
    from agent.config import settings

    cfg_dir = os.environ.get("EAIDE_CONFIG_DIR")
    if cfg_dir:
        return Path(cfg_dir) / settings.workspace_config_path
    return Path(settings.workspace_config_path)


def load_workspace_override() -> str | None:
    """读用户在设置页自定义的工作空间路径（未配置/不可读返 None）。"""
    try:
        with open(_workspace_config_path(), encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        return None
    raw = data.get("path") if isinstance(data, dict) else None
    if isinstance(raw, str) and raw.strip():
        return raw.strip()
    return None


def save_workspace_override(path: str | None) -> None:
    """写自定义工作空间路径（None / 空串 = 恢复默认，删掉自定义值）。"""
    target = _workspace_config_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    payload = {"path": path.strip()} if path and path.strip() else {}
    target.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def workspace_dir(*, ensure: bool = True) -> Path:
    """当前工作空间目录（智能体产出文件的默认落盘根）。

    优先级：
      1. $EAIDE_WORKSPACE_DIR（测试/部署显式注入）
      2. 设置页自定义（workspace.json）
      3. 默认 = 数据根/workspace（生产即安装目录/workspace）
    """
    raw = os.environ.get("EAIDE_WORKSPACE_DIR") or load_workspace_override()
    if raw:
        p = Path(raw).expanduser()
    else:
        p = data_root() / "workspace"
    resolved = p.resolve(strict=False)
    if ensure:
        resolved.mkdir(parents=True, exist_ok=True)
    return resolved


# 扩展名 → 分类（docs / data / images / other）
_IMAGE_EXTS = frozenset(
    {
        ".png",
        ".jpg",
        ".jpeg",
        ".gif",
        ".bmp",
        ".webp",
        ".svg",
        ".ico",
        ".tiff",
        ".tif",
        ".heic",
    }
)
_DATA_EXTS = frozenset(
    {
        ".csv",
        ".tsv",
        ".xlsx",
        ".xls",
        ".parquet",
        ".json",
        ".sqlite",
        ".db",
        ".ndjson",
        ".jsonl",
        ".feather",
        ".arrow",
    }
)
_DOC_EXTS = frozenset(
    {
        ".md",
        ".txt",
        ".docx",
        ".doc",
        ".pdf",
        ".rtf",
        ".html",
        ".htm",
        ".pptx",
        ".ppt",
        ".log",
    }
)


def classify_category(filename: str) -> str:
    """按扩展名推断分类（docs / data / images / other）。"""
    ext = Path(filename).suffix.lower()
    if ext in _IMAGE_EXTS:
        return "images"
    if ext in _DATA_EXTS:
        return "data"
    if ext in _DOC_EXTS:
        return "docs"
    return "other"


def _category_subdir(category: str) -> str:
    from agent.config import settings

    return {
        "docs": settings.workspace_subdir_docs,
        "data": settings.workspace_subdir_data,
        "images": settings.workspace_subdir_images,
    }.get(category, settings.workspace_subdir_other)


def is_user_specified_output(path: str, context_texts: tuple[str, ...] | None = None) -> bool:
    """判断路径是否为用户显式指定的输出位置。

    收紧（2026-08-26）：此前绝对路径一律视为用户指定 → 模型自造
    C:\\Users\\xxx\\a.pptx 也豁免，产物散落用户目录。现改为：
      - 提供了对话上下文（context_texts）：路径在 prompt/history 原文中出现过才算用户指定；
      - 未提供上下文（兼容旧调用）：维持绝对路径即豁免的旧行为。
    """
    p = Path(path)
    if not p.is_absolute():
        return False
    if context_texts is None:
        return True
    needle = str(p).lower().replace("/", "\\")
    for text in context_texts:
        if not text:
            continue
        hay = text.lower().replace("/", "\\")
        if needle in hay or str(p).lower() in text.lower():
            return True
    return False


# ---------------------------------------------------------------------------
# 任务级工作目录（2026-08-26）
# ---------------------------------------------------------------------------

# 任务目录名 slug 合法字符：CJK / 字母数字 / 下划线；其余（含路径分隔符）剔除防穿越
_SLUG_KEEP_RE = re.compile(r"[^\w\u4e00-\u9fff]+", flags=re.UNICODE)
_TASKS_INDEX_NAME = "tasks-index.json"
_TASK_LEDGER_NAME = "task-ledger.json"
# 多会话并发（2026-08-26）：索引/台账都是整文件读-改-写，无锁时并发 run 会互相覆盖丢条目，
# 单进程多线程用 threading.Lock 串行即可（写量很小，不会成瓶颈）。
_TASK_FILE_LOCK = threading.Lock()


def _tasks_index_path() -> Path:
    return data_root() / _TASKS_INDEX_NAME


def _load_tasks_index() -> dict[str, str]:
    try:
        with open(_tasks_index_path(), encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, dict):
            return {str(k): str(v) for k, v in data.items() if isinstance(v, str)}
        return {}
    except (OSError, ValueError):
        return {}


def _save_tasks_index(index: dict[str, str]) -> None:
    target = _tasks_index_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(index, ensure_ascii=False, indent=2), encoding="utf-8")


def _task_slug(task_title: str | None, task_id: str) -> str:
    """任务目录名后缀：首问摘要（前 12 字）；非法字符剔除，空则用 task_id 前 8 位。"""
    raw = (task_title or "").strip()[:12]
    slug = _SLUG_KEEP_RE.sub("", raw)
    if not slug:
        slug = _SLUG_KEEP_RE.sub("", task_id)[:8] or "task"
    return slug


def task_dir(
    task_id: str | None, task_title: str | None = None, *, ensure: bool = True
) -> Path | None:
    """当前任务的工作目录（一个聊天页签 = 一个任务文件夹）。

    目录 = workspace_dir()/tasks/<yyyymmdd-HHMMSS>_<首问摘要>；同一 task_id 复用
    （映射持久化到 data_root/tasks-index.json）。task_id 为空返 None（回退旧规则）。
    """
    tid = (task_id or "").strip()
    if not tid:
        return None
    with _TASK_FILE_LOCK:
        index = _load_tasks_index()
        rel = index.get(tid)
        if not isinstance(rel, str) or not rel or "/" in rel or "\\" in rel or rel.startswith("."):
            ts = datetime.now().strftime("%Y%m%d-%H%M%S")
            rel = f"{ts}_{_task_slug(task_title, tid)}"
            index[tid] = rel
            try:
                _save_tasks_index(index)
            except OSError:
                pass  # 索引落盘失败不阻断（下次会重建目录，不影响功能）
    d = workspace_dir(ensure=False) / "tasks" / rel
    if ensure:
        d.mkdir(parents=True, exist_ok=True)
    return d.resolve(strict=False)


def ledger_record(task_id: str | None, path: str, kind: str) -> None:
    """任务台账：记录任务目录内产生的文件（artifact=交付产物 / intermediate=中间文件）。

    存储于 data_root/task-ledger.json；失败静默（台账仅供清理参考，不阻断主链路）。
    """
    tid = (task_id or "").strip()
    if not tid or not path:
        return
    if kind not in ("artifact", "intermediate"):
        return
    try:
        target = data_root() / _TASK_LEDGER_NAME
        with _TASK_FILE_LOCK:
            data: dict[str, dict[str, list[str]]] = {}
            if target.exists():
                with open(target, encoding="utf-8") as f:
                    loaded = json.load(f)
                if isinstance(loaded, dict):
                    data = loaded
            entry = data.get(tid)
            if not isinstance(entry, dict):
                entry = {"artifacts": [], "intermediates": []}
            key = "artifacts" if kind == "artifact" else "intermediates"
            norm = str(Path(path).resolve(strict=False))
            if norm not in entry[key]:
                entry[key].append(norm)
            data[tid] = entry
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    except (OSError, ValueError):
        pass


def ledger_read(task_id: str | None) -> dict[str, list[str]]:
    """读任务台账：{"artifacts": [...], "intermediates": [...]}（无记录返空列表）。"""
    tid = (task_id or "").strip()
    if not tid:
        return {"artifacts": [], "intermediates": []}
    try:
        with open(data_root() / _TASK_LEDGER_NAME, encoding="utf-8") as f:
            data = json.load(f)
        entry = data.get(tid) if isinstance(data, dict) else None
        if isinstance(entry, dict):
            return {
                "artifacts": [str(p) for p in entry.get("artifacts") or []],
                "intermediates": [str(p) for p in entry.get("intermediates") or []],
            }
    except (OSError, ValueError):
        pass
    return {"artifacts": [], "intermediates": []}


def resolve_output_path(
    path: str,
    *,
    category: str | None = None,
    task_root: Path | None = None,
    context_texts: tuple[str, ...] | None = None,
) -> Path:
    """底层规则入口：把创建类文件的目标路径解析到当前任务目录/工作空间内。

    - 用户显式指定（绝对路径且出现在对话原文，见 is_user_specified_output）→ 原样返回；
    - 模型自造的绝对路径（2026-08-26 收紧）→ 取文件名落任务目录，防散落用户目录；
    - 其余 → <基目录>/<分类子目录>/<文件名>（基目录 = task_root > workspace_dir，
      分类子目录保留）；用户给的相对子目录（如 "sub/a.txt"）保留层级在基目录内拼接。
    """
    p = Path(path).expanduser()
    base = task_root or workspace_dir()

    if p.is_absolute():
        if is_user_specified_output(path, context_texts):
            resolved = p.resolve(strict=False)
            resolved.parent.mkdir(parents=True, exist_ok=True)
            return resolved
        # 模型自造绝对路径：只取文件名落任务目录（防 C:\Users\xxx\a.pptx 类散落）
        p = Path(p.name)

    # 相对路径：带目录片段时保留层级（仍在基目录内）
    if len(p.parts) > 1:
        target = base / p
        target.parent.mkdir(parents=True, exist_ok=True)
        return target.resolve(strict=False)

    cat = category or classify_category(p.name)
    target_dir = base / _category_subdir(cat)
    target_dir.mkdir(parents=True, exist_ok=True)
    return (target_dir / p.name).resolve(strict=False)
