"""Phase 7 v2.87 · MetricResolver 抽象层 —— 指标平台接入预留。

设计哲学（详见 ``docs/design/phase-7-data-expert.md`` §4.1.1）：

国内金融/政企客户普遍已建「指标管理平台」（IMS / 阿里 Quick BI 指标百科 /
火山 DataFinder / 美数 KPI / 银行自研 IMS）。同一指标在不同系统定义不同口径
是合规事故。架构上必须**抽象 MetricResolver 接口**，业务代码只看
``ResolvedQuery``，不感知底下是业务字典（V0 默认）还是指标平台（V1 接力直连
API）/ dws 视图桥接（V1.5 接力）。配置驱动一行切换，不重写 NL2SQL 业务逻辑。

V0 范围（v2.87 设计补强）：

* :class:`MetricDef` / :class:`ResolvedQuery` Pydantic v2 数据类
* :class:`MetricResolver` Protocol 抽象接口
* :class:`DictMetricResolver` V0 默认实现（包装 ``nl2sql/dictionary.py`` 业务字典）
* :class:`PlatformMetricResolver` V1 接力占位（直连 IMS / Quick BI / DataFinder HTTP API）
* :class:`BridgeMetricResolver` V1.5 接力占位（走 dws 视图间接查询）
* :func:`build_resolver` 工厂函数 + :func:`get_default_resolver` 环境变量便捷入口
* ``EAIDE_METRIC_RESOLVER`` 环境变量切换（V0 唯一生效来源；
  ``config/data_expert.yaml::metric_resolver`` 是预留配置模板，当前无代码读取，yaml 接线留待 V1）

零现有业务代码改动：``nl2sql/dictionary.py`` 继续是 V0 DictMetricResolver 的
实际数据源，``nl2sql/generator.py`` 签名保持向后兼容（``ResolvedQuery`` 作为
可选 hint 留给 V1 调用方读取）。
"""

from __future__ import annotations

import os
from typing import Literal, Protocol, runtime_checkable

from pydantic import BaseModel, ConfigDict, Field

# 来源种类（决定 SQL 怎么生成）
SourceKind = Literal["dict", "platform", "bridge"]

# 聚合方式
AggKind = Literal["SUM", "AVG", "COUNT", "MIN", "MAX", "COUNT_DISTINCT", "RAW"]


# === Pydantic 数据类 ===============================================================


class MetricDef(BaseModel):
    """指标定义 —— 单一指标的元数据。

    字段冻结（frozen=True）：一旦构造不可变，避免下游误改导致口径漂移。
    """

    model_config = ConfigDict(frozen=True)

    code: str  # "loan_amt"
    name: str  # "放款总额"
    description: str = ""  # 业务定义
    source_table: str = ""  # "dws.loan_fact"（V0 留空让 linker 猜）
    source_column: str = ""  # "fwd_amt"
    agg: AggKind = "SUM"
    dimensions: list[str] = Field(default_factory=list)
    owner: str | None = None
    dimension_mappings: dict[str, str] = Field(default_factory=dict)


class ResolvedQuery(BaseModel):
    """自然语言解析后的结构化查询意图。

    ``source_kind`` 决定下游 NL2SQL 节点走哪条分支：
        * ``dict``     —— V0 自拼 SQL（基于 ``metric.source_table`` + ``dimensions``）
        * ``platform`` —— 平台给的 SQL 模板（``platform_sql`` 字段非空）
        * ``bridge``   —— 走 dws 视图间接查询（V1.5 接力）
    """

    model_config = ConfigDict(frozen=True)

    metric: MetricDef
    dimensions_filter: dict[str, str] = Field(default_factory=dict)
    time_range: tuple[str, str] | None = None
    source_kind: SourceKind
    confidence: float = Field(ge=0.0, le=1.0)
    platform_sql: str | None = None  # platform 模式有 SQL 模板
    candidates: list[MetricDef] = Field(default_factory=list)  # top-K 候选（Platform 模式）


# === 抽象接口 =====================================================================


@runtime_checkable
class MetricResolver(Protocol):
    """指标解析器抽象接口。三种实现同接口（V0 Dict / V1 Platform / V1.5 Bridge）。"""

    async def resolve(self, query: str, context: dict | None = None) -> ResolvedQuery | None:
        """把自然语言问句解析为 ``ResolvedQuery``；识别不出返回 None。"""
        ...

    async def list_metrics(self, project: str | None = None) -> list[MetricDef]:
        """列出可用指标（前端提示用）。"""
        ...


class MetricResolverConfigError(ValueError):
    """``metric_resolver`` 配置错误（unknown type / 缺字段等）。"""


