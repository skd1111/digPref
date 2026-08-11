"""test_ops_case.py —— 专家验收工作流 Case 路由测试（2026-08-10）。

覆盖：Case 查询 / 材料上传 / AI 审核（通过与打回、解析失败、LLM 不可用）/
人工改判 / 删除 / 迷你问答 / 图片视觉降级 / zip 导出（含审计事件）。
"""

from __future__ import annotations

import base64
import json
import sqlite3
import zipfile

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient


@pytest.fixture
def client(tmp_path, monkeypatch):
    db = str(tmp_path / "ops.db")
    audit_db = str(tmp_path / "audit.sqlite")
    monkeypatch.setenv("EAIDE_OPS_DB", db)
    monkeypatch.setenv("EAIDE_AUDIT_DB_PATH", audit_db)

    from agent.ops import api as ops_api

    ops_api._reset_storage_for_tests()
    ops_api._reset_case_storage_for_tests()
    app = FastAPI()
    app.include_router(ops_api.router)
    return TestClient(app)


@pytest.fixture(autouse=True)
def _isolate_sessions_archive(tmp_path, monkeypatch):
    """问答归档的 sessions 存储隔离到临时目录，绝不碰真实 %APPDATA% sessions.db。"""
    from agent.ops import api as ops_api
    from agent.sessions.storage import SessionStorage

    store = SessionStorage(str(tmp_path / "sessions.db"))
    monkeypatch.setattr(ops_api, "_sessions_store", lambda: store)


def _b64(text: str) -> str:
    return base64.b64encode(text.encode("utf-8")).decode("ascii")


def _upload(
    client: TestClient, name: str = "营业执照.txt", text: str = "统一社会信用代码 91310000XXXX"
) -> dict:
    r = client.post(
        "/ops/case/files",
        json={
            "case_id": "bank__ops_open",
            "team_id": "due_diligence_team",
            "member_key": "客户身份识别专家",
            "file_name": name,
            "content_base64": _b64(text),
        },
    )
    assert r.status_code == 200, r.text
    return r.json()


def _mock_llm(monkeypatch, reply: str):
    from agent.ops import api as ops_api

    async def fake(_messages):
        return reply

    monkeypatch.setattr(ops_api, "_make_summarize_llm", lambda: fake)


def _mock_llm_seq(monkeypatch, replies: list[str]):
    """按调用顺序依次返回（草稿表单 JSON 与审核 JSON 串场隔离）。"""
    from agent.ops import api as ops_api

    it = iter(replies)

    async def fake(_messages):
        return next(it)

    monkeypatch.setattr(ops_api, "_make_summarize_llm", lambda: fake)


def test_get_case_empty(client):
    r = client.get("/ops/case", params={"project_name": "bank", "feature_id": "ops_open"})
    assert r.status_code == 200
    body = r.json()
    assert body["case_id"] == "bank__ops_open"
    assert body["files"] == []
    assert body["qa"] == []


def test_upload_file_persists(client):
    row = _upload(client)
    assert row["id"].startswith("CF-")
    assert row["status"] == "pending"
    assert row["member_key"] == "客户身份识别专家"
    # 落盘成功且内容正确
    with open(row["file_path"], encoding="utf-8") as f:
        assert "91310000XXXX" in f.read()

    r = client.get("/ops/case", params={"project_name": "bank", "feature_id": "ops_open"})
    assert len(r.json()["files"]) == 1


@pytest.mark.asyncio
async def test_review_passed(client, monkeypatch):
    row = _upload(client)
    _mock_llm(
        monkeypatch,
        json.dumps(
            {
                "verdict": "passed",
                "note": "执照信息清晰可辨认",
                "fields": [{"field": "企业名称", "value": "测试贸易有限公司", "confidence": 0.9}],
                "evidence": ["91310000XXXX"],
            },
            ensure_ascii=False,
        ),
    )
    r = client.post(f"/ops/case/files/{row['id']}/review")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["status"] == "passed"
    assert body["reviewed_by"] == "ai"
    assert "执照信息清晰" in body["review_note"]
    # 要素提取：LLM 要素落库
    fields = body["extracted_fields"]
    assert any(f["field"] == "企业名称" for f in fields)
    # 证据链：真实存在于原文的摘录才保留
    assert body["evidence"] == ["91310000XXXX"]


