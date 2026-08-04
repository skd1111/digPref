"""Phase 12 V0 — Orchestrator E2E 测试（mock LLM + FastAPI TestClient）。

跳过：TestClient 在 Windows 上 TestClient 创建新 loop 与 Orchestrator.__init__ 创建的
asyncio.Queue 跨 loop 冲突 —— 本测试直接调 Orchestrator API，绕过 TestClient。
"""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

from agent.orchestrator.orchestrator import Orchestrator
from agent.orchestrator.spec import (
    SubAgentSpec,
    SubAgentStatus,
)
from agent.orchestrator.tree_guard import TreeLimitExceeded


@pytest.mark.asyncio
async def test_full_spawn_lifecycle_ok():
    """正常派生 → OK + 摘要 + 2 个 SSE 事件（spawn + done）。"""
    mock_router = MagicMock()
    mock_router.route = AsyncMock(
        return_value='子 Agent 解释：foo 函数是 Python 的 foo，调用 bar() 来处理 baz',
    )

    orch = Orchestrator(llm_router=mock_router)
    spec = SubAgentSpec(
        sub_agent_id='sub-e2e-1',
        parent_run_id='run-1',
        depth=1,
        task_type='plan',
        task_description='解释 foo 函数',
        input_payload={'file': 'a.py', 'symbol': 'foo'},
    )
    report = await orch.spawn(spec)

    assert report.status == SubAgentStatus.OK
    assert 'foo 函数' in report.summary
    assert report.latency_ms >= 0
    assert report.confidence > 0
    # task_type='plan' 不在 _LOCAL_ONLY_TASKS → preferred_backend='' → backend_used=''
    # （前端 UI 展示时把空 backend 当作「走 router 默认」）
    assert report.backend_used == ''
    assert orch.total_nodes == 1

    # SSE 事件：V1.5 改走进程内 deque（orchestrator.events）；V0 老路径在
    # Orchestrator._events asyncio.Queue 仍同步推一份（V0 e2e 兼容）。
    # 这里 e2e 直接从老的 asyncio.Queue 验证 emit 行为。
    events = []
    while not orch.event_queue.empty():
        events.append(orch.event_queue.get_nowait())
    assert len(events) == 2
    assert events[0]['channel'] == 'agent://sub_agent_spawn'
    assert events[1]['channel'] == 'agent://sub_agent_done'
    assert events[0]['data']['sub_agent_id'] == 'sub-e2e-1'
    assert events[1]['data']['status'] == 'ok'


@pytest.mark.asyncio
async def test_sensitive_task_force_local():
    """铁律 3：carries_sensitive_payload → 强制走本地 ollama 后端。"""
    mock_router = MagicMock()
    mock_router.route = AsyncMock(return_value='OK')

    orch = Orchestrator(llm_router=mock_router)
    spec = SubAgentSpec(
        sub_agent_id='sub-sensitive',
        parent_run_id='run-1',
        depth=1,
        task_type='custom',  # 不在 _LOCAL_ONLY_TASKS 也不在敏感路径
        task_description='总结 DB 数据',
        input_payload={'rows': ['row1', 'row2']},
    )
    # 通过 model_policy 标记敏感
    spec.model_policy.carries_sensitive_payload = True
    report = await orch.spawn(spec)

    assert report.status == SubAgentStatus.OK
    assert report.backend_used == 'ollama'


@pytest.mark.asyncio
async def test_data_summary_task_force_local():
    """铁律 3：task_type='data_summary' 已被并入 _LOCAL_ONLY_TASKS → 强制本地。"""
    mock_router = MagicMock()
    mock_router.route = AsyncMock(return_value='数据摘要结果')

    orch = Orchestrator(llm_router=mock_router)
    spec = SubAgentSpec(
        sub_agent_id='sub-data-sum',
        parent_run_id='run-1',
        depth=1,
        task_type='data_summary',  # 新增到 _LOCAL_ONLY_TASKS
        task_description='汇总数据库行',
    )
    report = await orch.spawn(spec)
    assert report.status == SubAgentStatus.OK
    assert report.backend_used == 'ollama'


@pytest.mark.asyncio
async def test_tree_limit_depth_exceeded():
    """depth > 2 → TreeLimitExceeded 抛出。"""
    mock_router = MagicMock()
    mock_router.route = AsyncMock(return_value='OK')

    orch = Orchestrator(llm_router=mock_router)
    spec = SubAgentSpec(
        sub_agent_id='sub-deep',
        parent_run_id='run-1',
        depth=3,  # > MAX_DEPTH=2
        task_type='plan',
        task_description='太深了',
    )
    with pytest.raises(TreeLimitExceeded) as exc:
        await orch.spawn(spec)
    assert exc.value.reason.startswith('depth')


@pytest.mark.asyncio
async def test_tree_limit_total_exceeded():
    """total > 30 → TreeLimitExceeded。"""
    mock_router = MagicMock()
    mock_router.route = AsyncMock(return_value='OK')

    orch = Orchestrator(llm_router=mock_router)
    # 强制 total_nodes = 30（逼近上限）
    orch._total_nodes = 30
    spec = SubAgentSpec(
        sub_agent_id='sub-31',
        parent_run_id='run-1',
        depth=1,
        task_type='plan',
        task_description='第 31 个',
    )
    with pytest.raises(TreeLimitExceeded) as exc:
        await orch.spawn(spec)
    assert exc.value.reason.startswith('total_nodes')


