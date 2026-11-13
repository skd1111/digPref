"""expert_teams/seed.py —— 内置专家团种子播种（2026-11-04）。

运营工作台 16 个模块此前只有「尽职调查专家团」一个种子，其余业务点选后
无团可选，只能退回 LLM 推荐/手选。这里把随包分发的种子
（expert_teams/seeds/*.yaml + seeds/templates/*.md）在启动时播种到数据根
``<data_root>/expert_teams/``：

    - 目标已存在（用户导入/改过）→ 不覆盖，用户资产优先
    - manifest（.seeded-manifest.json）记账已播种 id：用户删除后不复活
      （与 skills/seed.py 同机制，删除即永久移除，可通过设置页「导入内置专家团」恢复）
    - 每个种子先过 validate_expert_team_yaml + validate_no_dsn，坏种子记日志跳过不抛
    - 交付物模板（templates/*.md）仅在目标不存在时复制，不覆盖用户模板

种子源定位（三级回退，与 skills 种子同策略）：
    1. cwd 相对路径 ``services/agent/src/agent/expert_teams/seeds``
    2. PyInstaller ``_MEIPASS`` 内置副本（spec datas → ``agent/expert_teams/seeds``）
    3. 模块位置推导仓库根（开发态兜底）
"""

from __future__ import annotations

import json
import logging
import sys
from pathlib import Path
from typing import Any

import yaml

from agent.expert_teams.schema import validate_expert_team_yaml, validate_no_dsn

logger = logging.getLogger(__name__)

# 种子源相对路径（1 = cwd / 仓库根；2 = _MEIPASS 内打包落点）
_SEEDS_REL_REPO = "services/agent/src/agent/expert_teams/seeds"
_SEEDS_REL_MEIPASS = "agent/expert_teams/seeds"

_MANIFEST_NAME = ".seeded-manifest.json"
_TEMPLATES_SUBDIR = "templates"


def resolve_seeds_dir() -> Path | None:
    """三级回退定位种子源目录；全部不存在返 None。"""
    cwd_candidate = Path.cwd() / _SEEDS_REL_REPO
    if cwd_candidate.is_dir():
        return cwd_candidate
    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        bundled = Path(meipass) / _SEEDS_REL_MEIPASS
        if bundled.is_dir():
            return bundled
    try:
        # seed.py → expert_teams → agent → src → agent → services → 仓库根（parents[5]）
        repo_root = Path(__file__).resolve().parents[5]
        candidate = repo_root / _SEEDS_REL_REPO
        if candidate.is_dir():
            return candidate
    except IndexError:
        pass
    return None


def _load_manifest(teams_dir: Path) -> set[str]:
    path = teams_dir / _MANIFEST_NAME
    if not path.is_file():
        return set()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        ids = data.get("seeded", [])
        return {str(i) for i in ids if isinstance(i, str)}
    except (json.JSONDecodeError, OSError):
        return set()


