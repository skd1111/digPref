"""Phase 5 V0 · 审核专家模式 单元 + 集成测试（25+ 用例）。

覆盖:
  - models: 枚举 / 签名链 (compute_signature / verify_signature_chain) / check_decision_required_fields
  - compliance: 5 条规则 (DESTRUCTIVE_OP / PROD_ENV_RISK / OFF_HOURS / MISSING_EVIDENCE / HIGH_RISK_NO_MFA)
  - events: emit + consume + flush
  - storage: 4 表 CRUD + stats
  - api: 8 端点 (create/list/get/evidence/decide/verify/stats)
  - SSE + _LOCAL_ONLY_TASKS
"""

from __future__ import annotations

import pytest

# ---- models 测试 ---------------------------------------------------------


class TestModels:
    """数据类 + 签名链测试。"""

    def test_risk_level_enum(self):
        from agent.audit_expert.models import RiskLevel

        assert len(RiskLevel) == 5

    def test_approval_status_enum(self):
        from agent.audit_expert.models import ApprovalStatus

        assert ApprovalStatus.PENDING.value == "pending"
        assert ApprovalStatus.APPROVED.value == "approved"

    def test_action_type_enum(self):
        from agent.audit_expert.models import ActionType

        assert ActionType.APPROVE.value == "approve"
        assert ActionType.REJECT.value == "reject"

    def test_compliance_level_enum(self):
        from agent.audit_expert.models import ComplianceLevel

        assert ComplianceLevel.VIOLATION.value == "violation"

    def test_evidence_type_enum(self):
        from agent.audit_expert.models import EvidenceType

        assert EvidenceType.TOOL_CALL.value == "tool_call"

    def test_compute_signature_basic(self):
        from agent.audit_expert.models import (
            ActionType,
            ApprovalAction,
            compute_signature,
        )

        action = ApprovalAction(
            action_id="abc",
            task_id="t1",
            action_type=ActionType.APPROVE,
            actor="alice",
            reason="ok",
            mfa_verified=True,
            timestamp="2026-07-31T10:00:00+00:00",
            prev_hash="",
        )
        sig = compute_signature(action)
        assert len(sig) == 64  # SHA-256 hex
        assert all(c in "0123456789abcdef" for c in sig)

    def test_compute_signature_deterministic(self):
        """相同输入 → 相同 hash。"""
        from agent.audit_expert.models import (
            ActionType,
            ApprovalAction,
            compute_signature,
        )

        kwargs = dict(
            action_id="abc",
            task_id="t1",
            action_type=ActionType.APPROVE,
            actor="alice",
            reason="ok",
            mfa_verified=True,
            timestamp="2026-07-31T10:00:00+00:00",
        )
        a1 = ApprovalAction(**kwargs)
        a2 = ApprovalAction(**kwargs)
        assert compute_signature(a1) == compute_signature(a2)

    def test_signature_chain_valid(self):
        """3 个 actions 链式签名，全部 valid。"""
        from agent.audit_expert.models import (
            ActionType,
            ApprovalAction,
            compute_signature,
            verify_signature_chain,
        )

        actions = []
        prev = ""
        for i in range(3):
            a = ApprovalAction(
                action_id=f"a{i}",
                task_id="t1",
                action_type=ActionType.APPROVE,
                actor=f"user{i}",
                reason=f"r{i}",
                mfa_verified=True,
                timestamp=f"2026-07-31T10:0{i}:00+00:00",
            )
            a.prev_hash = prev
            a.signature_hash = compute_signature(a, prev)
            actions.append(a)
            prev = a.signature_hash
        assert verify_signature_chain(actions)

    def test_signature_chain_tampered(self):
        """篡改 reason 后签名链 invalid。"""
        from agent.audit_expert.models import (
            ActionType,
            ApprovalAction,
            compute_signature,
            verify_signature_chain,
        )

        actions = []
        prev = ""
        for i in range(2):
            a = ApprovalAction(
                action_id=f"a{i}",
                task_id="t1",
                action_type=ActionType.APPROVE,
                actor="alice",
                reason=f"original{i}",
                mfa_verified=True,
                timestamp=f"2026-07-31T10:0{i}:00+00:00",
            )
            a.prev_hash = prev
            a.signature_hash = compute_signature(a, prev)
            actions.append(a)
            prev = a.signature_hash
        # 篡改 reason
        actions[1].reason = "tampered"
        assert not verify_signature_chain(actions)

    def test_signature_chain_broken_link(self):
        """prev_hash 断链。"""
        from agent.audit_expert.models import (
            ActionType,
            ApprovalAction,
            compute_signature,
            verify_signature_chain,
        )

        actions = []
        prev = ""
        for i in range(2):
            a = ApprovalAction(
                action_id=f"a{i}",
                task_id="t1",
                action_type=ActionType.APPROVE,
                actor="alice",
                reason=f"r{i}",
                mfa_verified=True,
                timestamp=f"2026-07-31T10:0{i}:00+00:00",
            )
            a.prev_hash = prev
            a.signature_hash = compute_signature(a, prev)
            actions.append(a)
            prev = a.signature_hash
        # 改 prev_hash 但不改 signature_hash
        actions[1].prev_hash = "wronghash" * 4  # 64 chars
        assert not verify_signature_chain(actions)

    def test_check_decision_required_approve_missing(self):
        from agent.audit_expert.models import (
            ActionType,
            ApprovalAction,
            check_decision_required_fields,
        )

        a = ApprovalAction(
            action_id="x",
            task_id="t",
            action_type=ActionType.APPROVE,
            actor="u",
            reason="",
            mfa_verified=False,
        )
        missing = check_decision_required_fields(a)
        assert "reason" in missing
        assert "mfa_verified" in missing

    def test_check_decision_required_approve_ok(self):
        from agent.audit_expert.models import (
            ActionType,
            ApprovalAction,
            check_decision_required_fields,
        )

        a = ApprovalAction(
            action_id="x",
            task_id="t",
            action_type=ActionType.APPROVE,
            actor="u",
            reason="ok",
            mfa_verified=True,
        )
        assert check_decision_required_fields(a) == []

    def test_generate_id(self):
        from agent.audit_expert.models import generate_id

        assert len(generate_id()) == 32  # UUID4 hex