@pytest.mark.asyncio
async def test_llm_failure_returns_err_report():
    """LLM 抛异常 → report.status=err + error_message 非空（不进 DLQ 因为不是 OK 校验失败）。"""
    mock_router = MagicMock()
    mock_router.route = AsyncMock(side_effect=RuntimeError('LLM 服务挂了'))

    orch = Orchestrator(llm_router=mock_router)
    spec = SubAgentSpec(
        sub_agent_id='sub-fail',
        parent_run_id='run-1',
        depth=1,
        task_type='plan',
        task_description='必然失败',
    )
    report = await orch.spawn(spec)
    assert report.status == SubAgentStatus.ERR
    assert 'LLM 服务挂了' in report.error_message


@pytest.mark.asyncio
async def test_passthrough_context_policy_composes_prompt():
    """passthrough 策略：拼出包含 task_type / required_fields 的 prompt。"""
    orch = Orchestrator(llm_router=MagicMock(route=AsyncMock(return_value='OK')))
    spec = SubAgentSpec(
        sub_agent_id='sub-ctx',
        parent_run_id='run-1',
        depth=1,
        task_type='plan',
        task_description='plan 测试',
        input_payload={'file': 'a.py'},
    )
    spec.context_policy.required_fields = ['error_code', 'status_code']
    prompt = orch._compose_prompt(spec)
    assert '任务类型: plan' in prompt
    assert 'plan 测试' in prompt
    assert 'a.py' in prompt
    assert 'error_code' in prompt
    assert 'status_code' in prompt


@pytest.mark.asyncio
async def test_unknown_strategy_logs_warning_falls_through_to_passthrough():
    """shared_memory_pool / incremental_summary V0 还没实现 → 走 passthrough 但记 warning。"""
    orch = Orchestrator(llm_router=MagicMock(route=AsyncMock(return_value='OK')))
    spec = SubAgentSpec(
        sub_agent_id='sub-future',
        parent_run_id='run-1',
        depth=1,
        task_type='plan',
        task_description='未来策略',
    )
    spec.context_policy.strategy = 'shared_memory_pool'
    prompt = orch._compose_prompt(spec)
    assert '任务类型: plan' in prompt  # 仍然拼了


@pytest.mark.asyncio
async def test_multiple_spawns_accumulate_total_nodes():
    """连续派生 3 个，total_nodes 累加到 3。"""
    mock_router = MagicMock()
    mock_router.route = AsyncMock(return_value='OK')

    orch = Orchestrator(llm_router=mock_router)
    for i in range(3):
        await orch.spawn(
            SubAgentSpec(
                sub_agent_id=f'sub-{i}',
                parent_run_id='run-1',
                depth=1,
                task_type='plan',
                task_description=f'任务 {i}',
            )
        )
    assert orch.total_nodes == 3
    assert len(orch.list_reports()) == 3


@pytest.mark.asyncio
async def test_get_report_after_spawn():
    """派生后能用 get_report(id) 拿到完整 report。"""
    mock_router = MagicMock()
    mock_router.route = AsyncMock(return_value='子 Agent 返回内容 X')

    orch = Orchestrator(llm_router=mock_router)
    spec = SubAgentSpec(
        sub_agent_id='sub-get',
        parent_run_id='run-1',
        depth=1,
        task_type='plan',
        task_description='测试',
    )
    await orch.spawn(spec)
    r = orch.get_report('sub-get')
    assert r is not None
    assert r.sub_agent_id == 'sub-get'
    assert r.status == SubAgentStatus.OK
    # 不存在
    assert orch.get_report('nope') is None


@pytest.mark.asyncio
async def test_cancel_running_sub_agent_marks_cancelled():
    """cancel 一个 OK/ERR 状态的子 Agent → False（已结束）；cancel 一个 RUNNING → True。"""
    mock_router = MagicMock()
    mock_router.route = AsyncMock(return_value='OK')

    orch = Orchestrator(llm_router=mock_router)
    spec = SubAgentSpec(
        sub_agent_id='sub-cancel',
        parent_run_id='run-1',
        depth=1,
        task_type='plan',
        task_description='测试 cancel',
    )
    report = await orch.spawn(spec)
    # 已经 OK → cancel 返回 False
    ok = await orch.cancel('sub-cancel')
    assert ok is False
    # 手动构造一个 RUNNING 状态的 report 来测 cancel 成功路径
    from agent.orchestrator.spec import SubAgentReport
    fake_id = 'sub-running-fake'
    fake_report = SubAgentReport(
        sub_agent_id=fake_id,
        parent_run_id='run-1',
        status=SubAgentStatus.RUNNING,
        started_at='2026-07-22T00:00:00Z',
    )
    orch._reports[fake_id] = fake_report
    ok2 = await orch.cancel(fake_id)
    assert ok2 is True
    assert fake_report.status == SubAgentStatus.CANCELLED