def _save_manifest(teams_dir: Path, seeded_ids: set[str]) -> None:
    path = teams_dir / _MANIFEST_NAME
    try:
        path.write_text(
            json.dumps({"seeded": sorted(seeded_ids)}, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    except OSError:
        logger.warning("[expert-team-seed] manifest 写入失败: %s", path)


def _copy_templates(src_dir: Path, teams_dir: Path) -> list[str]:
    """把种子模板复制到 templates/（目标已存在不覆盖）。返回本次复制的文件名。"""
    tpl_src = src_dir / _TEMPLATES_SUBDIR
    if not tpl_src.is_dir():
        return []
    tpl_dst = teams_dir / _TEMPLATES_SUBDIR
    tpl_dst.mkdir(parents=True, exist_ok=True)
    copied: list[str] = []
    for path in sorted(tpl_src.glob("*.md")):
        target = tpl_dst / path.name
        if target.exists():
            continue  # 用户模板优先
        try:
            target.write_bytes(path.read_bytes())
            copied.append(path.name)
        except OSError as exc:
            logger.warning("[expert-team-seed] 模板复制失败 %s: %s", path.name, exc)
    return copied


class _LiteralStr(str):
    """标记「请用字块量风格 ``|`` 输出」的字符串（多行 prompt 回写用）。"""


def _repr_literal(dumper: yaml.SafeDumper, value: object) -> Any:
    return dumper.represent_scalar("tag:yaml.org,2002:str", str(value), style="|")


class _TeamDumper(yaml.SafeDumper):
    """专家团回写专用 dumper：不重复引用、多行串走字块。"""

    def ignore_aliases(self, data: object) -> bool:
        return True


_TeamDumper.add_representer(_LiteralStr, _repr_literal)


def _convert_multiline(node: Any) -> Any:
    if isinstance(node, dict):
        return {k: _convert_multiline(v) for k, v in node.items()}
    if isinstance(node, list):
        return [_convert_multiline(v) for v in node]
    if isinstance(node, str) and "\n" in node:
        return _LiteralStr(node)
    return node


def _dump_team_yaml(data: dict) -> str:
    """回写专家团 yaml（凭证清单补齐用）。

    多行字符串（主要是 prompt）保持字块量风格 ``|``，不让回写把提示词
    压成一整行转义长串；中文不转义、不改 key 顺序、不做引用共享。
    """
    return yaml.dump(
        _convert_multiline(data),
        Dumper=_TeamDumper,
        allow_unicode=True,
        sort_keys=False,
        default_flow_style=False,
        width=10**6,
    )


def backfill_credentials(teams_dir: Path, src_dir: Path) -> list[str]:
    """给已存在的同名团只补 members[*].credentials（2026-11-09 凭证预览）。

    背景：播种“已有文件一律不动”，凭证节点作为新资产无法自动到达已安装用户。
    这里采取最保守的合并：只动目标成员缺失的 ``credentials`` 键（不存在或空列表），
    其他字段（提示词/交付物/表单）一律不动；目标文件本身语法不合法则跳过。
    返回本次补齐的 team id 列表。注：按成员名对齐，用户改过成员名则不匹配。
    """
    patched: list[str] = []
    for path in sorted(src_dir.glob("*.yaml")):
        try:
            seed_data = yaml.safe_load(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        if not isinstance(seed_data, dict):
            continue
        team_id = seed_data.get("id")
        if not isinstance(team_id, str) or not team_id:
            continue
        target = teams_dir / f"{team_id}.yaml"
        if not target.is_file():
            continue  # 新文件走正常播种，不在这里重复写
        seed_by_name = {
            str(m.get("name", "")): m.get("credentials") or []
            for m in seed_data.get("members", [])
            if isinstance(m, dict)
        }
        try:
            local_data = yaml.safe_load(target.read_text(encoding="utf-8"))
        except Exception as exc:
            logger.warning("[expert-team-seed] 凭证补齐读取失败 %s: %s", target.name, exc)
            continue
        if not isinstance(local_data, dict):
            continue
        changed = 0
        for member in local_data.get("members", []):
            if not isinstance(member, dict):
                continue
            name = str(member.get("name", ""))
            preset = seed_by_name.get(name) or []
            if not preset:
                continue
            current = member.get("credentials")
            if isinstance(current, list) and current:
                continue  # 用户已有凭证清单（含自己编辑的）→ 不覆盖
            member["credentials"] = preset
            changed += 1
        if not changed:
            continue
        try:
            target.write_text(_dump_team_yaml(local_data), encoding="utf-8")
        except (OSError, yaml.YAMLError) as exc:
            logger.warning("[expert-team-seed] 凭证补齐回写失败 %s: %s", target, exc)
            continue
        patched.append(team_id)
    return patched


def seed_builtin_expert_teams(teams_dir: Path) -> list[str]:
    """把内置种子播种到 teams_dir；返回本次新播种的 team id 列表。

    幂等且保守：已有文件一律不动；已播过的 id 记入 manifest 后不再复活。
    """
    src_dir = resolve_seeds_dir()
    if src_dir is None or not src_dir.is_dir():
        return []
    try:
        teams_dir.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        logger.warning("[expert-team-seed] 目录不可写 %s: %s", teams_dir, exc)
        return []

    manifest = _load_manifest(teams_dir)
    seeded: list[str] = []

    for path in sorted(src_dir.glob("*.yaml")):
        try:
            data = yaml.safe_load(path.read_text(encoding="utf-8"))
        except Exception as exc:
            logger.warning("[expert-team-seed] skip (unreadable) %s: %s", path.name, exc)
            continue
        if not isinstance(data, dict):
            logger.warning("[expert-team-seed] skip (root not dict) %s", path.name)
            continue
        errors = validate_expert_team_yaml(data)
        if errors:
            logger.warning("[expert-team-seed] skip (schema) %s: %s", path.name, errors)
            continue
        if validate_no_dsn(data):
            logger.warning("[expert-team-seed] skip (dsn pattern) %s", path.name)
            continue
        team_id = data.get("id")
        if not isinstance(team_id, str) or not team_id:
            logger.warning("[expert-team-seed] skip (no id) %s", path.name)
            continue
        if team_id in manifest:
            continue  # 已播过：用户删除后也不复活
        manifest.add(team_id)  # 无论是否落盘都记账（覆盖场景同样不复活）
        target = teams_dir / f"{team_id}.yaml"
        if target.exists():
            continue  # 用户已有同名专家团 → 不覆盖
        try:
            target.write_bytes(path.read_bytes())
        except OSError as exc:
            logger.warning("[expert-team-seed] 写入失败 %s: %s", target, exc)
            continue
        seeded.append(team_id)

    _copy_templates(src_dir, teams_dir)
    _save_manifest(teams_dir, manifest)
    try:
        patched = backfill_credentials(teams_dir, src_dir)
    except Exception as exc:  # pragma: no cover - 补齐失败不影响启动
        logger.warning("[expert-team-seed] 凭证清单补齐异常: %s", exc)
        patched = []
    if seeded:
        logger.info("[expert-team-seed] 内置专家团已播种: %s", seeded)
    if patched:
        logger.info("[expert-team-seed] 已为存量专家团补齐凭证清单: %s", patched)
    return seeded