# === V0 默认实现：DictMetricResolver ==============================================


# 中文标点 + 英文标点（用于提取首句）
_QUERY_SEPARATORS = ("，", ",", "。", ".", "？", "?", "；", ";", "（", "(", "！", "!")


def _extract_first_clause(query: str) -> str:
    """V0 简化：从 query 取首个标点前的子句作为指标候选名。"""
    for sep in _QUERY_SEPARATORS:
        if sep in query:
            return query.split(sep, 1)[0].strip()
    return query.strip()


def _detect_agg(query: str) -> AggKind:
    """启发式识别聚合方式（V0 简化：关键词命中）。"""
    if any(kw in query for kw in ("平均", "均值", "AVG", "average", "Average")):
        return "AVG"
    if any(kw in query for kw in ("最大", "最高", "MAX", "max")):
        return "MAX"
    if any(kw in query for kw in ("最小", "最低", "MIN", "min")):
        return "MIN"
    if any(kw in query for kw in ("数量", "笔数", "次数", "count", "COUNT", "Count")):
        return "COUNT"
    if any(kw in query for kw in ("去重", "DISTINCT", "distinct")):
        return "COUNT_DISTINCT"
    return "SUM"


class DictMetricResolver:
    """业务字典实现 —— V0 默认。

    基于 ``agent.dataexpert.nl2sql.dictionary``（V1 已 YAML 外置：内置默认 +
    ``settings.data_biz_dict_dir`` 合并），启发式关键词命中 + 加权排序识别指标。
    """

    def __init__(self, dict_path: str | None = None) -> None:
        # V1：dict_path 真接 dictionary.load_dictionary（None → settings.data_biz_dict_dir）
        self._dict_path = dict_path

    async def resolve(
        self,
        query: str,
        context: dict | None = None,
    ) -> ResolvedQuery | None:
        """基于关键词命中识别指标。

        V0 简化策略：
          1. 从 ``context`` 取 ``source_id``（可选）
          2. 调用 ``dictionary.translate(query, source_id)`` 拿上下文
          3. 业务字典未命中 → 返回 None（前端允许回退到纯 NL2SQL）
          4. 业务字典命中 → 启发式聚合识别 + 取首句为指标名 → 包装 ResolvedQuery

        Args:
            query: 用户自然语言问句
            context: 可选上下文，含 ``source_id`` 等

        Returns:
            :class:`ResolvedQuery` 或 None
        """
        # 延迟导入避免循环依赖（dataexpert/__init__.py 同时导出本模块和 dictionary）
        from agent.dataexpert.nl2sql import dictionary

        source_id = (context or {}).get("source_id", "")
        ctx_str = dictionary.translate(query, source_id=source_id, dict_dir=self._dict_path)
        if not ctx_str:
            return None

        agg = _detect_agg(query)
        first_clause = _extract_first_clause(query)

        metric = MetricDef(
            code=first_clause or "unknown",
            name=first_clause or query[:20],
            description="",
            source_table="",  # V0 留空让 linker 自己猜；V1 Platform 模式有真值
            source_column="",
            agg=agg,
            dimensions=[],
        )

        # 置信度：业务字典命中行数 / 经验阈值 5（粗略）
        hit_count = sum(1 for line in ctx_str.split("\n") if "→" in line)
        confidence = min(1.0, hit_count / 5.0)

        return ResolvedQuery(
            metric=metric,
            dimensions_filter={},
            time_range=None,
            source_kind="dict",
            confidence=confidence,
            platform_sql=None,
            candidates=[],
        )

    async def list_metrics(self, project: str | None = None) -> list[MetricDef]:
        """遍历业务字典返回所有 MetricDef（V0 简化：用全局字典条目）。

        Args:
            project: 项目名（V0 忽略；V1 Platform 模式按 project 过滤）。
        """
        # 延迟导入；V1 走 load_dictionary（内置默认 + YAML 外置合并）
        from agent.dataexpert.nl2sql import dictionary

        merged = dictionary.load_dictionary(self._dict_path)
        out: list[MetricDef] = []
        for term, sql_frag in merged.get("_global", {}).items():
            out.append(
                MetricDef(
                    code=term,
                    name=term,
                    description=sql_frag,
                    source_table="",
                    source_column="",
                    agg="SUM",
                    dimensions=[],
                )
            )
        return out


# === V1 接力占位：PlatformMetricResolver ===========================================


