"""Phase 1B V9 · Office 文档工具族（OfficeCLI，2026-08-25）。

对照开发模式 Office 能力增强规划，以 OfficeCLI（单二进制、无需装 Office）为引擎，
在 V5/V6 文档工具族之上补齐 docx / xlsx / pptx 的「细粒度读 / 改 / 建 / 校验」：

工具清单：
    - office_read      结构 / 文本读取（view outline/text/annotated/stats + get/query，read）
    - office_edit      元素级修改（set/add/remove/move/batch；写文件，medium → HITL）
    - office_create    新建文档 + 模板 merge（{{key}} 填充；写文件，medium → HITL）
    - office_validate  OpenXML 校验 + 问题枚举（read，供 Agent「改完→校验→修复」闭环）

安全约束（与 V0-V6 一致）：
    - 所有路径先走 path_sandbox.validate_path()（Windows 保留名 / UNC / null byte）。
    - 写工具输出路径默认禁止覆盖（overwrite=True 显式放行）。
    - OfficeCLI 二进制缺失时返友好错误（不崩溃、不降级到任何外部服务）。
"""

from __future__ import annotations

import json
import tempfile
from pathlib import Path
from typing import Any

from agent.builtin.models import ToolResult
from agent.builtin.officecli_runtime import (
    OFFICE_SUFFIXES,
    OfficeCliOutcome,
    run_officecli,
)
from agent.builtin.path_sandbox import validate_path

# batch 命令数上限（防超大 payload 卡死子进程）
_MAX_BATCH_COMMANDS = 200

_VIEW_MODES = frozenset({"outline", "text", "annotated", "stats"})


def _outcome_failed(outcome: OfficeCliOutcome, risk_level: str) -> ToolResult:
    """OfficeCLI 失败结果 → ToolResult（保留结构化错误码 / 建议供自愈）。"""
    return ToolResult(
        ok=False,
        error=outcome.error or "officecli_failed",
        hint=outcome.suggestion or outcome.message,
        meta={"exit_code": outcome.exit_code},
        risk_level=risk_level,  # type: ignore[arg-type]
    )


def _validate_office_file(path: str, risk_level: str) -> Path | ToolResult:
    """路径沙箱校验 + 存在性 + Office 后缀校验。"""
    try:
        p = validate_path(path, must_exist=True)
    except Exception as exc:
        return ToolResult.from_exception(exc, risk_level=risk_level)  # type: ignore[arg-type]
    if not p.is_file() or p.suffix.lower() not in OFFICE_SUFFIXES:
        return ToolResult(
            ok=False,
            error="not_an_office_file",
            hint=f"仅支持 docx / xlsx / pptx: {p}",
            risk_level=risk_level,  # type: ignore[arg-type]
        )
    return p


def _prop_args(props: dict[str, Any] | None) -> list[str]:
    """props dict → 重复的 --prop k=v 参数（bool → true/false）。"""
    out: list[str] = []
    for k, v in (props or {}).items():
        if isinstance(v, bool):
            v = "true" if v else "false"
        out.extend(["--prop", f"{k}={v}"])
    return out


def builtin_office_read(
    *,
    path: str,
    action: str = "outline",
    element_path: str | None = None,
    query: str | None = None,
    depth: int = 1,
) -> ToolResult:
    """读取 Office 文档的结构 / 文本（只读）。

    action:
        outline / text / annotated / stats  OfficeCLI view 语义视图
        get    取 element_path 元素及子元素（--depth 层数，JSON）
        query  CSS 风格选择器查询（如 'paragraph[style=Heading1]'，JSON）
    """
    src = _validate_office_file(path, "read")
    if isinstance(src, ToolResult):
        return src
    d = max(1, min(int(depth), 8))

    if action in _VIEW_MODES:
        outcome = run_officecli(["view", str(src), action], as_json=False)
        if not outcome.ok:
            return _outcome_failed(outcome, "read")
        text = str(outcome.data or "").strip()
        return ToolResult(
            ok=True,
            content=text,
            meta={"mode": action, "chars": len(text), "source": str(src)},
            risk_level="read",
        )
    if action == "get":
        target = (element_path or "/").strip() or "/"
        outcome = run_officecli(["get", str(src), target, "--depth", str(d)])
        if not outcome.ok:
            return _outcome_failed(outcome, "read")
        return ToolResult(ok=True, content=outcome.data, risk_level="read")
    if action == "query":
        if not query or not query.strip():
            return ToolResult(
                ok=False,
                error="missing_query",
                hint="action=query 需要 query 选择器（如 'run:contains(TODO)'）",
                risk_level="read",
            )
        outcome = run_officecli(["query", str(src), query.strip()])
        if not outcome.ok:
            return _outcome_failed(outcome, "read")
        return ToolResult(ok=True, content=outcome.data, risk_level="read")
    return ToolResult(
        ok=False,
        error="invalid_action",
        hint="action 必须是 outline / text / annotated / stats / get / query",
        risk_level="read",
    )


