"""test_macc —— Phase 6 V1 MACC 三层压缩测试。

覆盖：
- models_macc 数据类 + CompressionStrategy Literal
- storage 3 张新表 CRUD + BFS recall + compression_log
- event_graph 启发式抽取 + BFS 召回
- semantic 蒸馏 + 召回
- compression CompressionRouter 决策矩阵 + 格式化
- api 4 端点（extract-events / distill-rules / recall-episode / compress）
"""

from __future__ import annotations

import pytest
from agent.sessions.compression import CompressionRouter as CompRouter
from agent.sessions.event_graph import (
    heuristic_extract_from_messages,
    recall_episode,
    serialize_graph,
)
from agent.sessions.models_macc import (
    DEFAULT_ANCHORS,
    CompressionContext,
    SemanticRule,
)
from agent.sessions.semantic import (
    distill_rules_from_events,
    recall_relevant_rules,
)
from agent.sessions.storage import SessionStorage

# ---- 数据类 ----------------------------------------------------------------


def test_data_classes_basic():
    rule = SemanticRule.new(
        session_id="s1",
        pattern="tool:foo",
        rule_text="高频调用",
        confidence=0.5,
    )
    assert rule.id
    d = rule.to_dict()
    assert d["pattern"] == "tool:foo"
    assert d["confidence"] == 0.5


def test_default_anchors():
    """架构师约定的 4 个关键状态锚点必须存在。"""
    names = [a.node_name for a in DEFAULT_ANCHORS]
    assert "hitl_gate" in names
    assert "tool_runner" in names
    assert "repair" in names
    assert "intent" in names


def test_compression_context_extra():
    ctx = CompressionContext(
        session_id="s1",
        token_count=10_000,
        message_count=25,
        task_complexity="complex",
        extra={"foo": "bar"},
    )
    assert ctx.session_id == "s1"
    assert ctx.extra["foo"] == "bar"


# ---- Storage 扩展（3 张新表）------------------------------------------------


@pytest.fixture
def storage(tmp_path):
    db = tmp_path / "sessions.db"
    s = SessionStorage(str(db))
    # 默认插入测试用 session（让外键 FK 通过）—— 用 SQLite 直插避免依赖 create_session 的随机 id
    from agent.sessions.storage import now_ms as _now_ms

    with s._connect() as conn:
        conn.execute(
            """
            INSERT INTO sessions(id, title, owner, project_name, status,
                                 created_at, updated_at, thread_id, metadata_json)
            VALUES ('s1', 'test', 'default', 'default', 'active', ?, ?, 't1', '{}')
            """,
            (_now_ms(), _now_ms()),
        )
    yield s


def test_storage_creates_macc_tables(storage):
    """schema 自动建 MACC 三表 + compression_log。"""
    # semantic_rules
    rid = storage.upsert_semantic_rule(
        pattern="tool:test",
        rule_text="test rule",
        session_id="s1",
        confidence=0.4,
    )
    assert rid
    rules = storage.list_semantic_rules(min_confidence=0.0)
    assert len(rules) == 1
    assert rules[0]["pattern"] == "tool:test"

    # event_graph_nodes
    nid = storage.insert_event_node(
        session_id="s1",
        entity="orders_db.orders",
        action="SELECT count(*)",
        status="ok",
    )
    assert nid
    nodes = storage.list_event_nodes("s1")
    assert len(nodes) == 1

    # event_graph_edges
    nid2 = storage.insert_event_node(
        session_id="s1",
        entity="hitl_gate",
        action="approve",
    )
    eid = storage.insert_event_edge(
        session_id="s1",
        from_node=nid,
        to_node=nid2,
        relation="triggers",
    )
    assert eid > 0
    edges = storage.list_event_edges("s1")
    assert len(edges) == 1

    # compression_log
    lid = storage.log_compression(
        session_id="s1",
        strategy="MEMORY",
        before_tokens=10000,
        after_tokens=3000,
        layers_used=["L3.WM", "L3.EM", "L3.SM"],
        elapsed_ms=150,
    )
    assert lid > 0
    logs = storage.list_compression_log("s1")
    assert len(logs) == 1
    assert logs[0]["strategy"] == "MEMORY"
    assert logs[0]["compression_ratio"] == pytest.approx(0.3, abs=1e-3)