class PlatformMetricResolver:
    """指标平台实现 —— V1 接力。直连 IMS / Quick BI / DataFinder HTTP API。

    本轮 V0 仅占位（构造函数 + 抛 :class:`NotImplementedError`）。
    V1 接力实现：httpx 异步调用 + Keyring 占位符鉴权 + 30s 超时 + 1 次重试
    + 异常降级 :class:`DictMetricResolver`（兜底不阻塞）。
    """

    def __init__(
        self,
        base_url: str,
        auth_secret: str = "",
        timeout: int = 30,
    ) -> None:
        if not base_url:
            raise MetricResolverConfigError(
                "PlatformMetricResolver 需要 base_url（环境变量 EAIDE_METRICS_BASE_URL；"
                "config/data_expert.yaml::metric_resolver.platform 是预留模板，V0 不读取）"
            )
        self._base_url = base_url.rstrip("/")
        self._auth = auth_secret
        self._timeout = timeout

    async def resolve(
        self,
        query: str,
        context: dict | None = None,
    ) -> ResolvedQuery | None:
        raise NotImplementedError(
            "PlatformMetricResolver V0 未实现；V1 接力。当前请把 metric_resolver.type 改回 'dict'。"
        )

    async def list_metrics(self, project: str | None = None) -> list[MetricDef]:
        raise NotImplementedError("PlatformMetricResolver V0 未实现；V1 接力。")


# === V1.5 接力占位：BridgeMetricResolver ============================================


class BridgeMetricResolver:
    """dws 视图桥接实现 —— V1.5 接力。

    本轮 V0 仅占位。V1.5 接力：``INFORMATION_SCHEMA.VIEWS`` 元数据发现 +
    VIEW_NAME 含 metric 关键字匹配 + readonly_pool 间接查询。
    """

    def __init__(self, dws_schema: str = "dws") -> None:
        self._dws_schema = dws_schema

    async def resolve(
        self,
        query: str,
        context: dict | None = None,
    ) -> ResolvedQuery | None:
        raise NotImplementedError(
            "BridgeMetricResolver V0 未实现；V1.5 接力。当前请把 metric_resolver.type 改回 'dict'。"
        )

    async def list_metrics(self, project: str | None = None) -> list[MetricDef]:
        raise NotImplementedError("BridgeMetricResolver V0 未实现；V1.5 接力。")


# === 工厂函数 =====================================================================


def build_resolver(
    *,
    resolver_type: str | None = None,
    dict_path: str | None = None,
    platform_base_url: str | None = None,
    platform_auth_secret: str | None = None,
    bridge_dws_schema: str | None = None,
) -> MetricResolver:
    """按 ``type`` 选实现。

    优先级：

    1. ``resolver_type`` 入参（测试可覆盖）
    2. ``EAIDE_METRIC_RESOLVER`` 环境变量
    3. 默认 ``"dict"``

    V0 注意：``config/data_expert.yaml`` 不参与（无加载器，预留模板）；
    yaml 接线留待 V1 接力。

    Args:
        resolver_type: ``dict`` / ``platform`` / ``bridge``
        dict_path: V0 业务字典 YAML 目录（V1 外置用；当前未使用）
        platform_base_url: 指标平台 API base URL
        platform_auth_secret: Keyring 占位符或环境变量值
        bridge_dws_schema: dws schema 名（默认 ``"dws"``）

    Returns:
        :class:`MetricResolver` 实例

    Raises:
        MetricResolverConfigError: type 未知或 platform 缺 base_url
    """
    kind = (resolver_type or os.environ.get("EAIDE_METRIC_RESOLVER") or "dict").lower()

    if kind == "dict":
        return DictMetricResolver(dict_path=dict_path)

    if kind == "platform":
        # base_url 入参优先；再尝试环境变量兜底
        base_url = platform_base_url or os.environ.get("EAIDE_METRICS_BASE_URL", "")
        auth = platform_auth_secret or os.environ.get("EAIDE_METRICS_API_KEY", "")
        return PlatformMetricResolver(
            base_url=base_url,
            auth_secret=auth,
        )

    if kind == "bridge":
        return BridgeMetricResolver(dws_schema=bridge_dws_schema or "dws")

    raise MetricResolverConfigError(
        f"unknown metric_resolver.type={kind!r}；期望 dict / platform / bridge。"
        "请检查 EAIDE_METRIC_RESOLVER 环境变量（V0 唯一生效来源；yaml 配置预留未接线）。"
    )


def get_default_resolver() -> MetricResolver:
    """便捷工厂：仅读环境变量返回默认 resolver。"""
    return build_resolver()


__all__ = [
    "AggKind",
    "BridgeMetricResolver",
    "DictMetricResolver",
    "MetricDef",
    "MetricResolver",
    "MetricResolverConfigError",
    "PlatformMetricResolver",
    "ResolvedQuery",
    "SourceKind",
    "build_resolver",
    "get_default_resolver",
]