def builtin_office_edit(
    *,
    path: str,
    op: str,
    element_path: str = "/",
    type: str | None = None,
    props: dict[str, Any] | None = None,
    to_parent: str | None = None,
    index: int | None = None,
    selector: str | None = None,
    commands: list[dict[str, Any]] | None = None,
) -> ToolResult:
    """元素级修改 Office 文档（medium 风险，走 HITL）。

    op:
        set     修改属性：selector（或 element_path）定位 + props 键值对
        add     新增元素：element_path 为父路径 + type 元素类型 + props
        remove  删除元素：element_path 定位
        move    移动元素：element_path + to_parent（+ index）
        batch   批量原子执行：commands 为命令列表（任一失败整体回滚）

    路径寻址用 OfficeCLI 语法（1 起索引，如 /slide[1]/shape[2]），非 XPath。
    """
    if op not in ("set", "add", "remove", "move", "batch"):
        return ToolResult(
            ok=False,
            error="invalid_op",
            hint="op 必须是 set / add / remove / move / batch",
            risk_level="medium",
        )
    src = _validate_office_file(path, "medium")
    if isinstance(src, ToolResult):
        return src

    if op == "batch":
        if not commands or not isinstance(commands, list):
            return ToolResult(
                ok=False,
                error="empty_commands",
                hint="batch 需要非空 commands 列表",
                risk_level="medium",
            )
        if len(commands) > _MAX_BATCH_COMMANDS:
            return ToolResult(
                ok=False,
                error="too_many_commands",
                hint=f"单次批量上限 {_MAX_BATCH_COMMANDS} 条",
                risk_level="medium",
            )
        batch_file = ""
        try:
            with tempfile.NamedTemporaryFile(
                "w", suffix=".json", delete=False, encoding="utf-8"
            ) as fh:
                json.dump(
                    [
                        {
                            "command": c.get("command", c.get("op")),
                            **{k: v for k, v in c.items() if k not in ("command", "op")},
                        }
                        for c in commands
                    ],
                    fh,
                    ensure_ascii=False,
                )
                batch_file = fh.name
            outcome = run_officecli(["batch", str(src), "--input", batch_file])
        except Exception as exc:
            return ToolResult.from_exception(exc, risk_level="medium")
        finally:
            if batch_file:
                try:
                    Path(batch_file).unlink(missing_ok=True)
                except OSError:
                    pass
        if not outcome.ok:
            return _outcome_failed(outcome, "medium")
        return ToolResult(
            ok=True,
            content={"path": str(src), "applied": len(commands)},
            meta={"op": "batch"},
            risk_level="medium",
        )

    target = (selector or element_path or "/").strip() or "/"
    args: list[str] = []
    if op == "set":
        if not props:
            return ToolResult(
                ok=False, error="empty_props", hint="set 需要 props 键值对", risk_level="medium"
            )
        args = ["set", str(src), target, *_prop_args(props)]
    elif op == "add":
        if not type:
            return ToolResult(
                ok=False, error="missing_type", hint="add 需要 type 元素类型", risk_level="medium"
            )
        args = ["add", str(src), target, "--type", type, *_prop_args(props)]
    elif op == "remove":
        args = ["remove", str(src), target]
    else:  # move
        if not to_parent:
            return ToolResult(
                ok=False, error="missing_to_parent", hint="move 需要 to_parent", risk_level="medium"
            )
        args = ["move", str(src), target, "--to", to_parent]
        if index is not None:
            args.extend(["--index", str(int(index))])

    outcome = run_officecli(args)
    if not outcome.ok:
        return _outcome_failed(outcome, "medium")
    return ToolResult(
        ok=True,
        content={"path": str(src), "op": op, "result": outcome.data},
        risk_level="medium",
    )