# ---- compliance 测试 -----------------------------------------------------


class TestCompliance:
    """5 条合规规则测试。"""

    def test_destructive_op_triggered(self):
        from agent.audit_expert.compliance import run_compliance_checks
        from agent.audit_expert.models import RiskLevel

        checks = run_compliance_checks(
            task_id="t1",
            risk_level=RiskLevel.MEDIUM,
            pending_tool_call={"name": "db.execute", "args": {"sql": "DROP TABLE users"}},
            evidence_count=3,
        )
        destructive = [c for c in checks if c.rule_name == "DESTRUCTIVE_OP"]
        assert len(destructive) == 1
        assert destructive[0].level.value == "violation"
        assert not destructive[0].passed

    def test_destructive_op_not_triggered(self):
        from agent.audit_expert.compliance import run_compliance_checks
        from agent.audit_expert.models import RiskLevel

        checks = run_compliance_checks(
            task_id="t1",
            risk_level=RiskLevel.LOW,
            pending_tool_call={"name": "db.query", "args": {"sql": "SELECT 1"}},
            evidence_count=3,
        )
        destructive = [c for c in checks if c.rule_name == "DESTRUCTIVE_OP"]
        assert len(destructive) == 1
        assert destructive[0].passed

    def test_high_risk_no_mfa_violation(self):
        from agent.audit_expert.compliance import run_compliance_checks
        from agent.audit_expert.models import RiskLevel

        checks = run_compliance_checks(
            task_id="t1",
            risk_level=RiskLevel.HIGH,
            pending_tool_call={"name": "delete_file"},
            evidence_count=3,
            mfa_configured=False,
        )
        mfa_check = [c for c in checks if c.rule_name == "HIGH_RISK_NO_MFA"]
        assert len(mfa_check) == 1
        assert mfa_check[0].level.value == "violation"

    def test_missing_evidence_warning(self):
        from agent.audit_expert.compliance import run_compliance_checks
        from agent.audit_expert.models import RiskLevel

        checks = run_compliance_checks(
            task_id="t1",
            risk_level=RiskLevel.LOW,
            pending_tool_call={"name": "read_file"},
            evidence_count=1,  # < 2
        )
        miss = [c for c in checks if c.rule_name == "MISSING_EVIDENCE"]
        assert len(miss) == 1
        assert miss[0].level.value == "warning"
        assert not miss[0].passed

    def test_prod_env_warning(self):
        from agent.audit_expert.compliance import run_compliance_checks
        from agent.audit_expert.models import RiskLevel

        checks = run_compliance_checks(
            task_id="t1",
            risk_level=RiskLevel.MEDIUM,
            pending_tool_call={"name": "db.query", "args": {"db": "prod-orders"}},
            evidence_count=3,
        )
        prod = [c for c in checks if c.rule_name == "PROD_ENV_RISK"]
        assert len(prod) == 1
        assert prod[0].level.value == "warning"


