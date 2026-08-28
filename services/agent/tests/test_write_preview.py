"""执行过程可视化（阶段四） · 写前 unified diff 预览测试。

验证 `_preview_unified_diff` 的计算语义：
1. write_file 新建 / 覆盖都能构造 diff；
2. edit_file 镜像工具语义（无匹配 / 多匹配未 replace_all → 不出预览）；
3. 只读不写盘（预览失败也不影响审批闸门）。
"""

from __future__ import annotations

from agent.builtin.dispatcher import _preview_unified_diff


def test_write_file_new_file_diff(tmp_path) -> None:
    target = tmp_path / "new.txt"
    diff = _preview_unified_diff("write_file", {"content": "hello\nworld\n"}, str(target))
    assert "+hello" in diff
    assert "+world" in diff
    assert target.exists() is False  # 只读预览，绝不落盘


def test_write_file_overwrite_diff(tmp_path) -> None:
    target = tmp_path / "exist.txt"
    target.write_text("old\nkeep\n", encoding="utf-8")
    diff = _preview_unified_diff("write_file", {"content": "new\nkeep\n"}, str(target))
    assert "-old" in diff
    assert "+new" in diff
    # 原文件未被预览改动
    assert target.read_text(encoding="utf-8") == "old\nkeep\n"


def test_edit_file_unique_match_diff(tmp_path) -> None:
    target = tmp_path / "code.py"
    target.write_text("a = 1\nb = 2\n", encoding="utf-8")
    diff = _preview_unified_diff(
        "edit_file",
        {"search_text": "b = 2", "replace_text": "b = 42"},
        str(target),
    )
    assert "-b = 2" in diff
    assert "+b = 42" in diff


def test_edit_file_no_match_returns_empty(tmp_path) -> None:
    target = tmp_path / "code.py"
    target.write_text("a = 1\n", encoding="utf-8")
    diff = _preview_unified_diff(
        "edit_file",
        {"search_text": "not_there", "replace_text": "x"},
        str(target),
    )
    assert diff == ""


def test_edit_file_ambiguous_match_returns_empty(tmp_path) -> None:
    """多匹配未 replace_all → 工具会拒绝，预览也不出（与实际行为一致）。"""
    target = tmp_path / "dup.py"
    target.write_text("x\nx\n", encoding="utf-8")
    diff = _preview_unified_diff(
        "edit_file",
        {"search_text": "x", "replace_text": "y"},
        str(target),
    )
    assert diff == ""


def test_diff_size_capped(tmp_path) -> None:
    target = tmp_path / "big.txt"
    diff = _preview_unified_diff("write_file", {"content": "line\n" * 100000}, str(target))
    assert len(diff) <= 65536
