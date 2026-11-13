"""ExpertMember / ExpertTeam 数据类。

专家团是系统一等资产（不寄生于 Skill）：团 + 成员两级结构化。
与 agent/skills/models.py 同风格（dataclass，V0 不进 shared-protocol）。
"""

from __future__ import annotations

from dataclasses import dataclass, field

_ALLOWED_CREDENTIAL_MEDIA = frozenset({"image", "doc", "any"})


def normalize_credentials(raw: object) -> list[dict]:
    """凭证节点归一化（装载 / 保存 / 种子回填共用）。

    只接受 dict 条目；name 去空白、media 限白名单（非法归 any）、
    required 转 bool、keywords/fields 压成字符串/dict 列表；无 name 的条目丢弃。
    上限 20 个节点、每节点 12 个要素（防单个成员清单写成无边界长表）。
    """
    out: list[dict] = []
    if not isinstance(raw, list):
        return out
    for item in raw:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name", "") or "").strip()
        if not name:
            continue
        media = str(item.get("media", "any") or "any").strip().lower()
        if media not in _ALLOWED_CREDENTIAL_MEDIA:
            media = "any"
        fields: list[dict] = []
        raw_fields = item.get("fields")
        if isinstance(raw_fields, list):
            for f in raw_fields:
                if isinstance(f, dict):
                    fname = str(f.get("name", "") or "").strip()
                    flabel = str(f.get("label", "") or "").strip() or fname
                    if fname:
                        fields.append({"name": fname, "label": flabel})
                elif isinstance(f, str) and f.strip():
                    fields.append({"name": f.strip(), "label": f.strip()})
        out.append(
            {
                "name": name[:64],
                "required": bool(item.get("required", False)),
                "media": media,
                "hint": str(item.get("hint", "") or "").strip()[:200],
                "keywords": [
                    str(k).strip()[:32] for k in (item.get("keywords") or []) if str(k).strip()
                ][:12],
                "fields": fields[:12],
            }
        )
        if len(out) >= 20:
            break
    return out


@dataclass
class ExpertMember:
    """专家团内的单个专家角色。"""

    name: str  # 专家名称（必填）
    role: str  # 角色定位（必填）
    responsibilities: list[str] = field(default_factory=list)  # 主要职责
    focus_points: list[str] = field(default_factory=list)  # 关注点
    outputs: list[str] = field(default_factory=list)  # 典型输出
    # 交付物 → 表单模板（零 LLM 直开表单，2026-08-14）：
    # {交付物名: [{name, label, type, options, hint, required}]}，字段语义与
    # ops 草稿 template_json 一致（_ALLOWED_FIELD_TYPES）；未定义的交付物仍走
    # 问专家 → LLM 生成草稿链路。
    output_forms: dict[str, list[dict]] = field(default_factory=dict)
    # 凭证节点（2026-11-09 凭证预览）：该专家办理时要求上传的影像/单据清单。
    # [{name, required, media(image|doc|any), hint, keywords[], fields[{name,label}]}]；
    # keywords 供材料自动归类兜底匹配，fields 声明该凭证期望采集的要素
    # （前端点要素名 → 在影像上定位并高亮对应区域）。
    credentials: list[dict] = field(default_factory=list)
    prompt: str = ""  # 独立 prompt

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "role": self.role,
            "responsibilities": list(self.responsibilities),
            "focus_points": list(self.focus_points),
            "outputs": list(self.outputs),
            "output_forms": {k: list(v) for k, v in self.output_forms.items()},
            "credentials": [dict(c) for c in self.credentials],
            "prompt": self.prompt,
        }

    @classmethod
    def from_dict(cls, data: dict) -> ExpertMember:
        raw_forms = data.get("output_forms", {})
        output_forms: dict[str, list[dict]] = {}
        if isinstance(raw_forms, dict):
            for key, fields in raw_forms.items():
                if isinstance(fields, list):
                    output_forms[str(key)] = [f for f in fields if isinstance(f, dict)]
        return cls(
            name=data["name"],
            role=data["role"],
            responsibilities=list(data.get("responsibilities", [])),
            focus_points=list(data.get("focus_points", [])),
            outputs=list(data.get("outputs", [])),
            output_forms=output_forms,
            credentials=normalize_credentials(data.get("credentials", [])),
            prompt=data.get("prompt", ""),
        )


@dataclass
class ExpertTeam:
    """专家团（系统重要资产）。"""

    id: str  # slug（必填）
    name: str  # 中文名（必填）
    schema_version: str = "1.0"
    description: str = ""
    applicable_scenarios: list[str] = field(default_factory=list)  # 适用场景
    trigger_keywords: list[str] = field(default_factory=list)
    enabled: bool = True
    members: list[ExpertMember] = field(default_factory=list)
    report_template: str = ""  # 交付物报告模板文件名（templates/ 目录下，可选）

    # 运行时元数据（不持久化）
    source_path: str = ""
    loaded_at: int = 0

    def to_dict(self) -> dict:
        return {
            "schema_version": self.schema_version,
            "id": self.id,
            "name": self.name,
            "description": self.description,
            "applicable_scenarios": list(self.applicable_scenarios),
            "trigger_keywords": list(self.trigger_keywords),
            "enabled": self.enabled,
            "members": [m.to_dict() for m in self.members],
            "report_template": self.report_template,
        }

    @classmethod
    def from_dict(cls, data: dict) -> ExpertTeam:
        return cls(
            schema_version=data.get("schema_version", "1.0"),
            id=data["id"],
            name=data["name"],
            description=data.get("description", ""),
            applicable_scenarios=list(data.get("applicable_scenarios", [])),
            trigger_keywords=list(data.get("trigger_keywords", [])),
            enabled=bool(data.get("enabled", True)),
            members=[
                ExpertMember.from_dict(m)
                for m in data.get("members", [])
                if isinstance(m, dict) and "name" in m and "role" in m
            ],
            report_template=str(data.get("report_template", "") or ""),
        )
