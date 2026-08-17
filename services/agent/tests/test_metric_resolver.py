"""Phase 7 v2.87 · MetricResolver 抽象层 —— 9 用例覆盖。

覆盖范围：

* ``MetricDef`` / ``ResolvedQuery`` Pydantic 校验（frozen / Literal / 范围）
* ``DictMetricResolver.resolve()`` 命中 / 未命中 / 关键词加权
* ``DictMetricResolver.list_metrics()`` 遍历 YAML 字典
* ``build_resolver()`` 三种 kind 分发（dict / platform / bridge）
* 配置错误兜底（unknown type 抛 :class:`MetricResolverConfigError`）
* 与 ``nl2sql/dictionary.py`` 既有调用点兼容（不破坏既有 ``test_data_nl2sql``）
"""

from __future__ import annotations

import pytest
from agent.dataexpert.metric_resolver import (
    BridgeMetricResolver,
    DictMetricResolver,
    MetricDef,
    MetricResolver,
    MetricResolverConfigError,
    PlatformMetricResolver,
    ResolvedQuery,
    build_resolver,
    get_default_resolver,
)
from agent.llm.router import _LOCAL_ONLY_TASKS
from pydantic import ValidationError

# =============================================================================
# 1) MetricDef Pydantic 校验（含 agg Literal + frozen）
# =============================================================================


def test_metric_def_basic_construction():
    """MetricDef 可正常构造（默认 agg=SUM / 空 dimensions）。"""
    m = MetricDef(
        code="loan_amt",
        name="放款总额",
        source_table="dws.loan_fact",
        source_column="fwd_amt",
    )
    assert m.code == "loan_amt"
    assert m.name == "放款总额"
    assert m.agg == "SUM"
    assert m.dimensions == []
    assert m.dimension_mappings == {}
    assert m.owner is None


def test_metric_def_is_frozen():
    """MetricDef frozen=True：构造后字段不可修改。"""
    m = MetricDef(code="x", name="y", source_table="", source_column="")
    with pytest.raises(ValidationError):
        m.code = "z"  # type: ignore[misc]


def test_metric_def_agg_literal_validates():
    """MetricDef.agg 仅接受 Literal 中的 7 种值。"""
    MetricDef(
        code="x",
        name="y",
        source_table="",
        source_column="",
        agg="COUNT_DISTINCT",
    )
    with pytest.raises(ValidationError):
        MetricDef(
            code="x",
            name="y",
            source_table="",
            source_column="",
            agg="INVALID_AGG",  # type: ignore[arg-type]
        )


# =============================================================================
# 2) ResolvedQuery Pydantic 校验（含 source_kind Literal + confidence 范围）
# =============================================================================


def _make_metric() -> MetricDef:
    return MetricDef(
        code="loan_amt",
        name="放款总额",
        source_table="dws.loan_fact",
        source_column="fwd_amt",
    )


def test_resolved_query_basic_construction():
    """ResolvedQuery 可正常构造（默认 confidence=0.0）。"""
    rq = ResolvedQuery(metric=_make_metric(), source_kind="dict", confidence=0.8)
    assert rq.source_kind == "dict"
    assert rq.confidence == 0.8
    assert rq.platform_sql is None
    assert rq.candidates == []


def test_resolved_query_confidence_range():
    """ResolvedQuery.confidence 必须 ∈ [0.0, 1.0]。"""
    metric = _make_metric()
    with pytest.raises(ValidationError):
        ResolvedQuery(metric=metric, source_kind="dict", confidence=1.5)
    with pytest.raises(ValidationError):
        ResolvedQuery(metric=metric, source_kind="dict", confidence=-0.1)


def test_resolved_query_source_kind_literal_validates():
    """ResolvedQuery.source_kind 仅接受 dict / platform / bridge。"""
    metric = _make_metric()
    with pytest.raises(ValidationError):
        ResolvedQuery(metric=metric, source_kind="other", confidence=0.5)  # type: ignore[arg-type]