def test_semantic_rule_dedup(storage):
    """同 pattern + rule_text → confidence 累加（不重复插）。"""
    storage.upsert_semantic_rule(pattern="p", rule_text="r", session_id="s1", confidence=0.2)
    storage.upsert_semantic_rule(pattern="p", rule_text="r", session_id="s1", confidence=0.2)
    rules = storage.list_semantic_rules(min_confidence=0.0)
    assert len(rules) == 1
    assert rules[0]["confidence"] == pytest.approx(0.4, abs=1e-3)


def test_bfs_recall_episode(storage):
    """BFS 沿 outgoing + incoming edges 扩展节点。"""
    n1 = storage.insert_event_node("s1", "orders", "SELECT")
    n2 = storage.insert_event_node("s1", "orders", "UPDATE")
    n3 = storage.insert_event_node("s1", "audit", "INSERT")
    storage.insert_event_edge("s1", n1, n2, relation="next")
    storage.insert_event_edge("s1", n2, n3, relation="triggers")

    # 从 "orders" 出发 → 应找到 n1（hop=0）+ n2（hop=1）+ n3（hop=2）
    nodes = storage.bfs_recall_episode("s1", seed_entities=["orders"], max_hops=2)
    ids = [n["id"] for n in nodes]
    assert n1 in ids
    assert n2 in ids
    assert n3 in ids
    # hops 排序
    hops = [n["hops"] for n in nodes]
    assert hops == sorted(hops)


def test_bfs_recall_with_incoming(storage):
    """从末端节点反向 BFS（incoming edges）。"""
    n1 = storage.insert_event_node("s1", "tool.x", "invoke")
    n2 = storage.insert_event_node("s1", "tool.y", "invoke")
    storage.insert_event_edge("s1", n1, n2, relation="triggers")

    # 从 n2 出发 → 应找到 n2 + n1（沿 incoming）
    nodes = storage.bfs_recall_episode("s1", seed_entities=["tool.y"], max_hops=2)
    ids = [n["id"] for n in nodes]
    assert n2 in ids
    assert n1 in ids


def test_bfs_recall_empty_seed(storage):
    """seed 为空 → 返空。"""
    assert storage.bfs_recall_episode("s1", seed_entities=[]) == []


def test_delete_semantic_rule(storage):
    rid = storage.upsert_semantic_rule(
        pattern="p",
        rule_text="r",
        session_id="s1",
        confidence=0.5,
    )
    assert storage.delete_semantic_rule(rid) is True
    assert storage.list_semantic_rules(min_confidence=0.0) == []
    # 二次删 → False
    assert storage.delete_semantic_rule(rid) is False


# ---- event_graph -----------------------------------------------------------


def test_heuristic_extract_sql():
    """从含 SQL 的消息中抽取事件节点。"""
    msgs = [
        {"role": "user", "content": "查 SELECT count(*) FROM orders WHERE status='paid'"},
        {"role": "user", "content": "我需要 UPDATE orders SET status='shipped'"},
    ]
    nodes = heuristic_extract_from_messages("s1", msgs, storage=None)  # type: ignore[arg-type]
    # 至少 2 个节点（SELECT + UPDATE）
    assert len(nodes) >= 2
    entities = [n.entity for n in nodes]
    assert any("orders" in e for e in entities)


def test_heuristic_extract_tool_call():
    """tool_call / tool_result 模式抽取。"""
    msgs = [
        {"role": "assistant", "content": "tool_call: mcp_database SELECT * FROM users"},
        {"role": "tool", "content": "id, name, email\n1, Alice, a@example.com"},
    ]
    nodes = heuristic_extract_from_messages("s1", msgs, storage=None)  # type: ignore[arg-type]
    # 应有 tool.mcp_database 节点，且 result 被 tool_result 填充
    assert any(n.entity.startswith("tool.") for n in nodes)
    tool_node = next(n for n in nodes if n.entity.startswith("tool."))
    assert tool_node.result  # tool_result 已写回
    assert tool_node.status == "ok"