@pytest.mark.asyncio
async def test_review_garbage_reply_still_gives_regex_fields(client, monkeypatch):
    """纠错成本陷阱兑底：LLM 返回非 JSON 时，正则要素照样给。"""
    row = _upload(client, name="营业执照.txt", text="统一社会信用代码 91350100M000100Y43，法人张三")
    _mock_llm(monkeypatch, "看不懂的自由文本")
    body = client.post(f"/ops/case/files/{row['id']}/review").json()
    assert body["status"] == "rejected"  # 保守打回
    names = [f["field"] for f in body["extracted_fields"]]
    assert "统一社会信用代码" in names


@pytest.mark.asyncio
async def test_review_evidence_hallucination_filtered(client, monkeypatch):
    """证据链防幻觉：LLM 编造的摘录（不在原文里）不进 evidence。"""
    row = _upload(client)
    _mock_llm(
        monkeypatch,
        json.dumps(
            {
                "verdict": "passed",
                "note": "ok",
                "fields": [],
                "evidence": ["91310000XXXX", "这段原文根本不存在"],
            },
            ensure_ascii=False,
        ),
    )
    body = client.post(f"/ops/case/files/{row['id']}/review").json()
    assert body["evidence"] == ["91310000XXXX"]


@pytest.mark.asyncio
async def test_review_rejected_and_parse_fallback(client, monkeypatch):
    row = _upload(client)
    _mock_llm(monkeypatch, '{"verdict": "rejected", "note": "关键编号模糊"}')
    body = client.post(f"/ops/case/files/{row['id']}/review").json()
    assert body["status"] == "rejected"

    # LLM 返回非 JSON → 保守打回
    _mock_llm(monkeypatch, "看不懂的自由文本")
    body = client.post(f"/ops/case/files/{row['id']}/review").json()
    assert body["status"] == "rejected"
    assert "保守打回" in body["review_note"]


@pytest.mark.asyncio
async def test_review_llm_unavailable_falls_back_pending(client, monkeypatch):
    from agent.ops import api as ops_api

    row = _upload(client)

    async def broken(_messages):
        raise RuntimeError("所有 LLM 后端均不可用（mock）")

    monkeypatch.setattr(ops_api, "_make_summarize_llm", lambda: broken)
    r = client.post(f"/ops/case/files/{row['id']}/review")
    assert r.status_code == 502
    # 状态回退 pending 且写明原因
    got = client.get("/ops/case", params={"project_name": "bank", "feature_id": "ops_open"}).json()
    assert got["files"][0]["status"] == "pending"
    assert "AI 审核不可用" in got["files"][0]["review_note"]


@pytest.mark.asyncio
async def test_review_image_vision_fallback(client, monkeypatch):
    from agent.ops import api as ops_api

    # 视觉模型不可用 → 降级为关注点核对清单 + 待人工确认（不调 LLM）
    async def no_vision(_path):
        return ""

    monkeypatch.setattr(ops_api, "_vision_describe", no_vision)
    r = client.post(
        "/ops/case/files",
        json={
            "case_id": "bank__ops_open",
            "team_id": "due_diligence_team",
            "member_key": "客户身份识别专家",
            "file_name": "营业执照.jpg",
            "content_base64": _b64("fake-image-bytes"),
        },
    )
    row = r.json()
    body = client.post(f"/ops/case/files/{row['id']}/review").json()
    assert body["status"] == "pending"
    assert body["reviewed_by"] == "ai"
    assert "人工" in body["review_note"]
    # 低置信占位要素：前端会标红提醒人工核对
    assert body["extracted_fields"][0]["confidence"] < 0.6


def test_override_and_delete(client):
    row = _upload(client)
    r = client.post(
        f"/ops/case/files/{row['id']}/override", json={"status": "passed", "note": "人工核对无误"}
    )
    body = r.json()
    assert body["status"] == "passed"
    assert body["reviewed_by"] == "human"

    # 非法状态拒绝
    assert (
        client.post(f"/ops/case/files/{row['id']}/override", json={"status": "bogus"}).status_code
        == 400
    )

    assert client.delete(f"/ops/case/files/{row['id']}").status_code == 200
    assert client.delete(f"/ops/case/files/{row['id']}").status_code == 404