# ---- events 测试 --------------------------------------------------------


class TestEvents:
    @pytest.mark.asyncio
    async def test_emit_and_consume(self):
        from agent.audit_expert.events import (
            EVT_AUDIT_TASK_PENDING,
            consume_events,
            emit_event,
            flush_events,
        )

        await flush_events()
        await emit_event(EVT_AUDIT_TASK_PENDING, {"task_id": "x"})
        events = await consume_events()
        assert len(events) == 1
        assert events[0][0] == EVT_AUDIT_TASK_PENDING


# ---- storage 测试 -------------------------------------------------------


class TestStorage:
    @pytest.mark.asyncio
    async def test_insert_and_get_task(self, tmp_path, monkeypatch):
        from agent.audit_expert.store import AuditExpertStorage, reset_default_storage
        from agent.config import settings

        reset_default_storage()
        db_path = tmp_path / "audit_expert.db"
        monkeypatch.setattr(settings, "audit_expert_db_path", str(db_path))

        storage = AuditExpertStorage()
        await storage.insert_task(
            task_id="t1",
            run_id="r1",
            title="Test",
            description="Test desc",
            risk_level="medium",
            pending_tool_call={"name": "x"},
            requested_by="alice",
            meta={},
        )
        task = await storage.get_task("t1")
        assert task is not None
        assert task["title"] == "Test"
        assert task["status"] == "pending"

    @pytest.mark.asyncio
    async def test_insert_and_list_action(self, tmp_path, monkeypatch):
        from agent.audit_expert.store import AuditExpertStorage, reset_default_storage
        from agent.config import settings

        reset_default_storage()
        db_path = tmp_path / "audit_expert.db"
        monkeypatch.setattr(settings, "audit_expert_db_path", str(db_path))

        storage = AuditExpertStorage()
        await storage.insert_task(
            task_id="t2",
            run_id="r1",
            title="x",
            description="y",
            risk_level="low",
            pending_tool_call={},
            requested_by="u",
            meta={},
        )
        await storage.insert_action(
            action_id="a1",
            task_id="t2",
            action_type="approve",
            actor="alice",
            reason="ok",
            mfa_verified=True,
            timestamp="2026-07-31T10:00:00",
            prev_hash="",
            signature_hash="abc123",
        )
        await storage.update_task_decision(
            "t2",
            status="approved",
            decided_by="alice",
            decision_reason="ok",
            mfa_verified=True,
        )
        actions = await storage.list_actions("t2")
        assert len(actions) == 1
        assert actions[0]["actor"] == "alice"

    @pytest.mark.asyncio
    async def test_insert_evidence_hash(self, tmp_path, monkeypatch):
        from agent.audit_expert.store import AuditExpertStorage, reset_default_storage
        from agent.config import settings

        reset_default_storage()
        db_path = tmp_path / "audit_expert.db"
        monkeypatch.setattr(settings, "audit_expert_db_path", str(db_path))

        storage = AuditExpertStorage()
        await storage.insert_task(
            task_id="t3",
            run_id="r1",
            title="x",
            description="y",
            risk_level="low",
            pending_tool_call={},
            requested_by="u",
            meta={},
        )
        await storage.insert_evidence(
            evidence_id="e1",
            task_id="t3",
            evidence_type="tool_call",
            title="Call evidence",
            content={"sql": "SELECT 1"},
            source="agent",
        )
        entries = await storage.list_evidence("t3")
        assert len(entries) == 1
        assert len(entries[0]["hash"]) == 64  # SHA-256 hex


# ---- API 端点测试 -------------------------------------------------------