def test_resolved_query_is_frozen():
    """ResolvedQuery frozen=True：构造后不可修改。"""
    rq = ResolvedQuery(metric=_make_metric(), source_kind="dict", confidence=0.5)
    with pytest.raises(ValidationError):
        rq.confidence = 0.9  # type: ignore[misc]


# =============================================================================
# 3) DictMetricResolver.resolve() 命中 / 未命中
# =============================================================================


@pytest.mark.asyncio
async def test_dict_resolver_hit_returns_resolved_query():
    """业务字典命中 → 返回 ResolvedQuery（source_kind='dict'）。"""
    resolver = DictMetricResolver()
    # "成功" 是 _DEFAULT_DICTIONARY._global 中的条目
    rq = await resolver.resolve("查询所有成功的订单", context={"source_id": ""})
    assert rq is not None
    assert isinstance(rq, ResolvedQuery)
    assert rq.source_kind == "dict"
    assert rq.platform_sql is None
    assert 0.0 <= rq.confidence <= 1.0


@pytest.mark.asyncio
async def test_dict_resolver_miss_returns_none():
    """业务字典未命中 → 返回 None（前端可回退到纯 NL2SQL）。"""
    resolver = DictMetricResolver()
    # 没有业务术语
    rq = await resolver.resolve("今天是星期几")
    assert rq is None


@pytest.mark.asyncio
async def test_dict_resolver_detects_agg_keyword():
    """DictMetricResolver 启发式识别聚合方式（avg/max/min/count 关键词）。

    query 必须同时含业务字典条目（让 resolve 返回非 None）和 agg 关键词（让 _detect_agg 返回非 SUM）。
    """
    resolver = DictMetricResolver()
    # "成功" 在 _global 字典里；"平均" → agg=AVG
    rq = await resolver.resolve("查询成功订单的平均金额", context={"source_id": ""})
    assert rq is not None
    assert rq.metric.agg == "AVG"


@pytest.mark.asyncio
async def test_dict_resolver_with_source_id():
    """context 含 source_id='ds_credit' → 用特定数据源字典（'正常类' 是 ds_credit 条目）。"""
    resolver = DictMetricResolver()
    rq = await resolver.resolve("统计正常类贷款数量", context={"source_id": "ds_credit"})
    assert rq is not None
    assert rq.source_kind == "dict"
    # COUNT 关键词命中（"数量"）→ agg=COUNT
    assert rq.metric.agg == "COUNT"


@pytest.mark.asyncio
async def test_dict_resolver_list_metrics_returns_all_terms():
    """list_metrics() 遍历全局字典所有条目。"""
    resolver = DictMetricResolver()
    metrics = await resolver.list_metrics()
    assert len(metrics) >= 5
    # 每个都是 MetricDef
    for m in metrics:
        assert isinstance(m, MetricDef)
    # 至少包含 "成功" 这条
    codes = {m.code for m in metrics}
    assert "成功" in codes


# =============================================================================
# 4) build_resolver() 工厂函数：三种 kind 分发
# =============================================================================


def test_build_resolver_dict_default():
    """build_resolver 默认返回 DictMetricResolver。"""
    r = build_resolver()
    assert isinstance(r, DictMetricResolver)


def test_build_resolver_dict_explicit():
    """build_resolver(resolver_type='dict') → DictMetricResolver。"""
    r = build_resolver(resolver_type="dict")
    assert isinstance(r, DictMetricResolver)


def test_build_resolver_platform_placeholder(monkeypatch):
    """build_resolver(resolver_type='platform') → PlatformMetricResolver（V0 占位）。

    V0 实际调用 resolve() 抛 NotImplementedError；这是设计预期，不是 bug。
    """
    monkeypatch.setenv("EAIDE_METRICS_BASE_URL", "https://metrics.example.com/api/v1")
    r = build_resolver(
        resolver_type="platform",
        platform_auth_secret="test-secret",
    )
    assert isinstance(r, PlatformMetricResolver)
    assert r._base_url == "https://metrics.example.com/api/v1"
    assert r._auth == "test-secret"
    # resolve() V0 占位
    import asyncio

    with pytest.raises(NotImplementedError):
        asyncio.run(r.resolve("test"))