def test_recall_episode_with_query(storage):
    """recall_episode 自动从 query 抽 seed entity + BFS。"""
    storage.insert_event_node("s1", "orders", "SELECT count(*)")
    storage.insert_event_node("s1", "users", "SELECT id")
    nodes = recall_episode(
        storage,
        "s1",
        query="查 orders 表的总数",
        max_hops=1,
        max_nodes=5,
    )
    assert any("orders" in n["entity"] for n in nodes)


def test_recall_episode_with_explicit_keywords(storage):
    storage.insert_event_node("s1", "tool.x", "invoke")
    storage.insert_event_node("s1", "tool.y", "invoke")
    nodes = recall_episode(
        storage,
        "s1",
        query="anything",
        entity_keywords=["tool.x"],
    )
    assert any(n["entity"] == "tool.x" for n in nodes)


def test_serialize_graph():
    nodes = [
        {
            "id": "n1",
            "entity": "e",
            "action": "a",
            "result": "r",
            "status": "ok",
            "hops": 0,
            "metadata": {},
            "created_at": 1,
        }
    ]
    edges = [{"from_node": "n1", "to_node": "n2", "relation": "next"}]
    out = serialize_graph(nodes, edges)
    assert out["node_count"] == 1
    assert out["edge_count"] == 1
    assert out["edges"][0]["from"] == "n1"


# ---- semantic --------------------------------------------------------------


def test_distill_rules_from_events_basic(storage):
    """高频 (entity, action) → 蒸馏为规则。"""
    # 模拟 5 次同 (tool.x, invoke)
    for _ in range(5):
        storage.insert_event_node("s1", "tool.x", "invoke")
    rules = distill_rules_from_events(
        "s1",
        storage=storage,
        min_occurrences=3,
        max_rules=5,
    )
    assert len(rules) >= 1
    rule = rules[0]
    assert "tool" in rule.pattern.lower() or "x" in rule.pattern
    assert rule.confidence > 0


def test_distill_rules_skip_low_frequency(storage):
    """< min_occurrences → 不蒸馏。"""
    storage.insert_event_node("s1", "tool.rare", "invoke")
    rules = distill_rules_from_events(
        "s1",
        storage=storage,
        min_occurrences=3,
    )
    assert rules == []


def test_distill_rules_sql_template(storage):
    """SQL 模式 → 用 sql:* pattern。"""
    for _ in range(4):
        storage.insert_event_node("s1", "orders_db.orders", "SELECT * FROM orders")
    rules = distill_rules_from_events(
        "s1",
        storage=storage,
        min_occurrences=3,
    )
    assert rules
    assert any(r.pattern.startswith("sql:") for r in rules)


def test_recall_relevant_rules(storage):
    """关键词命中 + confidence 加权排序。"""
    storage.upsert_semantic_rule(
        pattern="订单平账",
        rule_text="先确认 payment status 再改 order",
        session_id="s1",
        confidence=0.8,
    )
    storage.upsert_semantic_rule(
        pattern="redis 部署", rule_text="用 Redis Cluster 模式", session_id="s1", confidence=0.6
    )
    rules = recall_relevant_rules(storage, "订单平账流程", top_k=2)
    assert len(rules) >= 1
    # "订单平账" 应排第一
    assert "订单平账" in rules[0]["pattern"]


def test_recall_relevant_rules_min_confidence(storage):
    storage.upsert_semantic_rule(pattern="a", rule_text="x", session_id="s1", confidence=0.1)
    rules = recall_relevant_rules(storage, "anything", min_confidence=0.5)
    assert rules == []


# ---- CompressionRouter -----------------------------------------------------


def test_router_decide_none_short_conversation():
    router = CompRouter(storage=None)  # type: ignore[arg-type]
    strategy = router._decide_strategy(
        CompressionContext(
            session_id="s1",
            token_count=1000,
            message_count=5,
        )
    )
    assert strategy == "NONE"


def test_router_decide_working_only_medium():
    router = CompRouter(storage=None)  # type: ignore[arg-type]
    strategy = router._decide_strategy(
        CompressionContext(
            session_id="s1",
            token_count=10_000,
            message_count=30,
        )
    )
    assert strategy == "WORKING_ONLY"