@pytest.mark.asyncio
async def test_override_ai_result_records_correction(client, monkeypatch):
    """纠错闭环（铁律 2）：AI 结论被人工改判 → 落纠错样本；人工再改判不重复落。"""
    row = _upload(client)
    _mock_llm(monkeypatch, '{"verdict": "rejected", "note": "编号模糊"}')
    client.post(f"/ops/case/files/{row['id']}/review")

    # 人工改判为通过 → 记录一条纠错样本
    client.post(
        f"/ops/case/files/{row['id']}/override",
        json={"status": "passed", "note": "原件核对无误"},
    )
    body = client.get("/ops/case/corrections", params={"case_id": "bank__ops_open"}).json()
    assert body["total"] == 1
    corr = body["corrections"][0]
    assert corr["ai_status"] == "rejected"
    assert corr["human_status"] == "passed"
    assert corr["file_name"] == "营业执照.txt"

    # 再次人工改判（此时 reviewed_by 已是 human）→ 不重复落样本
    client.post(f"/ops/case/files/{row['id']}/override", json={"status": "rejected"})
    after = client.get("/ops/case/corrections", params={"case_id": "bank__ops_open"}).json()
    assert after["total"] == 1


@pytest.mark.asyncio
async def test_crosscheck_detects_inconsistency(client, monkeypatch):
    """人肉比对变自动交叉：同名字段跨材料不一致 → 标红提示。"""
    f1 = _upload(client, name="营业执照.txt", text="营业执照 法人：张三")
    f2 = _upload(client, name="授权书.txt", text="授权书 法人：李四")
    _mock_llm(
        monkeypatch,
        json.dumps(
            {
                "verdict": "passed",
                "note": "ok",
                "fields": [{"field": "法人", "value": "张三", "confidence": 0.9}],
                "evidence": [],
            },
            ensure_ascii=False,
        ),
    )
    client.post(f"/ops/case/files/{f1['id']}/review")
    _mock_llm(
        monkeypatch,
        json.dumps(
            {
                "verdict": "passed",
                "note": "ok",
                "fields": [{"field": "法人", "value": "李四", "confidence": 0.4}],
                "evidence": [],
            },
            ensure_ascii=False,
        ),
    )
    client.post(f"/ops/case/files/{f2['id']}/review")

    body = client.get("/ops/case/crosscheck", params={"case_id": "bank__ops_open"}).json()
    assert body["consistent"] is False
    inc = body["inconsistencies"][0]
    assert inc["field"] == "法人"
    assert {v["value"] for v in inc["values"]} == {"张三", "李四"}
    # 低置信清单：confidence 0.4 的「法人」在列
    assert any(lc["field"] == "法人" for lc in body["low_confidence"])


@pytest.mark.asyncio
async def test_ask_expert(client, monkeypatch):
    _mock_llm(monkeypatch, "营业执照需在有效期内，且经营范围覆盖申请业务。")
    r = client.post(
        "/ops/case/ask",
        json={
            "case_id": "bank__ops_open",
            "team_id": "due_diligence_team",
            "member_key": "客户身份识别专家",
            "question": "营业执照要注意什么？",
        },
    )
    assert r.status_code == 200, r.text
    qa = r.json()["qa"]
    assert qa["question"] == "营业执照要注意什么？"
    assert "有效期" in qa["answer"]
    got = client.get("/ops/case", params={"project_name": "bank", "feature_id": "ops_open"}).json()
    assert len(got["qa"]) == 1