def test_build_resolver_platform_requires_base_url(monkeypatch):
    """platform 模式必须提供 base_url。"""
    monkeypatch.delenv("EAIDE_METRICS_BASE_URL", raising=False)
    with pytest.raises(MetricResolverConfigError):
        build_resolver(resolver_type="platform")


def test_build_resolver_bridge_placeholder():
    """build_resolver(resolver_type='bridge') → BridgeMetricResolver（V1.5 占位）。"""
    r = build_resolver(resolver_type="bridge", bridge_dws_schema="dws_mkt")
    assert isinstance(r, BridgeMetricResolver)
    assert r._dws_schema == "dws_mkt"


def test_build_resolver_unknown_type_raises():
    """未知的 resolver type 抛 MetricResolverConfigError。"""
    with pytest.raises(MetricResolverConfigError, match=r"unknown metric_resolver\.type"):
        build_resolver(resolver_type="unknown")


def test_build_resolver_env_var_override(monkeypatch):
    """EAIDE_METRIC_RESOLVER 环境变量可覆盖默认 dict。"""
    monkeypatch.setenv("EAIDE_METRIC_RESOLVER", "bridge")
    r = build_resolver()
    assert isinstance(r, BridgeMetricResolver)


# =============================================================================
# 5) Protocol 运行时检查（runtime_checkable）
# =============================================================================


def test_dict_resolver_satisfies_protocol():
    """DictMetricResolver 是 MetricResolver 的运行时实例。"""
    assert isinstance(DictMetricResolver(), MetricResolver)


# =============================================================================
# 6) 与 nl2sql/dictionary.py 既有调用点兼容（不破坏既有测试）
# =============================================================================


def test_metric_resolver_does_not_break_existing_dictionary():
    """导入 metric_resolver 不会破坏既有 dictionary.translate() 调用。"""
    from agent.dataexpert.nl2sql import dictionary

    ctx = dictionary.translate("查询成功的订单", source_id="")
    assert "成功" in ctx  # 既有行为未变


def test_get_default_resolver_returns_dict_by_default(monkeypatch):
    """get_default_resolver() 默认返回 DictMetricResolver。"""
    monkeypatch.delenv("EAIDE_METRIC_RESOLVER", raising=False)
    r = get_default_resolver()
    assert isinstance(r, DictMetricResolver)


# =============================================================================
# 7) ★ 红线：metric_resolve 在 _LOCAL_ONLY_TASKS 中（CLAUDE.md §2）
# =============================================================================


def test_metric_resolve_is_local_only_task():
    """★ 红线：v2.87 新增 metric_resolve 必须存在于 _LOCAL_ONLY_TASKS。

    若此测试失败，说明有人试图将指标识别路由到云端 —— 绝对禁止。
    业务字典翻译 / 平台 API 响应都可能含敏感表结构 / 字段注释。
    """
    assert "metric_resolve" in _LOCAL_ONLY_TASKS, (
        "★ 安全红线违规：'metric_resolve' 不在 _LOCAL_ONLY_TASKS 中！"
        "指标识别含业务字典翻译 / 平台响应可能敏感，永不出云。"
    )


# =============================================================================
# SourceKind Literal 烟雾测试（设计预期）
# =============================================================================


def test_source_kind_literal_values():
    """SourceKind Literal 严格匹配三个值（防止误增）。"""
    # Literal["dict", "platform", "bridge"] —— 通过 ResolvedQuery 验证
    m = _make_metric()
    for kind in ("dict", "platform", "bridge"):
        rq = ResolvedQuery(metric=m, source_kind=kind, confidence=0.5)  # type: ignore[arg-type]
        assert rq.source_kind == kind
    # 不在 Literal 中的值报错
    with pytest.raises(ValidationError):
        ResolvedQuery(metric=m, source_kind="elasticsearch", confidence=0.5)  # type: ignore[arg-type]
