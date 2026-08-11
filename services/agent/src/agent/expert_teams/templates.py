"""专家团交付物报告模板存储（2026-08-10）。

模板目录：%APPDATA%\\eaide\\expert_teams\\templates\\（与专家团 YAML 同父级）。
解析顺序（ops 导出时用，见 agent/ops/report_template.py）：
  1. ExpertTeam.report_template 显式指定的文件名；
  2. 自动探测 {team_id}.docx → {team_id}.md；
  3. 都没有 → 内置报告结构（ops/api.py 硬编码版）。

红线：模板是可选增强，任何缺失/损坏都只降级，绝不阻塞交付物导出。
"""

from __future__ import annotations

import base64
import logging
from pathlib import Path

from agent.expert_teams.loader import EXPERT_TEAMS_DIR

logger = logging.getLogger(__name__)

# 允许的模板后缀（docx 占位符模板 / md 文本模板）
ALLOWED_TEMPLATE_SUFFIXES = frozenset({".docx", ".md"})

MAX_TEMPLATE_BYTES = 10 * 1024 * 1024  # 10MB，防止误传超大文件


def templates_dir() -> Path:
    d = EXPERT_TEAMS_DIR / "templates"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _safe_template_name(file_name: str) -> str:
    """文件名安全化：只取 basename，后缀必须在白名单内（否则抛 ValueError）。"""
    name = Path(file_name or "").name.strip()
    if not name:
        raise ValueError("模板文件名为空")
    if Path(name).suffix.lower() not in ALLOWED_TEMPLATE_SUFFIXES:
        raise ValueError(f"模板仅支持 {sorted(ALLOWED_TEMPLATE_SUFFIXES)} 格式")
    return name


def save_template(team_id: str, file_name: str, content_base64: str) -> Path:
    """base64 模板落盘到 templates/ 目录，返回存储路径。"""
    name = _safe_template_name(file_name)
    raw = base64.b64decode(content_base64)
    if len(raw) > MAX_TEMPLATE_BYTES:
        raise ValueError("模板文件超过 10MB 限制")
    target = templates_dir() / name
    target.parent.mkdir(parents=True, exist_ok=True)
    # 路径穿越兜底：resolve 后必须仍在 templates 目录内
    if not target.resolve().is_relative_to(templates_dir().resolve()):
        raise ValueError("非法模板路径")
    target.write_bytes(raw)
    logger.info("[expert-team-template] saved %s for team %s (%d bytes)", name, team_id, len(raw))
    return target


def resolve_template_path(team_id: str, report_template: str) -> Path | None:
    """按解析顺序定位模板文件；不存在返 None（调用方降级内置结构）。"""
    d = templates_dir()
    if report_template:
        try:
            name = _safe_template_name(report_template)
        except ValueError as e:
            logger.warning(
                "[expert-team-template] invalid report_template %r: %s", report_template, e
            )
            name = ""
        if name:
            p = d / name
            if p.is_file():
                return p
            logger.warning(
                "[expert-team-template] %s 配置的模板 %s 不存在，降级探测默认名", team_id, name
            )
    for candidate in (f"{team_id}.docx", f"{team_id}.md"):
        p = d / candidate
        if p.is_file():
            return p
    return None


def delete_template(team_id: str, report_template: str) -> bool:
    """删除当前生效的模板文件；没有可删的返 False。"""
    p = resolve_template_path(team_id, report_template)
    if p is None:
        return False
    try:
        p.unlink()
        logger.info("[expert-team-template] deleted %s", p.name)
        return True
    except OSError as e:
        logger.warning("[expert-team-template] delete %s failed: %s", p.name, e)
        return False
