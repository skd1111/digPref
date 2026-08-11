"""Skill 分享（zip 导出/导入）。

V1 简化：用 zipfile 把多个 skill yaml 打成一个 zip 供下载；导入侧解 zip
校验每个 yaml 后调 loader.load_one()。

设计：
- 导出：`export_zip(skills) -> bytes` —— 返回 zip 字节流（FastAPI 用 Response 包）
- 导入：`import_zip(zip_bytes, loader) -> ImportReport` —— 解压 → 校验 → 写文件 → load_one
- 不持久化历史导入记录（V2 可加 audit 表）

遵守 CLAUDE.md：
- 不进 shared-protocol（前端自行处理 .zip 文件下载/上传）
- 不动审计 schema（V2 再考虑）
- DSN 检查复用 schema.validate_no_dsn
"""

from __future__ import annotations

import io
import logging
import zipfile
from dataclasses import dataclass, field
from pathlib import Path

import yaml

from agent.skills.loader import SkillLoader
from agent.skills.models import Skill
from agent.skills.schema import validate_no_dsn, validate_skill_yaml

logger = logging.getLogger(__name__)


@dataclass
class ImportReport:
    """导入结果汇总。"""

    imported: list[str] = field(default_factory=list)  # 成功导入的 skill id
    skipped: list[str] = field(default_factory=list)  # 因已存在而跳过的 id
    errors: list[dict] = field(default_factory=list)  # [{filename, reason}]

    def to_dict(self) -> dict:
        return {
            "imported": list(self.imported),
            "skipped": list(self.skipped),
            "errors": list(self.errors),
            "summary": {
                "imported": len(self.imported),
                "skipped": len(self.skipped),
                "failed": len(self.errors),
            },
        }


def export_zip(skills: list[Skill]) -> bytes:
    """把 skill 列表打成 zip 字节流。

    zip 内每个文件：`<skill_id>.yaml`（不允许 skill id 含路径分隔符 / 特殊字符）。
    """
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, mode="w", compression=zipfile.ZIP_DEFLATED) as zf:
        for skill in skills:
            if "/" in skill.id or "\\" in skill.id or ".." in skill.id:
                logger.warning("export_zip skip unsafe id=%s", skill.id)
                continue
            data = yaml.safe_dump(skill.to_dict(), allow_unicode=True, sort_keys=False)
            zf.writestr(f"{skill.id}.yaml", data)
    return buf.getvalue()


def import_zip(zip_bytes: bytes, loader: SkillLoader, overwrite: bool = False) -> ImportReport:
    """从 zip 字节流导入 skill。

    流程：
    1. 解压所有 `.yaml` 成员
    2. 逐个 validate（schema + DSN）
    3. 目标目录有同名文件 → overwrite=False 跳过；否则覆盖
    4. 写文件 + loader.load_one()
    """
    report = ImportReport()

    try:
        zf = zipfile.ZipFile(io.BytesIO(zip_bytes), mode="r")
    except zipfile.BadZipFile as e:
        report.errors.append({"filename": "<zip>", "reason": f"bad zip: {e}"})
        return report

    members = [n for n in zf.namelist() if n.endswith(".yaml") and not n.startswith("__MACOSX/")]
    if not members:
        report.errors.append({"filename": "<zip>", "reason": "no .yaml members found"})
        return report

    for name in members:
        # 防 zip slip
        if "/" in name or "\\" in name or ".." in Path(name).parts:
            report.errors.append({"filename": name, "reason": "unsafe path (zip slip)"})
            continue

        try:
            raw = zf.read(name)
            data = yaml.safe_load(raw)
        except Exception as e:
            report.errors.append({"filename": name, "reason": f"read/yaml parse: {e}"})
            continue

        if not isinstance(data, dict):
            report.errors.append({"filename": name, "reason": "yaml root must be a dict"})
            continue

        schema_errors = validate_skill_yaml(data)
        if schema_errors:
            report.errors.append({"filename": name, "reason": f"schema: {schema_errors}"})
            continue

        dsn_errors = validate_no_dsn(data)
        if dsn_errors:
            report.errors.append({"filename": name, "reason": f"dsn: {dsn_errors}"})
            continue

        skill_id = data.get("id")
        target = loader._dir / f"{skill_id}.yaml"
        if target.exists() and not overwrite:
            report.skipped.append(skill_id)
            continue

        target.parent.mkdir(parents=True, exist_ok=True)
        with open(target, "w", encoding="utf-8") as f:
            yaml.safe_dump(data, f, allow_unicode=True, sort_keys=False)
        loaded = loader.load_one(target)
        if not loaded:
            report.errors.append({"filename": name, "reason": "load_one returned None"})
            continue
        report.imported.append(skill_id)

    return report