def test_router_decide_memory_long():
    router = CompRouter(storage=None)  # type: ignore[arg-type]
    strategy = router._decide_strategy(
        CompressionContext(
            session_id="s1",
            token_count=50_000,
            message_count=150,
        )
    )
    assert strategy == "MEMORY"


def test_router_decide_hybrid_super_long():
    router = CompRouter(storage=None)  # type: ignore[arg-type]
    strategy = router._decide_strategy(
        CompressionContext(
            session_id="s1",
            token_count=200_000,
            message_count=600,
        )
    )
    assert strategy == "HYBRID"


def test_router_decide_gist_multimodal():
    router = CompRouter(storage=None)  # type: ignore[arg-type]
    strategy = router._decide_strategy(
        CompressionContext(
            session_id="s1",
            token_count=70_000,
            message_count=50,
            has_multimodal=True,
        )
    )
    assert strategy == "GIST"


def test_router_decide_memory_idle():
    router = CompRouter(storage=None)  # type: ignore[arg-type]
    strategy = router._decide_strategy(
        CompressionContext(
            session_id="s1",
            token_count=10_000,
            message_count=30,
            idle_time_s=400,
        )
    )
    assert strategy == "MEMORY"


def test_router_route_writes_compression_log(storage):
    """route() 自动写 compression_log。"""
    router = CompRouter(storage=storage)
    msgs = [
        {"role": "user", "content": "查订单"},
        {"role": "assistant", "content": "好的，正在处理"},
    ]
    result = router.route(
        CompressionContext(
            session_id="s1",
            token_count=10_000,
            message_count=30,
        ),
        messages=msgs,
    )
    assert result.strategy == "WORKING_ONLY"
    assert result.compression_ratio <= 1.0
    # log 已写
    logs = storage.list_compression_log("s1")
    assert len(logs) == 1
    assert logs[0]["strategy"] == "WORKING_ONLY"


def test_router_route_formats_working_memory(storage):
    """WORKING_ONLY 输出包含锚点 + 滑动窗口。"""
    router = CompRouter(storage=storage, window_size=5)
    msgs = [{"role": "user", "content": f"msg-{i}"} for i in range(10)]
    result = router.route(
        CompressionContext(
            session_id="s1",
            token_count=10_000,
            message_count=30,
        ),
        messages=msgs,
    )
    assert "[ANCHOR]" in result.formatted_prompt
    assert "msg-" in result.formatted_prompt
    # 滑动窗口只保留最后 5 条
    assert result.formatted_prompt.count("msg-") == 5


def test_router_route_memory_layers(storage):
    """MEMORY 策略走 L3 三层。"""
    router = CompRouter(storage=storage)
    # 先存一些事件 + 规则
    for _ in range(5):
        storage.insert_event_node("s1", "tool.x", "invoke")
    storage.upsert_semantic_rule(
        pattern="tool:x", rule_text="高频调用工具", session_id="s1", confidence=0.5
    )

    result = router.route(
        CompressionContext(
            session_id="s1",
            token_count=50_000,
            message_count=150,
        ),
        messages=[{"role": "user", "content": "x"}],
    )
    assert result.strategy == "MEMORY"
    assert "L3.WM" in result.layers_used
    assert "L3.EM" in result.layers_used
    assert "L3.SM" in result.layers_used
    # events 加载
    assert len(result.event_graph_nodes) >= 1
    # rules 加载
    assert any(r.pattern == "tool:x" for r in result.semantic_rules)


# ---- integration: CompressionRouter with default settings --------------------


def test_router_default_window_size():
    router = CompRouter(storage=None)  # type: ignore[arg-type]
    assert router.window_size == 20


def test_router_default_anchors_from_ctx():
    """V1 默认返回 DEFAULT_ANCHORS 全 4 个。"""
    router = CompRouter(storage=None)  # type: ignore[arg-type]
    ctx = CompressionContext(session_id="s1")
    anchors = router._default_anchors_from_ctx(ctx)
    assert len(anchors) == 4
