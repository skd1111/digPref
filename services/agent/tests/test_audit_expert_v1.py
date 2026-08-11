"""Phase 5 V1 · RSA + TOTP MFA + 双人复核 单元测试（20+ 用例）。

覆盖:
  - rsa_sign: 签名 / 验签 / 公钥导出 / PEM 格式
  - mfa: TOTP 生成 / 验证 / 共享密钥存储
  - models: 双人复核字段扩展
  - api: dual-first / dual-second 端点 + 必填校验 + TOTP 端点
  - compliance: high/critical 自动启 dual
"""

from __future__ import annotations

import time

import pytest

# ---- rsa_sign 测试 -------------------------------------------------------


class TestRsaSign:
    """RSA 签名模块测试。"""

    def test_sign_and_verify_basic(self):
        from agent.audit_expert.rsa_sign import (
            sign_payload,
            verify_payload_signature,
        )

        payload = "test|payload|123"
        sig = sign_payload(payload)
        assert sig  # 非空
        assert len(sig) > 0
        assert verify_payload_signature(payload, sig)

    def test_verify_wrong_payload(self):
        from agent.audit_expert.rsa_sign import (
            sign_payload,
            verify_payload_signature,
        )

        sig = sign_payload("correct")
        assert not verify_payload_signature("wrong", sig)

    def test_verify_tampered_signature(self):
        from agent.audit_expert.rsa_sign import (
            sign_payload,
            verify_payload_signature,
        )

        sig = sign_payload("x")
        # 篡改签名（翻转末尾字符）
        tampered = sig[:-1] + ("A" if sig[-1] != "A" else "B")
        assert not verify_payload_signature("x", tampered)

    def test_signature_is_base64(self):
        from agent.audit_expert.rsa_sign import sign_payload

        sig = sign_payload("test")
        # base64 字符集
        for c in sig:
            assert c in "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/="

    def test_get_public_key_pem(self):
        from agent.audit_expert.rsa_sign import get_verification_public_key_pem

        pem = get_verification_public_key_pem()
        assert "-----BEGIN PUBLIC KEY-----" in pem
        assert "-----END PUBLIC KEY-----" in pem
        assert "BEGIN" in pem and "END" in pem

    def test_sign_long_payload(self):
        from agent.audit_expert.rsa_sign import (
            sign_payload,
            verify_payload_signature,
        )

        long_payload = "x" * 10000
        sig = sign_payload(long_payload)
        assert verify_payload_signature(long_payload, sig)

    def test_sign_empty_payload(self):
        from agent.audit_expert.rsa_sign import (
            sign_payload,
            verify_payload_signature,
        )

        sig = sign_payload("")
        assert verify_payload_signature("", sig)


# ---- mfa TOTP 测试 -------------------------------------------------------


class TestTotp:
    """TOTP MFA 模块测试（RFC 6238）。"""

    def test_generate_totp_basic(self):
        from agent.audit_expert.mfa import generate_totp

        code = generate_totp("JBSWY3DPEHPK3PXP")
        assert len(code) == 6
        assert code.isdigit()

    def test_verify_totp_current(self):
        from agent.audit_expert.mfa import generate_totp, verify_totp

        secret = "JBSWY3DPEHPK3PXP"
        code = generate_totp(secret)
        assert verify_totp(secret, code)

    def test_verify_totp_invalid(self):
        from agent.audit_expert.mfa import verify_totp

        assert not verify_totp("JBSWY3DPEHPK3PXP", "000000")

    def test_verify_totp_empty(self):
        from agent.audit_expert.mfa import verify_totp

        assert not verify_totp("JBSWY3DPEHPK3PXP", "")

    def test_verify_totp_non_digits(self):
        from agent.audit_expert.mfa import verify_totp

        assert not verify_totp("JBSWY3DPEHPK3PXP", "abcdef")

    def test_verify_totp_window(self):
        """±1 窗口内有效。"""
        from agent.audit_expert.mfa import generate_totp, verify_totp

        secret = "JBSWY3DPEHPK3PXP"
        # 30 秒前生成的码（counter - 1）
        past_code = generate_totp(secret, timestamp=time.time() - 30)
        assert verify_totp(secret, past_code)
        # 60 秒前（counter - 2，超出窗口）
        far_past_code = generate_totp(secret, timestamp=time.time() - 60)
        assert not verify_totp(secret, far_past_code)

    def test_get_or_create_user_secret(self):
        from agent.audit_expert.mfa import get_or_create_user_secret

        secret1 = get_or_create_user_secret("test_user_v1_001")
        secret2 = get_or_create_user_secret("test_user_v1_001")
        # 同一用户名 → 同一密钥
        assert secret1 == secret2
        assert len(secret1) > 0

    def test_get_current_totp_for_user(self):
        from agent.audit_expert.mfa import get_current_totp_for_user

        code = get_current_totp_for_user("test_user_v1_002")
        assert len(code) == 6
        assert code.isdigit()