@pytest.mark.asyncio
async def test_ask_expert_with_knowledge_sources(client, monkeypatch, tmp_path):
    """找制度变问助手：知识库命中时回答附制度出处（防黑盒）。"""
    kb = tmp_path / "kb"
    kb.mkdir()
    (kb / "01-合规风险.md").write_text(
        "# 合规风险清单\n\n## 反洗钱\n客户风险等级评定必须留痕，高风险客户需强化尽调。\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("EAIDE_KB_DIR", str(kb))
    _mock_llm(monkeypatch, "需按制度对客户进行风险等级评定并留痕。")
    r = client.post(
        "/ops/case/ask",
        json={
            "case_id": "bank__ops_open",
            "team_id": "due_diligence_team",
            "member_key": "反洗钱专家",
            "question": "反洗钱客户风险等级评定有什么要求？",
        },
    )
    assert r.status_code == 200, r.text
    answer = r.json()["qa"]["answer"]
    assert "制度出处" in answer
    assert "合规风险清单" in answer
    assert "01-合规风险.md" in answer


@pytest.mark.asyncio
async def test_export_zip_contents(client, tmp_path, monkeypatch):
    _mock_llm(monkeypatch, "本笔业务资料齐全，专家验收全部通过。")
    row = _upload(client)
    client.post(f"/ops/case/files/{row['id']}/override", json={"status": "passed", "note": "ok"})

    target = tmp_path / "out" / "交付物.zip"
    target.parent.mkdir(parents=True, exist_ok=True)
    r = client.post(
        "/ops/case/export",
        json={
            "case_id": "bank__ops_open",
            "target_path": str(target),
            "feature_name": "对公开户",
            "team_name": "尽职调查专家团",
            "checklist": ["客户身份识别专家 → 客户身份基本信息表"],
        },
    )
    assert r.status_code == 200, r.text
    assert r.json()["ok"] is True
    assert target.exists()

    with zipfile.ZipFile(target) as zf:
        names = zf.namelist()
        assert any(n.startswith("交付文件/客户身份识别专家/") for n in names)
        assert "检查结果/客户身份识别专家.md" in names
        assert "README.md" in names
        assert "业务小结.md" in names
        # 报告初稿（docx 优先，写八股文变改填空题）
        assert "尽调报告初稿.docx" in names
        readme = zf.read("README.md").decode("utf-8")
        assert "尽职调查专家团" in readme
        assert "客户身份基本信息表" in readme
        summary = zf.read("业务小结.md").decode("utf-8")
        assert "专家验收全部通过" in summary
        # docx 可被 python-docx 打开且含人机边界提示
        import io

        from docx import Document

        doc = Document(io.BytesIO(zf.read("尽调报告初稿.docx")))
        full = "\n".join(p.text for p in doc.paragraphs)
        assert "需人工填写" in full
        assert "需人工确认" in full


def test_export_requires_files(client, tmp_path):
    target = tmp_path / "empty.zip"
    r = client.post(
        "/ops/case/export",
        json={"case_id": "bank__ops_open", "target_path": str(target)},
    )
    assert r.status_code == 400


@pytest.mark.asyncio
async def test_case_actions_write_audit(client, tmp_path, monkeypatch):
    """审核功能：上传/审核/导出等写操作必须留下审计事件。"""
    row = _upload(client)
    _mock_llm(monkeypatch, json.dumps({"verdict": "passed", "note": "ok"}, ensure_ascii=False))
    client.post(f"/ops/case/files/{row['id']}/review")

    audit_db = tmp_path / "audit.sqlite"
    conn = sqlite3.connect(audit_db)
    try:
        actions = {str(a[0]) for a in conn.execute("SELECT action FROM audit").fetchall()}
    finally:
        conn.close()
    assert "ops_case_file_add" in actions
    assert "ops_case_file_review" in actions


# ---------------------------------------------------------------------------
# 交付草稿（BUGFIX #78）：要「模板/清单」不给一大段文字，界面直填 →
# 提交后自动成为材料走专家审核 → 通过即并入交付物 zip。
# ---------------------------------------------------------------------------

_DRAFT_TEMPLATE_REPLY = json.dumps(
    {
        "title": "对公开户资料清单",
        "fields": [
            {"name": "corp_name", "label": "企业名称", "type": "text", "required": True},
            {"name": "license_no", "label": "统一社会信用代码", "type": "text", "required": True},
            {"name": "remark", "label": "备注", "type": "textarea", "required": False},
        ],
    },
    ensure_ascii=False,
)


def _ask_template(client: TestClient) -> dict:
    r = client.post(
        "/ops/case/ask",
        json={
            "case_id": "bank__ops_open",
            "team_id": "due_diligence_team",
            "member_key": "客户身份识别专家",
            "question": "给我的资料清单与模板文件（通用版）",
        },
    )
    assert r.status_code == 200, r.text
    return r.json()


@pytest.mark.asyncio
async def test_ask_template_creates_draft_not_wall_of_text(client, monkeypatch):
    _mock_llm(monkeypatch, _DRAFT_TEMPLATE_REPLY)
    body = _ask_template(client)
    # 回答是简短引导，正文在结构化草稿里（不再砸一大段文字）
    draft = body["draft"]
    assert draft["title"] == "对公开户资料清单"
    assert len(draft["template"]) == 3
    assert draft["status"] == "draft"
    assert "填写表单" in body["qa"]["answer"]
    # Case 查询同步可见
    got = client.get("/ops/case", params={"project_name": "bank", "feature_id": "ops_open"}).json()
    assert len(got["drafts"]) == 1


@pytest.mark.asyncio
async def test_ask_template_unparsable_falls_back_to_plain_answer(client, monkeypatch):
    """LLM 未返回有效表单 JSON → 降级普通问答，绝不断链。"""
    _mock_llm(monkeypatch, "这是纯文本回答，没有 JSON。")
    body = _ask_template(client)
    assert "draft" not in body
    # 降级普通问答（可能附知识库出处尾巴，只断言正文在）
    assert body["qa"]["answer"].startswith("这是纯文本回答，没有 JSON。")


@pytest.mark.asyncio
async def test_draft_submit_missing_required_rejected(client, monkeypatch):
    _mock_llm(monkeypatch, _DRAFT_TEMPLATE_REPLY)
    draft = _ask_template(client)["draft"]
    r = client.post(f"/ops/case/drafts/{draft['id']}/submit")
    assert r.status_code == 400
    assert "必填项未填写" in r.json()["detail"]


@pytest.mark.asyncio
async def test_draft_fill_submit_passes_and_exports(client, monkeypatch, tmp_path):
    """填写 → 提交 → 自动审核通过 → 草稿 passed 且并入交付物 zip。"""
    _mock_llm_seq(
        monkeypatch,
        [
            _DRAFT_TEMPLATE_REPLY,
            json.dumps(
                {"verdict": "passed", "note": "资料齐全", "fields": [], "evidence": []},
                ensure_ascii=False,
            ),
        ],
    )
    draft = _ask_template(client)["draft"]
    r = client.put(
        f"/ops/case/drafts/{draft['id']}",
        json={
            "values": {
                "corp_name": "某某贸易有限公司",
                "license_no": "91310000XXXX",
                "unknown_field": "模板外字段应被丢弃",
            }
        },
    )
    assert r.status_code == 200, r.text
    assert r.json()["values"]["corp_name"] == "某某贸易有限公司"
    assert "unknown_field" not in r.json()["values"]

    r = client.post(f"/ops/case/drafts/{draft['id']}/submit")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["draft"]["status"] == "passed"
    assert body["file"]["status"] == "passed"
    with open(body["file"]["file_path"], encoding="utf-8") as f:
        content = f.read()
    assert "某某贸易有限公司" in content and "91310000XXXX" in content

    # 通过后自然并入导出 zip 的交付文件目录
    target = tmp_path / "delivery.zip"
    r = client.post(
        "/ops/case/export",
        json={"case_id": "bank__ops_open", "target_path": str(target)},
    )
    assert r.status_code == 200, r.text
    with zipfile.ZipFile(target) as zf:
        names = zf.namelist()
    assert any("对公开户资料清单" in n and n.startswith("交付文件/") for n in names)


@pytest.mark.asyncio
async def test_draft_rejected_can_refill_and_resubmit(client, monkeypatch):
    """被打回 → 修改重提 → 复用同一材料行重审。"""
    _mock_llm_seq(
        monkeypatch,
        [
            _DRAFT_TEMPLATE_REPLY,
            json.dumps(
                {"verdict": "rejected", "note": "信用代码位数不对", "fields": [], "evidence": []},
                ensure_ascii=False,
            ),
            json.dumps(
                {"verdict": "passed", "note": "ok", "fields": [], "evidence": []},
                ensure_ascii=False,
            ),
        ],
    )
    draft = _ask_template(client)["draft"]
    client.put(
        f"/ops/case/drafts/{draft['id']}",
        json={"values": {"corp_name": "甲公司", "license_no": "123"}},
    )
    r = client.post(f"/ops/case/drafts/{draft['id']}/submit")
    assert r.json()["draft"]["status"] == "submitted"
    assert r.json()["file"]["status"] == "rejected"
    first_file_id = r.json()["file"]["id"]

    client.put(
        f"/ops/case/drafts/{draft['id']}",
        json={"values": {"corp_name": "甲公司", "license_no": "91310000XXXX"}},
    )
    r = client.post(f"/ops/case/drafts/{draft['id']}/submit")
    assert r.status_code == 200, r.text
    assert r.json()["draft"]["status"] == "passed"
    assert r.json()["file"]["id"] == first_file_id


@pytest.mark.asyncio
async def test_draft_prefill_from_material_and_snapshot(client, monkeypatch):
    """BUGFIX #79：材料提取要素自动预填草稿；提交时存快照与版本计数。"""
    _mock_llm_seq(
        monkeypatch,
        [
            # 1) 材料审核：产出高置信要素「企业名称」
            json.dumps(
                {
                    "verdict": "passed",
                    "note": "ok",
                    "fields": [
                        {"field": "企业名称", "value": "某某贸易有限公司", "confidence": 0.9}
                    ],
                    "evidence": [],
                },
                ensure_ascii=False,
            ),
            # 2) 草稿模板
            _DRAFT_TEMPLATE_REPLY,
            # 3) 草稿提交后的自动审核
            json.dumps(
                {"verdict": "passed", "note": "ok", "fields": [], "evidence": []},
                ensure_ascii=False,
            ),
        ],
    )
    f = _upload(client)
    r = client.post(f"/ops/case/files/{f['id']}/review")
    assert r.status_code == 200, r.text

    body = _ask_template(client)
    draft = body["draft"]
    # label「企业名称」命中要素 → 自动预填（少打字，用户只核不改）
    assert draft["values"].get("corp_name") == "某某贸易有限公司"

    # 补齐其余必填 → 提交 → 快照与版本计数落库
    client.put(
        f"/ops/case/drafts/{draft['id']}",
        json={"values": {"corp_name": "某某贸易有限公司", "license_no": "91310000XXXX"}},
    )
    r = client.post(f"/ops/case/drafts/{draft['id']}/submit")
    assert r.status_code == 200, r.text
    got = client.get("/ops/case/drafts", params={"case_id": "bank__ops_open"}).json()
    d = got["drafts"][0]
    assert d["submit_count"] == 1
    assert "某某贸易有限公司" in d["last_snapshot"]


@pytest.mark.asyncio
async def test_review_reject_marks_locate_in_document(client, monkeypatch):
    """BUGFIX #80：打回时产出文档内定位；幻觉摘录（不在原文）被丢弃。"""
    text = "营业执照复印件\n统一社会信用代码 91310000XXXX\n执照已过有效期"
    f = _upload(client, "执照.txt", text)
    _mock_llm(
        monkeypatch,
        json.dumps(
            {
                "verdict": "rejected",
                "note": "执照已过期",
                "fields": [],
                "evidence": ["执照已过有效期"],
                "reject_spans": [
                    {"quote": "执照已过有效期", "advice": "请更换有效期内的营业执照"},
                    {"quote": "原文不存在这段内容", "advice": "幻觉摘录应被丢弃"},
                ],
            },
            ensure_ascii=False,
        ),
    )
    r = client.post(f"/ops/case/files/{f['id']}/review")
    assert r.status_code == 200, r.text
    row = r.json()
    assert row["status"] == "rejected"
    # 只保留真实存在于原文的定位，幻觉那条被丢
    assert len(row["reject_marks"]) == 1
    assert row["reject_marks"][0]["quote"] == "执照已过有效期"
    assert "有效期" in row["reject_marks"][0]["advice"]


@pytest.mark.asyncio
async def test_clear_case_resets_everything(client, monkeypatch):
    """BUGFIX #85：重新开始办理 —— 清空后材料/问答/草稿全部为空。"""
    _mock_llm(monkeypatch, _DRAFT_TEMPLATE_REPLY)
    _upload(client)          # 1 份材料
    body = _ask_template(client)  # 1 条问答 + 1 份草稿
    assert "draft" in body

    r = client.delete("/ops/case", params={"project_name": "bank", "feature_id": "ops_open"})
    assert r.status_code == 200, r.text
    assert r.json()["ok"] is True

    got = client.get("/ops/case", params={"project_name": "bank", "feature_id": "ops_open"}).json()
    assert got["files"] == []
    assert got["qa"] == []
    assert got["drafts"] == []


@pytest.mark.asyncio
async def test_ask_plain_question_checklist_answer_converts_to_draft(client, monkeypatch):
    """BUGFIX #82：问题没带模板关键词（如「需要什么材料」），回答是长文清单时也自动转草稿。"""
    checklist_answer = (
        "需准备以下尽调材料：\n"
        "- 营业执照副本（有效期内，加盖公章）\n"
        "- 法定代表人身份证原件及复印\n"
        "- 近三年经审计财务报表\n"
        "- 近六个月银行流水（覆盖八成以上交易量）\n"
        "- 公司章程及修正案\n"
        "- 股东会同意融资的决议\n"
        "- 抵质押物权属证明（如有）\n"
        "注：以上材料均需加盖公章，验原件留复印件。"
    )
    _mock_llm_seq(
        monkeypatch,
        [
            checklist_answer,  # 1) 普通问答
            _DRAFT_TEMPLATE_REPLY,  # 2) 回答 → 表单转换
        ],
    )
    r = client.post(
        "/ops/case/ask",
        json={
            "case_id": "bank__ops_open",
            "team_id": "due_diligence_team",
            "member_key": "尽调项目经理",
            "question": "需要什么材料",
        },
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert "draft" in body
    assert body["draft"]["title"] == "对公开户资料清单"
    # 气泡不再砸长文，只留简短引导
    assert "草稿表单" in body["qa"]["answer"]
    assert "营业执照副本" not in body["qa"]["answer"]


# ---------------------------------------------------------------------------
# 会话管理归档（2026-08-11）：专家团对话也进 sessions，修复「永远只有 1 个」
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_ask_archives_qa_to_sessions(client, tmp_path, monkeypatch):
    """专家问答自动归档 sessions：同 Case 复用一个会话，问答各一条消息。"""
    from agent.ops import api as ops_api
    from agent.sessions.storage import SessionStorage

    sstore = SessionStorage(str(tmp_path / "sessions.db"))
    monkeypatch.setattr(ops_api, "_sessions_store", lambda: sstore)
    _mock_llm(monkeypatch, "回答正文")

    for q in ("第一问", "第二问"):
        r = client.post(
            "/ops/case/ask",
            json={
                "case_id": "bank__ops_open",
                "team_id": "due_diligence_team",
                "member_key": "客户身份识别专家",
                "question": q,
            },
        )
        assert r.status_code == 200, r.text

    sessions = sstore.list_sessions(limit=10)
    assert len(sessions) == 1  # 同 Case 复用同一个会话
    assert "专家问答" in sessions[0].title
    msgs = sstore.list_messages(sessions[0].id)
    roles = [m.role for m in msgs]
    assert roles == ["user", "assistant", "user", "assistant"]
    assert msgs[0].content == "第一问"
    assert msgs[1].content.startswith("回答正文")  # 可能附知识库制度出处后缀


@pytest.mark.asyncio
async def test_ask_survives_archive_failure(client, monkeypatch):
    """归档失败只降级不断链：问答主链路照常返回（永不阻塞红线）。"""
    from agent.ops import api as ops_api

    def _boom():
        raise RuntimeError("sessions db unavailable")

    monkeypatch.setattr(ops_api, "_sessions_store", _boom)
    _mock_llm(monkeypatch, "回答正文")
    r = client.post(
        "/ops/case/ask",
        json={
            "case_id": "bank__ops_open",
            "team_id": "due_diligence_team",
            "member_key": "客户身份识别专家",
            "question": "归档挂了也要回答",
        },
    )
    assert r.status_code == 200, r.text
    assert r.json()["qa"]["answer"].startswith("回答正文")