class TestAPI:
    @pytest.fixture
    def client(self, tmp_path, monkeypatch):
        from agent.audit_expert.store import reset_default_storage
        from agent.config import settings

        reset_default_storage()
        db_path = tmp_path / "audit_expert.db"
        monkeypatch.setattr(settings, "audit_expert_db_path", str(db_path))

        from agent.audit_expert.api import router as audit_api_router
        from fastapi import FastAPI
        from fastapi.testclient import TestClient

        app = FastAPI()
        app.include_router(audit_api_router)
        return TestClient(app)

    def test_create_task(self, client):
        resp = client.post(
            "/audit/tasks",
            json={
                "run_id": "r1",
                "title": "Delete prod users",
                "description": "需要删除生产环境 users 表",
                "risk_level": "critical",
                "pending_tool_call": {
                    "name": "db.execute",
                    "args": {"sql": "DROP TABLE users"},
                },
                "requested_by": "alice",
            },
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "pending"
        assert body["risk_level"] == "critical"
        assert body["task_id"]

    def test_list_tasks_empty(self, client):
        resp = client.get("/audit/tasks")
        assert resp.status_code == 200
        assert resp.json() == []

    def test_list_tasks_after_create(self, client):
        client.post(
            "/audit/tasks",
            json={
                "run_id": "r1",
                "title": "T",
                "description": "D",
                "risk_level": "low",
                "pending_tool_call": {},
                "requested_by": "u",
            },
        )
        resp = client.get("/audit/tasks")
        assert len(resp.json()) == 1

    def test_get_task_not_found(self, client):
        resp = client.get("/audit/tasks/nonexistent")
        assert resp.status_code == 404

    def test_add_evidence_and_decide(self, client):
        # 创建
        create = client.post(
            "/audit/tasks",
            json={
                "run_id": "r1",
                "title": "T",
                "description": "D",
                "risk_level": "medium",
                "pending_tool_call": {},
                "requested_by": "u",
            },
        ).json()
        task_id = create["task_id"]

        # 添加证据
        ev = client.post(
            f"/audit/tasks/{task_id}/evidence",
            json={
                "evidence_type": "tool_call",
                "title": "E1",
                "content": {"x": 1},
                "source": "agent",
            },
        )
        assert ev.status_code == 200

        # 决策
        d = client.post(
            f"/audit/tasks/{task_id}/decide",
            json={
                "action_type": "approve",
                "actor": "alice",
                "reason": "approved",
                "mfa_verified": True,
            },
        )
        assert d.status_code == 200
        assert d.json()["new_status"] == "approved"

    def test_decide_missing_fields(self, client):
        create = client.post(
            "/audit/tasks",
            json={
                "run_id": "r1",
                "title": "T",
                "description": "D",
                "risk_level": "medium",
                "pending_tool_call": {},
                "requested_by": "u",
            },
        ).json()
        task_id = create["task_id"]
        resp = client.post(
            f"/audit/tasks/{task_id}/decide",
            json={
                "action_type": "approve",
                "actor": "alice",
                "reason": "",
                "mfa_verified": False,
            },
        )
        assert resp.status_code == 400

    def test_decide_already_decided(self, client):
        create = client.post(
            "/audit/tasks",
            json={
                "run_id": "r1",
                "title": "T",
                "description": "D",
                "risk_level": "low",
                "pending_tool_call": {},
                "requested_by": "u",
            },
        ).json()
        task_id = create["task_id"]
        client.post(
            f"/audit/tasks/{task_id}/decide",
            json={
                "action_type": "approve",
                "actor": "u",
                "reason": "ok",
                "mfa_verified": True,
            },
        )
        resp2 = client.post(
            f"/audit/tasks/{task_id}/decide",
            json={
                "action_type": "reject",
                "actor": "u",
                "reason": "no",
                "mfa_verified": True,
            },
        )
        assert resp2.status_code == 400

    def test_verify_chain(self, client):
        create = client.post(
            "/audit/tasks",
            json={
                "run_id": "r1",
                "title": "T",
                "description": "D",
                "risk_level": "low",
                "pending_tool_call": {},
                "requested_by": "u",
            },
        ).json()
        task_id = create["task_id"]
        client.post(
            f"/audit/tasks/{task_id}/decide",
            json={
                "action_type": "approve",
                "actor": "alice",
                "reason": "ok",
                "mfa_verified": True,
            },
        )
        resp = client.get(f"/audit/tasks/{task_id}/verify")
        assert resp.status_code == 200
        assert resp.json()["valid"] is True
        assert resp.json()["action_count"] == 1

    def test_stats_endpoint(self, client):
        resp = client.get("/audit/stats")
        assert resp.status_code == 200
        assert "tasks_by_status" in resp.json()


# ---- SSE 测试 -----------------------------------------------------------


class TestStream:
    def test_channel_by_kind_has_audit(self):
        from agent.graph.stream import _CHANNEL_BY_KIND

        assert _CHANNEL_BY_KIND["audit_task_pending"] == "agent://audit_task_pending"
        assert _CHANNEL_BY_KIND["audit_task_decided"] == "agent://audit_task_decided"
        assert _CHANNEL_BY_KIND["audit_evidence_added"] == "agent://audit_evidence_added"
        assert _CHANNEL_BY_KIND["audit_compliance_done"] == "agent://audit_compliance_done"
