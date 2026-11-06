"""dict.storage —— 数据字典 SQLite 存储（Phase 2H）。"""

from __future__ import annotations

import sqlite3
import time
from pathlib import Path
from typing import Any

from .models import DictItem

# 类内定义了 `list` 方法，类作用域里的 `list[...]` 注解会解析到该方法；
# 用模块级别名引用内置 list，兼顾 mypy 与 ruff UP006。
_List = list

_SCHEMA_PATH = Path(__file__).with_name("schema.sql")


def now() -> int:
    return int(time.time())


def _read_schema_sql() -> str:
    return _SCHEMA_PATH.read_text(encoding="utf-8")


# 首次建库时的种子公共参数（银行运营高频公共参数示例，可被 Skill 通过 key 引用）
SEED_ITEMS: list[dict[str, Any]] = [
    {
        "key": "authorization_valid_days",
        "category": "授权",
        "label": "授权有效期（天）",
        "value": "7",
        "description": "业务授权/授权书默认有效天数；超过需重新授权。",
    },
    {
        "key": "large_cash_threshold",
        "category": "现金",
        "label": "大额现金支取起点（元）",
        "value": "50000",
        "description": "单笔大额现金支取审核起点，超过需主管授权。",
    },
    {
        "key": "bo_identification_threshold",
        "category": "对公",
        "label": "受益所有人识别持股阈值",
        "value": "25%",
        "description": "直接或间接持股/表决权达到该比例需识别为受益所有人。",
    },
    {
        "key": "account_opening_docs",
        "category": "对公",
        "label": "对公开户必要资料",
        "value": "营业执照、法人身份证、经办人身份证、授权书、章程、受益所有人信息",
        "description": "对公开户资料清单（公共部分），具体按业务类型在功能点 Skill 中细化。",
    },
    {
        "key": "id_card_expiry_remind_days",
        "category": "客户",
        "label": "证件到期提醒天数",
        "value": "30",
        "description": "证件有效期剩余天数低于该值即触发到期提醒。",
    },
    {
        "key": "aml_suspicious_report_route",
        "category": "反洗钱",
        "label": "可疑交易上报路径",
        "value": "合规部 → 反洗钱监测系统",
        "description": "AI 仅辅助分析，最终上报由人工审核后走该路径。",
    },
    {
        "key": "complaint_response_days",
        "category": "客服",
        "label": "投诉答复时限（工作日）",
        "value": "5",
        "description": "投诉处理完成的时限（工作日），超期需升级。",
    },
    # ---- 业务下拉选项（数据字典化，前端实时读取）----------------------------
    # 知识库分类（KbPanel.tsx 上传/筛选下拉）
    {
        "key": "finance",
        "category": "知识库分类",
        "label": "财务/财税",
        "value": "finance",
        "description": "涉及财务报销、预算、会计、税务、审计等内容的文档",
    },
    {
        "key": "legal",
        "category": "知识库分类",
        "label": "法务/合同",
        "value": "legal",
        "description": "涉及合同、法律条款、诉讼、知识产权等内容的文档",
    },
    {
        "key": "compliance",
        "category": "知识库分类",
        "label": "合规/制度",
        "value": "compliance",
        "description": "涉及内控制度、合规规范、操作流程、监管政策等内容的文档",
    },
    {
        "key": "data_security",
        "category": "知识库分类",
        "label": "数据安全",
        "value": "data_security",
        "description": "涉及数据分类分级、隐私保护、信息安全、备份策略等内容的文档",
    },
    {
        "key": "hr",
        "category": "知识库分类",
        "label": "人力/组织",
        "value": "hr",
        "description": "涉及劳动人事、薪酬绩效、考勤招聘、员工培训与福利等人力组织类文档",
    },
    {
        "key": "operations",
        "category": "知识库分类",
        "label": "运营/业务",
        "value": "operations",
        "description": "涉及业务流程、操作手册、网点柜面、渠道受理与客户服务等运营类文档",
    },
    {
        "key": "risk",
        "category": "知识库分类",
        "label": "风险/信贷",
        "value": "risk",
        "description": "涉及风险评估、授信审查、贷后管理、反洗钱、案件预警等风险与信贷类文档",
    },
    {
        "key": "marketing",
        "category": "知识库分类",
        "label": "营销/品牌",
        "value": "marketing",
        "description": "涉及营销推广、品牌宣传、客户活动、产品客群等市场营销类文档",
    },
    {
        "key": "engineering",
        "category": "知识库分类",
        "label": "技术/研发",
        "value": "engineering",
        "description": "涉及技术架构、研发规范、接口对接、运维部署与算法等技术类文档",
    },
    # 资产类型（AssetConfigDialog.tsx 资产类型下拉）
    {
        "key": "database",
        "category": "资产类型",
        "label": "数据库",
        "value": "database",
        "description": '{"icon": "🗄"}',
    },
    {
        "key": "rest",
        "category": "资产类型",
        "label": "REST API",
        "value": "rest",
        "description": '{"icon": "🌐"}',
    },
    {
        "key": "ssh",
        "category": "资产类型",
        "label": "SSH",
        "value": "ssh",
        "description": '{"icon": "🔐"}',
    },
    {
        "key": "rpa",
        "category": "资产类型",
        "label": "RPA",
        "value": "rpa",
        "description": '{"icon": "🤖"}',
    },
    # 数据库类型（AssetConfigDialog.tsx 数据库类型下拉，description 存 JSON 扩展元数据）
    {
        "key": "mysql",
        "category": "数据库类型",
        "label": "MySQL",
        "value": "mysql",
        "description": '{"defaultPort": 3306, "dbCategory": "mainstream", "placeholderDb": "my_database", "placeholderUser": "readonly"}',
    },
    {
        "key": "postgresql",
        "category": "数据库类型",
        "label": "PostgreSQL",
        "value": "postgresql",
        "description": '{"defaultPort": 5432, "dbCategory": "mainstream", "placeholderDb": "postgres", "placeholderUser": "postgres"}',
    },
    {
        "key": "oracle",
        "category": "数据库类型",
        "label": "Oracle",
        "value": "oracle",
        "description": '{"defaultPort": 1521, "dbCategory": "mainstream", "placeholderDb": "ORCL", "placeholderUser": "system"}',
    },
    {
        "key": "sqlserver",
        "category": "数据库类型",
        "label": "SQL Server",
        "value": "sqlserver",
        "description": '{"defaultPort": 1433, "dbCategory": "mainstream", "placeholderDb": "master", "placeholderUser": "sa"}',
    },
    {
        "key": "clickhouse",
        "category": "数据库类型",
        "label": "ClickHouse",
        "value": "clickhouse",
        "description": '{"defaultPort": 8123, "dbCategory": "mainstream", "placeholderDb": "default", "placeholderUser": "default"}',
    },
    {
        "key": "sqlite",
        "category": "数据库类型",
        "label": "SQLite",
        "value": "sqlite",
        "description": '{"defaultPort": 0, "dbCategory": "file", "placeholderDb": "", "placeholderUser": ""}',
    },
    {
        "key": "dm",
        "category": "数据库类型",
        "label": "达梦 (DM)",
        "value": "dm",
        "description": '{"defaultPort": 5236, "dbCategory": "xinchuang", "placeholderDb": "DMDB", "placeholderUser": "SYSDBA"}',
    },
    {
        "key": "kingbase",
        "category": "数据库类型",
        "label": "人大金仓 (KingbaseES)",
        "value": "kingbase",
        "description": '{"defaultPort": 54321, "dbCategory": "xinchuang", "placeholderDb": "test", "placeholderUser": "system"}',
    },
    {
        "key": "gbase",
        "category": "数据库类型",
        "label": "南大通用 (GBase)",
        "value": "gbase",
        "description": '{"defaultPort": 5258, "dbCategory": "xinchuang", "placeholderDb": "gbase_db", "placeholderUser": "gbase"}',
    },
    {
        "key": "oceanbase",
        "category": "数据库类型",
        "label": "OceanBase",
        "value": "oceanbase",
        "description": '{"defaultPort": 2881, "dbCategory": "xinchuang", "placeholderDb": "oceanbase", "placeholderUser": "root"}',
    },
    {
        "key": "tidb",
        "category": "数据库类型",
        "label": "TiDB",
        "value": "tidb",
        "description": '{"defaultPort": 4000, "dbCategory": "xinchuang", "placeholderDb": "test", "placeholderUser": "root"}',
    },
    {
        "key": "gaussdb",
        "category": "数据库类型",
        "label": "华为 GaussDB",
        "value": "gaussdb",
        "description": '{"defaultPort": 5432, "dbCategory": "xinchuang", "placeholderDb": "postgres", "placeholderUser": "gaussdb"}',
    },
    {
        "key": "opengauss",
        "category": "数据库类型",
        "label": "openGauss",
        "value": "opengauss",
        "description": '{"defaultPort": 5432, "dbCategory": "xinchuang", "placeholderDb": "postgres", "placeholderUser": "gaussdb"}',
    },
    {
        "key": "highgo",
        "category": "数据库类型",
        "label": "瀚高 (HighGo)",
        "value": "highgo",
        "description": '{"defaultPort": 5866, "dbCategory": "xinchuang", "placeholderDb": "highgo", "placeholderUser": "sysdba"}',
    },
    {
        "key": "csv",
        "category": "数据库类型",
        "label": "CSV 文件",
        "value": "csv",
        "description": '{"defaultPort": 0, "dbCategory": "file"}',
    },
    {
        "key": "excel",
        "category": "数据库类型",
        "label": "Excel 文件",
        "value": "excel",
        "description": '{"defaultPort": 0, "dbCategory": "file"}',
    },
    # 环境类型（EnvironmentsSettingPanel.tsx 环境下拉）
    {
        "key": "env_dev",
        "category": "环境类型",
        "label": "开发 (dev)",
        "value": "dev",
        "description": "开发环境",
    },
    {
        "key": "env_test",
        "category": "环境类型",
        "label": "测试 (test)",
        "value": "test",
        "description": "测试环境",
    },
    {
        "key": "env_staging",
        "category": "环境类型",
        "label": "准生产 (staging)",
        "value": "staging",
        "description": "准生产/预发布环境",
    },
    {
        "key": "env_prod",
        "category": "环境类型",
        "label": "生产 (prod)",
        "value": "prod",
        "description": "生产环境",
    },
    # IM 渠道（ShareDialog.tsx IM 渠道选择，description 存 JSON 含 icon/color）
    {
        "key": "wecom",
        "category": "IM渠道",
        "label": "企微",
        "value": "wecom",
        "description": '{"icon": "💬", "color": "#07c160"}',
    },
    {
        "key": "dingtalk",
        "category": "IM渠道",
        "label": "钉钉",
        "value": "dingtalk",
        "description": '{"icon": "📌", "color": "#0089ff"}',
    },
    {
        "key": "feishu",
        "category": "IM渠道",
        "label": "飞书",
        "value": "feishu",
        "description": '{"icon": "🚀", "color": "#3370ff"}',
    },
    # 模型部署类型（ModelManagementPanel.tsx 模型类型/驻留下拉）
    {
        "key": "model_local",
        "category": "模型部署类型",
        "label": "端侧",
        "value": "local",
        "description": "本地端侧部署（Ollama / 端侧模型）",
    },
    {
        "key": "model_private",
        "category": "模型部署类型",
        "label": "私有部署",
        "value": "private",
        "description": "内网私有部署大模型",
    },
    {
        "key": "model_cloud",
        "category": "模型部署类型",
        "label": "云端",
        "value": "cloud",
        "description": "云端 API 服务（OpenAI / 其他云端模型）",
    },
    # ---- 实体类型（knowledge entity extraction，入库期 LLM 按类型抽取关键实体）----
    {
        "key": "person",
        "category": "实体类型",
        "label": "人名",
        "value": "person",
        "description": "自然人姓名（如法定代表人、经办人、审批人等）",
    },
    {
        "key": "organization",
        "category": "实体类型",
        "label": "机构/组织",
        "value": "organization",
        "description": "企业、政府部门、银行分支、监管机构等组织名称",
    },
    {
        "key": "regulation",
        "category": "实体类型",
        "label": "法规/制度",
        "value": "regulation",
        "description": "法律法规、监管文件、内部制度、合规规范名称",
    },
    {
        "key": "amount",
        "category": "实体类型",
        "label": "金额/数值",
        "value": "amount",
        "description": "关键金额、利率、比率、阈值等数值信息",
    },
    {
        "key": "date",
        "category": "实体类型",
        "label": "日期/期限",
        "value": "date",
        "description": "重要日期、截止日期、有效期、合同期限等",
    },
    {
        "key": "tech_term",
        "category": "实体类型",
        "label": "技术术语",
        "value": "tech_term",
        "description": "业务专有名词、产品名称、系统名称、技术概念",
    },
]