# ---- compliance 测试 -----------------------------------------------------


class TestComplianceDual:
    """V1 compliance 验证 high/critical 风险不会改变（dual_required 是 api 层逻辑）。"""

    def test_destructive_op_for_high_risk(self):
        from agent.audit_expert.compliance import run_compliance_checks
        from agent.audit_expert.models import RiskLevel

        checks = run_compliance_checks(
            task_id="t",
            risk_level=RiskLevel.HIGH,
            pending_tool_call={"name": "db.execute", "args": {"sql": "DELETE FROM users"}},
            evidence_count=3,
        )
        destructive = [c for c in checks if c.rule_name == "DESTRUCTIVE_OP"]
        assert len(destructive) == 1
        assert destructive[0].level.value == "violation"


# ---- api 端点 V1 测试 -----------------------------------------------------


class TestApiV1:
    """V1 api.py 端点测试（TOTP + RSA + 双人）。"""

    @pytest.fixture
    def client(self, tmp_path, monkeypatch):

        from agent.audit_expert.rsa_sign import reset_signing_key_cache
        from agent.audit_expert.store import (
            reset_default_storage,
        )
        from agent.config import settings

        reset_default_storage()
        reset_signing_key_cache()
        # 清理 TOTP 默认密钥缓存（保证每个测试用唯一 actor）
        db_path = tmp_path / "audit_expert.db"
        monkeypatch.setattr(settings, "audit_expert_db_path", str(db_path))

        from agent.audit_expert.api import router as audit_api_router
        from fastapi import FastAPI
        from fastapi.testclient import TestClient

        app = FastAPI()
        app.include_router(audit_api_router)
        return TestClient(app)

    def test_create_high_risk_task_sets_dual_required(self, client):
        """high/critical 风险任务自动启 dual_required。"""
        resp = client.post(
            "/audit/tasks",
            json={
                "run_id": "r1",
                "title": "T",
                "description": "D",
                "risk_level": "high",
                "pending_tool_call": {},
                "requested_by": "u",
            },
        )
        assert resp.status_code == 200
        assert resp.json()["dual_required"] is True

    def test_create_medium_risk_no_dual(self, client):
        resp = client.post(
            "/audit/tasks",
            json={
                "run_id": "r1",
                "title": "T",
                "description": "D",
                "risk_level": "medium",
                "pending_tool_call": {},
                "requested_by": "u",
            },
        )
        assert resp.json()["dual_required"] is False

    def test_decide_with_valid_totp(self, client):
        """完整决策：TOTP + RSA 签名。"""
        from agent.audit_expert.mfa import get_current_totp_for_user

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
        totp = get_current_totp_for_user("alice")

        resp = client.post(
            f"/audit/tasks/{task_id}/decide",
            json={
                "action_type": "approve",
                "actor": "alice",
                "reason": "approved by alice",
                "mfa_verified": True,
                "totp_code": totp,
                "use_rsa": True,
            },
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["new_status"] == "approved"
        assert body["rsa_signature"]  # RSA 签名非空

    def test_decide_invalid_totp_rejected(self, client):
        create = client.post(
            "/audit/tasks",
            json={
                "run_id": "r1",
                "title": "T",
                "description": "D",
                "risk_level": "high",
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
                "reason": "test",
                "mfa_verified": True,
                "totp_code": "000000",  # 错的 TOTP
                "use_rsa": False,
            },
        )
        # TOTP 错误 → high 风险 + mfa_verified=true 但 totp 失败 → 400
        # 实际行为：_enforce_mfa_and_signature 返 err
        assert resp.status_code == 400

    def test_high_risk_no_mfa_rejected(self, client):
        """high 风险任务且 mfa_verified=False → 拒绝。"""
        create = client.post(
            "/audit/tasks",
            json={
                "run_id": "r1",
                "title": "T",
                "description": "D",
                "risk_level": "critical",
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
                "reason": "test",
                "mfa_verified": False,
                "totp_code": None,
                "use_rsa": False,
            },
        )
        assert resp.status_code == 400

    def test_dual_first_approve(self, client):
        """双人复核第一审批。"""
        from agent.audit_expert.mfa import get_current_totp_for_user

        create = client.post(
            "/audit/tasks",
            json={
                "run_id": "r1",
                "title": "T",
                "description": "D",
                "risk_level": "critical",
                "pending_tool_call": {},
                "requested_by": "u",
            },
        ).json()
        task_id = create["task_id"]
        assert create["dual_required"] is True
        totp = get_current_totp_for_user("alice")

        resp = client.post(
            f"/audit/tasks/{task_id}/dual-first",
            json={
                "actor": "alice",
                "reason": "first approval",
                "mfa_verified": True,
                "totp_code": totp,
                "use_rsa": True,
            },
        )
        assert resp.status_code == 200
        assert resp.json()["new_status"] == "pending"  # 仍 pending
        assert resp.json()["first_approver"] == "alice"

    def test_dual_first_requires_dual(self, client):
        """非 dual_required 任务 → 不能调 dual-first。"""
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
        resp = client.post(
            f"/audit/tasks/{task_id}/dual-first",
            json={
                "actor": "alice",
                "reason": "test",
                "mfa_verified": True,
                "totp_code": None,
                "use_rsa": False,
            },
        )
        assert resp.status_code == 400

    def test_dual_second_requires_dual_first(self, client):
        """dual-second 必须先 dual-first。"""
        create = client.post(
            "/audit/tasks",
            json={
                "run_id": "r1",
                "title": "T",
                "description": "D",
                "risk_level": "critical",
                "pending_tool_call": {},
                "requested_by": "u",
            },
        ).json()
        task_id = create["task_id"]
        resp = client.post(
            f"/audit/tasks/{task_id}/dual-second",
            json={
                "actor": "bob",
                "reason": "second",
                "mfa_verified": True,
                "totp_code": None,
                "use_rsa": False,
            },
        )
        assert resp.status_code == 400

    def test_dual_second_different_actor(self, client):
        """dual-second actor 必须与 dual-first 不同。"""
        from agent.audit_expert.mfa import get_current_totp_for_user

        create = client.post(
            "/audit/tasks",
            json={
                "run_id": "r1",
                "title": "T",
                "description": "D",
                "risk_level": "critical",
                "pending_tool_call": {},
                "requested_by": "u",
            },
        ).json()
        task_id = create["task_id"]
        totp = get_current_totp_for_user("alice")
        client.post(
            f"/audit/tasks/{task_id}/dual-first",
            json={
                "actor": "alice",
                "reason": "first",
                "mfa_verified": True,
                "totp_code": totp,
                "use_rsa": True,
            },
        )
        # 同 actor 调 dual-second
        resp = client.post(
            f"/audit/tasks/{task_id}/dual-second",
            json={
                "actor": "alice",
                "reason": "second",
                "mfa_verified": True,
                "totp_code": totp,
                "use_rsa": True,
            },
        )
        assert resp.status_code == 400

    def test_dual_full_flow(self, client):
        """完整双人流程：第一审批 + 第二审批 → approved。"""
        from agent.audit_expert.mfa import get_current_totp_for_user

        create = client.post(
            "/audit/tasks",
            json={
                "run_id": "r1",
                "title": "T",
                "description": "D",
                "risk_level": "critical",
                "pending_tool_call": {},
                "requested_by": "u",
            },
        ).json()
        task_id = create["task_id"]
        # 30s 内 TOTP 相同 → alice 和 bob 用同一码通过验证
        totp = get_current_totp_for_user("alice")
        resp1 = client.post(
            f"/audit/tasks/{task_id}/dual-first",
            json={
                "actor": "alice",
                "reason": "first approval",
                "mfa_verified": True,
                "totp_code": totp,
                "use_rsa": True,
            },
        )
        assert resp1.status_code == 200, f"first failed: {resp1.json()}"
        resp2 = client.post(
            f"/audit/tasks/{task_id}/dual-second",
            json={
                "actor": "bob",
                "reason": "second approval",
                "mfa_verified": True,
                "totp_code": totp,
                "use_rsa": True,
            },
        )
        assert resp2.status_code == 200, f"second failed: {resp2.json()}"
        assert resp2.json()["new_status"] == "approved"

    def test_verify_chain_after_dual(self, client):
        """双人流程后 verify 返 RSA signed count >= 2。"""
        from agent.audit_expert.mfa import get_current_totp_for_user

        create = client.post(
            "/audit/tasks",
            json={
                "run_id": "r1",
                "title": "T",
                "description": "D",
                "risk_level": "critical",
                "pending_tool_call": {},
                "requested_by": "u",
            },
        ).json()
        task_id = create["task_id"]
        totp = get_current_totp_for_user("alice")
        client.post(
            f"/audit/tasks/{task_id}/dual-first",
            json={
                "actor": "alice",
                "reason": "first",
                "mfa_verified": True,
                "totp_code": totp,
                "use_rsa": True,
            },
        )
        client.post(
            f"/audit/tasks/{task_id}/dual-second",
            json={
                "actor": "bob",
                "reason": "second",
                "mfa_verified": True,
                "totp_code": totp,
                "use_rsa": True,
            },
        )

        resp = client.get(f"/audit/tasks/{task_id}/verify")
        assert resp.status_code == 200
        body = resp.json()
        assert body["valid"] is True
        assert body["action_count"] >= 2
        assert body["rsa_signed_actions"] >= 2

    def test_get_mfa_code_endpoint(self, client):
        resp = client.get("/audit/mfa/test_user_demo")
        assert resp.status_code == 200
        assert "totp_code" in resp.json()
        assert len(resp.json()["totp_code"]) == 6

    def test_get_public_key_endpoint(self, client):
        resp = client.get("/audit/public-key")
        assert resp.status_code == 200
        body = resp.json()
        assert body["algorithm"] == "RSA-2048-PSS-SHA256"
        assert body["public_key_pem"].startswith("-----BEGIN PUBLIC KEY-----")

    def test_get_task_includes_dual_fields(self, client):
        create = client.post(
            "/audit/tasks",
            json={
                "run_id": "r1",
                "title": "T",
                "description": "D",
                "risk_level": "high",
                "pending_tool_call": {},
                "requested_by": "u",
            },
        ).json()
        task_id = create["task_id"]
        resp = client.get(f"/audit/tasks/{task_id}")
        body = resp.json()
        assert "dual_required" in body
        assert "first_approver" in body
        assert "second_approver" in body
        assert body["dual_required"] is True

    def test_decide_dual_required_routed_via_dual_first(self, client):
        """dual_required 任务调 /decide 而不是 /dual-first → 400。"""
        create = client.post(
            "/audit/tasks",
            json={
                "run_id": "r1",
                "title": "T",
                "description": "D",
                "risk_level": "high",
                "pending_tool_call": {},
                "requested_by": "u",
            },
        ).json()
        task_id = create["task_id"]
        from agent.audit_expert.mfa import get_current_totp_for_user

        totp = get_current_totp_for_user("alice")
        resp = client.post(
            f"/audit/tasks/{task_id}/decide",
            json={
                "action_type": "approve",
                "actor": "alice",
                "reason": "try",
                "mfa_verified": True,
                "totp_code": totp,
                "use_rsa": True,
            },
        )
        # dual_required 应强制走 dual-first
        assert resp.status_code == 400