def builtin_office_create(
    *,
    path: str,
    template: str | None = None,
    data: dict[str, Any] | None = None,
    overwrite: bool = False,
) -> ToolResult:
    """新建 Office 文档，或按模板 {{key}} 填充生成（medium 风险，走 HITL）。

    - 无 template：create 空白 docx / xlsx / pptx（类型由扩展名决定）
    - 有 template：merge 模板占位符，data 为 {key: 值}；模板布局一次设计、
      批量确定性填充，避免 Agent 逐份重新生成导致版式漂移
    """
    try:
        out = validate_path(path, must_exist=False)
    except Exception as exc:
        return ToolResult.from_exception(exc, risk_level="medium")
    if out.suffix.lower() not in OFFICE_SUFFIXES:
        return ToolResult(
            ok=False,
            error="unsupported_format",
            hint=f"仅支持创建 docx / xlsx / pptx: {out}",
            risk_level="medium",
        )
    if out.exists() and not overwrite:
        return ToolResult(
            ok=False,
            error="exists_no_overwrite",
            hint=f"目标文件已存在: {out}（传 overwrite=True 覆盖）",
            risk_level="medium",
        )
    if out.parent and not out.parent.exists():
        out.parent.mkdir(parents=True, exist_ok=True)

    if template:
        try:
            tpl = validate_path(template, must_exist=True)
        except Exception as exc:
            return ToolResult.from_exception(exc, risk_level="medium")
        if not tpl.is_file() or tpl.suffix.lower() not in OFFICE_SUFFIXES:
            return ToolResult(
                ok=False,
                error="not_an_office_file",
                hint=f"模板必须是 docx / xlsx / pptx: {tpl}",
                risk_level="medium",
            )
        if not data:
            return ToolResult(
                ok=False,
                error="empty_data",
                hint="模板 merge 需要 data（{{key}} → 值）",
                risk_level="medium",
            )
        data_file = ""
        try:
            with tempfile.NamedTemporaryFile(
                "w", suffix=".json", delete=False, encoding="utf-8"
            ) as fh:
                json.dump(data, fh, ensure_ascii=False)
                data_file = fh.name
            outcome = run_officecli(["merge", str(tpl), str(out), "--data", data_file])
        except Exception as exc:
            return ToolResult.from_exception(exc, risk_level="medium")
        finally:
            if data_file:
                try:
                    Path(data_file).unlink(missing_ok=True)
                except OSError:
                    pass
        if not outcome.ok:
            return _outcome_failed(outcome, "medium")
        return ToolResult(
            ok=True,
            content={"path": str(out), "template": str(tpl), "keys": sorted(data)},
            meta={"path": str(out)},
            risk_level="medium",
        )

    outcome = run_officecli(["create", str(out)])
    if not outcome.ok:
        return _outcome_failed(outcome, "medium")
    return ToolResult(
        ok=True, content={"path": str(out)}, meta={"path": str(out)}, risk_level="medium"
    )


def builtin_office_validate(*, path: str) -> ToolResult:
    """校验 Office 文档（OpenXML schema）并枚举质量问题（只读）。

    供 Agent「改完 → 校验 → 按 issues 修复」自愈闭环：
    issues 含文本溢出 / 缺 alt 文本 / 公式错误 / 元素遮挡等。
    """
    src = _validate_office_file(path, "read")
    if isinstance(src, ToolResult):
        return src

    validate_outcome = run_officecli(["validate", str(src)])
    # 基础设施类错误（未安装 / 超时 / 启动失败）不能当「校验通过」掩盖，直接返失败
    if validate_outcome.error in ("officecli_not_installed", "timed_out", "spawn_failed"):
        return _outcome_failed(validate_outcome, "read")
    issues_outcome = run_officecli(["view", str(src), "issues"])

    valid = validate_outcome.ok
    issues: Any = issues_outcome.data if issues_outcome.ok else None
    # OfficeCLI --json 返回 {success, data: {count, issues}}；解包到问题列表层，
    # 降低 Agent 消费成本（解包失败保留原结构）
    if isinstance(issues, dict) and isinstance(issues.get("data"), dict):
        inner = issues["data"]
        if "issues" in inner:
            issues = inner["issues"]
    content: dict[str, Any] = {
        "valid": valid,
        "issues": issues,
        "validate_message": validate_outcome.message or None,
    }
    return ToolResult(
        ok=True,
        content=content,
        meta={"source": str(src)},
        risk_level="read",
    )