# 随产品定义持续补齐的内置分组（已有库也补缺失项；其余分组仍仅空库播种，删除不复活）
_ALWAYS_SEED_CATEGORIES = {"知识库分类"}


class DictStorage:
    """数据字典存储（单文件 sqlite）。"""

    def __init__(self, db_path: str):
        self._db_path = db_path
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as conn:
            conn.executescript(_read_schema_sql())
        self._seed_if_empty()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self._db_path, timeout=5)
        conn.row_factory = sqlite3.Row
        return conn

    def _seed_if_empty(self) -> None:
        """播种内置条目。

        - 空库：全量播种；
        - 非空库：仅补齐「知识库分类」等随产品定义的内置分组（INSERT OR IGNORE，
          不覆盖用户已改过的条目，也不复活其它分组里被删掉的种子）。
        """
        ts = now()
        with self._connect() as conn:
            row = conn.execute("SELECT COUNT(*) AS n FROM dict_items").fetchone()
            empty = int(row["n"]) == 0
            for item in SEED_ITEMS:
                if not empty and item["category"] not in _ALWAYS_SEED_CATEGORIES:
                    continue
                conn.execute(
                    """
                    INSERT OR IGNORE INTO dict_items(
                        key, category, label, value, description, source,
                        updated_by, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, 'seed', '', ?, ?)
                    """,
                    (
                        item["key"],
                        item["category"],
                        item["label"],
                        item["value"],
                        item["description"],
                        ts,
                        ts,
                    ),
                )

    def upsert(self, item: DictItem, *, replace_seed: bool = False) -> DictItem:
        """新建/更新。seed 条目默认只读（除非 replace_seed=True，UI 显式编辑时用）。"""
        existing = self.get(item.key)
        ts = now()
        source = item.source
        if existing is not None:
            if existing.source == "seed" and not replace_seed:
                raise ValueError(
                    f"dictionary key {item.key} is a seed entry; edit it explicitly to override"
                )
            source = "manual"
        item.source = source
        item.updated_at = ts
        if existing is None:
            item.created_at = ts
            with self._connect() as conn:
                conn.execute(
                    """
                    INSERT INTO dict_items(
                        key, category, label, value, description, source,
                        updated_by, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        item.key,
                        item.category,
                        item.label,
                        item.value,
                        item.description,
                        item.source,
                        item.updated_by,
                        item.created_at,
                        item.updated_at,
                    ),
                )
        else:
            item.created_at = existing.created_at
            with self._connect() as conn:
                conn.execute(
                    """
                    UPDATE dict_items SET
                        category = ?, label = ?, value = ?, description = ?,
                        source = ?, updated_by = ?, updated_at = ?
                    WHERE key = ?
                    """,
                    (
                        item.category,
                        item.label,
                        item.value,
                        item.description,
                        item.source,
                        item.updated_by,
                        item.updated_at,
                        item.key,
                    ),
                )
        return item

    def get(self, key: str) -> DictItem | None:
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM dict_items WHERE key = ?", (key,)).fetchone()
        return DictItem.from_dict(dict(row)) if row else None

    def list(self, category: str | None = None) -> list[DictItem]:
        sql = "SELECT * FROM dict_items WHERE 1=1"
        params: list[Any] = []
        if category:
            sql += " AND category = ?"
            params.append(category)
        sql += " ORDER BY category, key"
        with self._connect() as conn:
            rows = conn.execute(sql, params).fetchall()
        return [DictItem.from_dict(dict(r)) for r in rows]

    def search(self, query: str, limit: int = 50) -> _List[DictItem]:
        """LIKE 模糊搜索：key / label / value / description / category。"""
        q = f"%{query}%"
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT * FROM dict_items
                WHERE key LIKE ? OR label LIKE ? OR value LIKE ?
                      OR description LIKE ? OR category LIKE ?
                ORDER BY category, key LIMIT ?
                """,
                (q, q, q, q, q, max(1, min(int(limit), 200))),
            ).fetchall()
        return [DictItem.from_dict(dict(r)) for r in rows]

    def categories(self) -> _List[str]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT DISTINCT category FROM dict_items ORDER BY category"
            ).fetchall()
        return [str(r["category"]) for r in rows]

    def delete(self, key: str) -> bool:
        with self._connect() as conn:
            cur = conn.execute("DELETE FROM dict_items WHERE key = ?", (key,))
        return cur.rowcount > 0
