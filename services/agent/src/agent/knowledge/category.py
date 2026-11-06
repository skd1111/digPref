"""knowledge.category —— 从数据字典读取知识库分类类型（实时生效）。

分类类型存储在 dict_items（dict.db）中，category='知识库分类' 分组。
每次调用实时查库，无缓存；dict.db 不可用 / 无分类条目 → 返回空列表。

用途：
- ingestion._auto_classify 构造 LLM 分类 prompt
- 前端 KbPanel 动态渲染上传/筛选下拉
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)

# 启发式兜底分类（模型不可用 / 限流 429 / 判定失败时使用）：
# 顺序即优先级（先命中先算）；「…法（年份）」结尾单独按法律类处理。
_HEURISTIC_RULES: tuple[tuple[str, tuple[str, ...]], ...] = (
    (
        "data_security",
        (
            "数据安全",
            "个人信息",
            "隐私保护",
            "网络安全",
            "数据出境",
            "分类分级",
            "密码",
            "加密",
        ),
    ),
    (
        "finance",
        (
            "税",
            "会计",
            "财务",
            "报销",
            "预算",
            "发票",
            "审计",
            "核算",
            "成本",
            "资金",
            "薪酬",
        ),
    ),
    ("legal", ("法律", "法典", "民法", "刑法", "合同", "诉讼", "仲裁", "知识产权", "侵权")),
    (
        "compliance",
        (
            "合规",
            "内控",
            "规范",
            "制度",
            "条例",
            "办法",
            "规定",
            "监管",
            "监督",
            "管理",
            "指引",
            "准则",
        ),
    ),
    (
        "hr",
        (
            "人事",
            "劳资",
            "劳动",
            "薪酬",
            "绩效",
            "考勤",
            "招聘",
            "员工",
            "入职",
            "离职",
            "福利",
            "岗位",
        ),
    ),
    (
        "operations",
        (
            "运营",
            "业务",
            "流程",
            "操作手册",
            "渠道",
            "网点",
            "柜面",
            "受理",
            "客户服务",
            "客户",
        ),
    ),
    (
        "risk",
        (
            "风险",
            "授信",
            "贷后",
            "担保",
            "反洗钱",
            "案件",
            "预警",
            "信贷",
        ),
    ),
    (
        "marketing",
        ("营销", "品牌", "推广", "促销", "客群", "广告", "宣传"),
    ),
    (
        "engineering",
        ("技术", "研发", "架构", "接口", "开发", "运维", "部署", "代码", "算法"),
    ),
)

# 文件名以「法」结尾（如「中华人民共和国公司法」）→ 法律类；
# 但含「税」等财务关键词的已在上一步归类。
_LAW_SUFFIX_CHARS = "法"


def guess_category(file_name: str, text: str = "") -> str:
    """按文件名（+ 正文片段）启发式猜分类，返回分类 key；无匹配返回空串。

    仅作兜底：模型不可用、限流 429 或判定失败时使用，保证分类不空。
    """
    name = (file_name or "").strip()
    body = (text or "")[:500]
    for key, keywords in _HEURISTIC_RULES:
        for kw in keywords:
            if kw in name or kw in body:
                return key
    # 结尾是「法」→ 法律类（先去掉年份后缀与扩展名）
    core = name.split("（")[0].split("(")[0].strip()
    stem = core.rsplit(".", 1)[0] if "." in core else core
    if stem.endswith(_LAW_SUFFIX_CHARS):
        return "legal"
    return ""


def get_kb_categories() -> list[dict[str, Any]]:
    """从数据字典读取知识库分类类型（实时生效）。

    返回 ``[{"key": "finance", "label": "财务/财税", "description": "..."}]``。
    dict.db 不可用或无分类条目 → 返回空列表（best-effort，不抛异常）。
    """
    try:
        from agent.datadict.storage import DictStorage
        from agent.paths import data_root

        db_path = str(data_root() / "dict.db")
        storage = DictStorage(db_path)
        items = storage.list(category="知识库分类")
        return [
            {
                "key": item.key,
                "label": item.label,
                "value": item.value,
                "description": item.description,
            }
            for item in items
        ]
    except Exception as exc:
        logger.debug("get_kb_categories failed: %s", exc)
        return []
