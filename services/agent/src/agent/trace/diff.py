"""Phase 16 · unified diff 计算（difflib 标准库，禁用第三方库）。

性能要求（架构师忠告 3）：
    - 后端预计算并缓存（collector 调用一次，storage 持久化）
    - 大文件只保留关键片段：preview 截取变更行前后各 PREVIEW_CONTEXT 行
"""
from __future__ import annotations

import difflib

# diff 预览片段：变更行前后各保留的行数
PREVIEW_CONTEXT = 50
# diff 全文入库上限（行）—— 超过只存 preview，避免 trace.db 膨胀
MAX_DIFF_LINES = 2000


def compute_unified_diff(
    before: str,
    after: str,
    path: str = "",
    *,
    context: int = 3,
) -> str:
    """计算 unified diff 文本。空输入合法（新建文件 before=""）。"""
    before_lines = before.splitlines(keepends=True)
    after_lines = after.splitlines(keepends=True)
    diff_iter = difflib.unified_diff(
        before_lines,
        after_lines,
        fromfile=f"a/{path}" if path else "a",
        tofile=f"b/{path}" if path else "b",
        n=context,
    )
    return "".join(diff_iter)


def diff_stats(diff_text: str) -> tuple[int, int]:
    """统计 +/- 行数（忽略 +++ / --- 文件头）。"""
    added = 0
    removed = 0
    for line in diff_text.splitlines():
        if line.startswith("+++") or line.startswith("---"):
            continue
        if line.startswith("+"):
            added += 1
        elif line.startswith("-"):
            removed += 1
    return added, removed


def extract_preview(diff_text: str, max_lines: int = PREVIEW_CONTEXT * 2) -> str:
    """截取 diff 关键片段（前端只加载前后 50 行）。

    策略：保留文件头 + 第一个 hunk 起的前 max_lines 行；
    超长时追加截断提示行。
    """
    lines = diff_text.splitlines()
    if len(lines) <= max_lines:
        return diff_text
    kept = lines[:max_lines]
    kept.append(f"@@ ... 已截断，完整 diff 共 {len(lines)} 行 ... @@")
    return "\n".join(kept)


def build_diff_fields(before: str, after: str, path: str) -> dict:
    """一次性计算 diff / preview / 行数统计（写库前调用）。"""
    diff = compute_unified_diff(before, after, path)
    if not diff:
        return {"diff": "", "preview": "", "lines_added": 0, "lines_removed": 0}
    added, removed = diff_stats(diff)
    # 超长 diff 只存 preview（性能红线：trace.db 不膨胀）
    stored_diff = diff
    if diff.count("\n") > MAX_DIFF_LINES:
        stored_diff = extract_preview(diff)
    return {
        "diff": stored_diff,
        "preview": extract_preview(diff),
        "lines_added": added,
        "lines_removed": removed,
    }


def estimate_tokens(text: str) -> int:
    """粗略 token 估算（中文 ~1 字/token，英文 ~4 字符/token 的折中）。"""
    if not text:
        return 0
    return max(1, len(text) // 2)
