"""test_ops_credentials.py —— 凭证预览后端链路测试（2026-11-09）。

覆盖：
  - 专家团 credentials 节点装载 + 存量团补齐（backfill，不覆盖用户改过的清单）
  - 端侧影像元数据 ocr_image_meta（不可用时诚实降级）
  - 材料归类：关键词一级命中 → LLM 二级白名单 → 剩下留人工
  - 手动挂载（拖动）与非法节点名拒绝
  - OCR 自动填入顺带登记识别底图（credential_images，不走审核链路）
  - 凭证聚合视图（已传 / 缺失 / 要素来源与能否定位）
  - 台账详情带凭证摘要
"""

from __future__ import annotations

import base64
import json
from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

TEAM_ID = "cred_team"
MEMBER_A = "材料清单专家"
MEMBER_B = "合规质检专家"
LICENSE = "营业执照（副本）"
APPLY_FORM = "开户/变更申请书"
AUTH_PAGE = "客户告知与授权签署件"

_CASE = "bank__ops_open"


def _team_dict() -> dict[str, Any]:
    return {
        "schema_version": "1.0",
        "id": TEAM_ID,
        "name": "凭证测试团",
        "members": [
            {
                "name": MEMBER_A,
                "role": "材料",
                "credentials": [
                    {
                        "name": LICENSE,
                        "required": True,
                        "media": "image",
                        "hint": "四角完整",
                        "keywords": ["执照", "工商"],
                        "fields": [
                            {"name": "corp_name", "label": "企业名称"},
                            {"name": "credit_code", "label": "统一社会信用代码"},
                        ],
                    },
                    {"name": APPLY_FORM, "required": True, "media": "image"},
                ],
            },
            {
                "name": MEMBER_B,
                "role": "合规",
                "credentials": [{"name": AUTH_PAGE, "required": True, "media": "image"}],
            },
        ],
    }


class _FakeLoader:
    """只实现 get()：凭证相关端点仅用 loader.get(team_id)。"""

    def __init__(self, team: Any) -> None:
        self._team = team

    def get(self, team_id: str) -> Any:
        return self._team if team_id == TEAM_ID else None


@pytest.fixture
def client(tmp_path, monkeypatch):
    db = str(tmp_path / "ops.db")
    monkeypatch.setenv("EAIDE_OPS_DB", db)
    monkeypatch.setenv("EAIDE_AUDIT_DB_PATH", str(tmp_path / "audit.sqlite"))

    from agent.expert_teams.models import ExpertTeam
    from agent.ops import api as ops_api

    ops_api._reset_storage_for_tests()
    ops_api._reset_case_storage_for_tests()
    ops_api._reset_task_storage_for_tests()

    import agent.expert_teams.api as teams_api

    monkeypatch.setattr(teams_api, "_loader", _FakeLoader(ExpertTeam.from_dict(_team_dict())))

    app = FastAPI()
    app.include_router(ops_api.router)
    return TestClient(app)


def _b64(data: bytes | str) -> str:
    raw = data.encode("utf-8") if isinstance(data, str) else data
    return base64.b64encode(raw).decode("ascii")


def _upload(client: TestClient, name: str, member: str = MEMBER_A, text: str = "企业名称 某某公司") -> dict:
    r = client.post(
        "/ops/case/files",
        json={
            "case_id": _CASE,
            "team_id": TEAM_ID,
            "member_key": member,
            "file_name": name,
            # 端点要求 content_base64 非空（min_length=1）：无正文时放一个占位字节
            "content_base64": _b64(text or " "),
        },
    )
    assert r.status_code == 200, r.text
    return r.json()


def _view(client: TestClient) -> dict:
    r = client.get("/ops/case/credentials", params={"case_id": _CASE, "team_ids": TEAM_ID})
    assert r.status_code == 200, r.text
    return r.json()


def _member_view(view: dict, member: str) -> dict:
    for team in view["teams"]:
        for m in team["members"]:
            if m["member_key"] == member:
                return m
    raise AssertionError(f"成员视图缺失: {member}")


def _node(member_view: dict, name: str) -> dict:
    for node in member_view["credentials"]:
        if node["name"] == name:
            return node
    raise AssertionError(f"凭证节点缺失: {name}")


# ---------------------------------------------------------------------------
# 聚合视图：预设节点 / 已传 / 缺失
# ---------------------------------------------------------------------------


def test_credentials_view_lists_all_preset_nodes(client):
    view = _view(client)
    stats = view["stats"]
    assert stats["has_preset"] is True
    assert stats["required_total"] == 3
    assert stats["required_ok"] == 0
    assert {m["credential"] for m in stats["missing"]} == {LICENSE, APPLY_FORM, AUTH_PAGE}
    assert _node(_member_view(view, MEMBER_A), LICENSE)["media"] == "image"


def test_keyword_classification_mounts_material_and_marks_satisfied(client):
    row = _upload(client, "营业执照_副本.jpg")
    r = client.post("/ops/case/classify-credentials", json={"case_id": _CASE, "team_ids": [TEAM_ID]})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["classify_stats"]["keyword"] == 1
    node = _node(_member_view(body, MEMBER_A), LICENSE)
    assert node["satisfied"] is True
    assert [a["id"] for a in node["assets"]] == [row["id"]]
    assert node["assets"][0]["classified_by"] == "keyword"
    # 另一份必传凭证仍缺 → 汇总口径可用
    assert _node(_member_view(body, MEMBER_A), APPLY_FORM)["satisfied"] is False
    assert body["stats"]["required_ok"] == 1


def test_unmatched_material_lands_in_unassigned(client):
    row = _upload(client, "随手拍.JPG", text="")
    client.post("/ops/case/classify-credentials", json={"case_id": _CASE})
    view = _view(client)
    mine = _member_view(view, MEMBER_A)
    assert [a["id"] for a in mine["unassigned"]] == [row["id"]]
    assert view["stats"]["unassigned_total"] == 1


def test_assets_from_unselected_team_stay_visible(client):
    """没传 team_ids 时也要能看到材料，否则「已传但找不到」比缺失更误导。"""
    row = _upload(client, "营业执照.jpg")
    r = client.get("/ops/case/credentials", params={"case_id": _CASE})
    assert r.status_code == 200
    view = r.json()
    ids = {
        a["id"]
        for team in view["teams"]
        for m in team["members"]
        for node in m["credentials"]
        for a in node["assets"]
    }
    ids |= {
        a["id"]
        for team in view["teams"]
        for m in team["members"]
        for a in m["unassigned"]
    }
    assert row["id"] in ids


# ---------------------------------------------------------------------------
# 手动挂载（拖动）
# ---------------------------------------------------------------------------


def test_manual_assign_moves_asset_between_nodes(client):
    row = _upload(client, "随手拍.jpg")
    r = client.put(f"/ops/case/credential-asset/{row['id']}", json={"credential": APPLY_FORM})
    assert r.status_code == 200, r.text
    assert r.json()["asset"]["credential"] == APPLY_FORM
    assert r.json()["asset"]["classified_by"] == "manual"
    assert _node(_member_view(_view(client), MEMBER_A), APPLY_FORM)["satisfied"] is True

    # 空串 = 退回未归属
    r2 = client.put(f"/ops/case/credential-asset/{row['id']}", json={"credential": ""})
    assert r2.status_code == 200
    assert _node(_member_view(_view(client), MEMBER_A), APPLY_FORM)["satisfied"] is False


def test_assign_rejects_unknown_credential_node(client):
    row = _upload(client, "随手拍.jpg")
    r = client.put(f"/ops/case/credential-asset/{row['id']}", json={"credential": "不存在凭证"})
    assert r.status_code == 400
    assert "不存在凭证" in r.text


def test_asset_detail_returns_bytes_and_blocks(client, monkeypatch):
    from agent.image_processing import ocr as ocr_mod

    monkeypatch.setattr(
        ocr_mod,
        "recognize_image_sync",
        lambda _img: (
            "企业名称：某某贸易有限公司",
            [
                {
                    "text": "企业名称：某某贸易有限公司",
                    "bbox": [[10, 20], [310, 20], [310, 44], [10, 44]],
                    "confidence": 0.91,
                }
            ],
            0.91,
        ),
    )
    row = _upload(client, "营业执照.jpg")
    # 直接走识别端点（上传后的后台任务在 TestClient 下不保证跑完）
    r = client.post(f"/ops/case/credential-asset/{row['id']}/recognize")
    assert r.status_code == 200, r.text
    assert r.json()["ocr"]["blocks"][0]["text"].startswith("企业名称")

    detail = client.get(f"/ops/case/credential-asset/{row['id']}")
    assert detail.status_code == 200
    body = detail.json()
    assert body["mime"] == "image/jpeg"
    assert base64.b64decode(body["content_base64"])
    assert body["ocr"]["blocks"][0]["bbox"][1][0] == 310


def test_recognize_rejects_non_image(client):
    row = _upload(client, "说明.txt")
    r = client.post(f"/ops/case/credential-asset/{row['id']}/recognize")
    assert r.status_code == 400
    assert "不是图片" in r.text


def test_asset_delete_credential_image_only(client):
    store = _case_storage()
    img = store.add_image(
        case_id=_CASE,
        team_id=TEAM_ID,
        member_key=MEMBER_A,
        draft_id="",
        file_name="底图.jpg",
        file_path=str(Path(store.case_dir(_CASE)) / "底图.jpg"),
        credential=LICENSE,
        source="ocr-fill",
        values_json=json.dumps({"corp_name": "某某贸易有限公司"}),
        ocr_meta="{}",
    )
    r = client.delete(f"/ops/case/credential-asset/{img['id']}")
    assert r.status_code == 200
    assert r.json()["kind"] == "image"
    assert store.list_images(_CASE) == []


def _case_storage():
    from agent.ops import api as ops_api

    return ops_api._get_case_storage()


# ---------------------------------------------------------------------------
# OCR 自动填入 → 识别底图（不走审核）
# ---------------------------------------------------------------------------


def _make_draft(client: TestClient, member: str = MEMBER_A) -> dict:
    store = _case_storage()
    fields = [
        {"name": "corp_name", "label": "企业名称", "type": "text", "required": True},
        {"name": "credit_code", "label": "统一社会信用代码", "type": "text"},
    ]
    return store.add_draft(
        case_id=_CASE,
        team_id=TEAM_ID,
        member_key=member,
        title="客户材料清单",
        template_json=json.dumps(fields, ensure_ascii=False),
        values_json="{}",
    )


def test_ocr_fill_registers_credential_image_with_blocks(client, monkeypatch):
    from agent.image_processing import ocr as ocr_mod
    from agent.ops import api as ops_api

    ocr_mod.reset_ocr_engine()
    monkeypatch.setattr(
        ocr_mod,
        "recognize_image_sync",
        lambda _img: (
            "企业名称：某某贸易有限公司\n统一社会信用代码 91330100MA2ABCDX1",
            [
                {
                    "text": "企业名称：某某贸易有限公司",
                    "bbox": [[10, 20], [310, 20], [310, 44], [10, 44]],
                    "confidence": 0.91,
                }
            ],
            0.91,
        ),
    )

    async def _llm(_messages):
        return json.dumps({"values": {"corp_name": "某某贸易有限公司"}})

    monkeypatch.setattr(ops_api, "_make_summarize_llm", lambda: _llm)

    draft = _make_draft(client)
    r = client.post(
        f"/ops/case/drafts/{draft['id']}/ocr-fill",
        json={"file_name": "营业执照.jpg", "content_base64": _b64("imgbytes")},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    image = body["credential_image"]
    assert image is not None
    assert image["kind"] == "image"
    assert image["id"].startswith("CI-")
    assert image["credential"] == LICENSE
    assert image["ocr"]["blocks"] == 1

    # 底图不是材料：不污染专家卡，也不触发审核
    assert _case_storage().list_files(_CASE) == []

    view = _view(client)
    node = _node(_member_view(view, MEMBER_A), LICENSE)
    assert [a["id"] for a in node["assets"]] == [image["id"]]
    got = {f["name"]: f for f in node["field_values"]}
    assert got["corp_name"]["value"] == "某某贸易有限公司"
    assert got["corp_name"]["source"] == "ocr"
    assert got["corp_name"]["can_locate"] is True
    assert {"name": "credit_code", "label": "统一社会信用代码"} in node["fields_missing"]


def test_ocr_fill_repeat_reuses_same_credential_image(client, monkeypatch):
    """ocr-fill 重发幂等：桌面端带响应体中断重试（commands/ops.rs，最多 5 次），
    一次用户点击可能让本端点跑多遍。同表单同文件只能留一张底图 + 一份文件，
    否则凭证预览会被重复影像刷屏。"""
    from agent.image_processing import ocr as ocr_mod
    from agent.ops import api as ops_api

    ocr_mod.reset_ocr_engine()
    monkeypatch.setattr(
        ocr_mod,
        "recognize_image_sync",
        lambda _img: (
            "企业名称：某某贸易有限公司",
            [
                {
                    "text": "企业名称：某某贸易有限公司",
                    "bbox": [[10, 20], [310, 20], [310, 44], [10, 44]],
                    "confidence": 0.91,
                }
            ],
            0.91,
        ),
    )

    async def _llm(_messages):
        return json.dumps({"values": {"corp_name": "某某贸易有限公司"}})

    monkeypatch.setattr(ops_api, "_make_summarize_llm", lambda: _llm)

    draft = _make_draft(client)
    payload = {"file_name": "营业执照.jpg", "content_base64": _b64("imgbytes")}
    ids = []
    for _ in range(3):
        r = client.post(f"/ops/case/drafts/{draft['id']}/ocr-fill", json=payload)
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["filled"] == ["corp_name"]
        ids.append(body["credential_image"]["id"])

    # 三次重发拿到同一张底图 id，库里也只有一行
    assert len(set(ids)) == 1, ids
    images = _case_storage().list_images(_CASE)
    assert [i["id"] for i in images] == [ids[0]]
    # 落盘同样只有一份：不该堆出 _1 / _2 副本
    paths = {i["file_path"] for i in images}
    assert len(paths) == 1
    assert not any(p.endswith(("_1.jpg", "_2.jpg")) for p in paths)


def test_ocr_fill_without_preset_still_registers_unassigned(client, monkeypatch):
    from agent.image_processing import ocr as ocr_mod
    from agent.ops import api as ops_api

    monkeypatch.setattr(
        ocr_mod,
        "recognize_image_sync",
        lambda _img: ("企业名称：某某公司", [{"text": "企业名称：某某公司", "bbox": [[0, 0], [9, 0], [9, 9], [0, 9]], "confidence": 0.8}], 0.8),
    )

    async def _llm(_messages):
        return json.dumps({"values": {"corp_name": "某某公司"}})

    monkeypatch.setattr(ops_api, "_make_summarize_llm", lambda: _llm)
    draft = _make_draft(client, member="未知专家")
    r = client.post(
        f"/ops/case/drafts/{draft['id']}/ocr-fill",
        json={"file_name": "照片1.jpg", "content_base64": _b64("x")},
    )
    assert r.status_code == 200, r.text
    image = r.json()["credential_image"]
    assert image["credential"] == ""
    # 未关联到任何专家（团里没有该成员）→ 仍然出现在未归属里
    view = _view(client)
    leftover = [a for team in view["teams"] for m in team["members"] for a in m["unassigned"]]
    assert [a["id"] for a in leftover] == [image["id"]]


def test_ocr_fill_failure_does_not_break_values(client, monkeypatch):
    """底图入库失败（这里模拟落盘异常）→ 填表结果照常返回，只丢留档提示。"""
    from agent.image_processing import ocr as ocr_mod
    from agent.ops import api as ops_api

    monkeypatch.setattr(
        ocr_mod,
        "recognize_image_sync",
        lambda _img: ("企业名称：某某公司", [], 0.8),
    )

    async def _llm(_messages):
        return json.dumps({"values": {"corp_name": "某某公司"}})

    monkeypatch.setattr(ops_api, "_make_summarize_llm", lambda: _llm)
    store = _case_storage()
    monkeypatch.setattr(store, "save_named_upload", lambda *a, **k: (_ for _ in ()).throw(OSError("disk")))
    draft = _make_draft(client)
    r = client.post(
        f"/ops/case/drafts/{draft['id']}/ocr-fill",
        json={"file_name": "营业执照.jpg", "content_base64": _b64("x")},
    )
    assert r.status_code == 200, r.text
    assert r.json()["values"]["corp_name"] == "某某公司"
    assert r.json()["credential_image"] is None


# ---------------------------------------------------------------------------
# LLM 归类兜底：只接受候选节点名（防臆造）
# ---------------------------------------------------------------------------


def test_llm_classification_whitelists_node_names(client, monkeypatch):
    from agent.ops import api as ops_api

    row = _upload(client, "IMG_0012.jpg", text="")
    other = _upload(client, "IMG_0013.jpg", member=MEMBER_B, text="")

    async def _llm(_messages):
        return json.dumps(
            {
                "assignments": [
                    {"id": row["id"], "credential": APPLY_FORM},
                    {"id": other["id"], "credential": "客户签收单"},  # 候选里没有 → 丢弃
                ]
            }
        )

    monkeypatch.setattr(ops_api, "_make_summarize_llm", lambda: _llm)
    r = client.post("/ops/case/classify-credentials", json={"case_id": _CASE})
    assert r.status_code == 200, r.text
    assert r.json()["classify_stats"]["ai"] == 1
    view = _view(client)
    assert _node(_member_view(view, MEMBER_A), APPLY_FORM)["satisfied"] is True
    assert _member_view(view, MEMBER_B)["unassigned"][0]["credential"] == ""


def test_classification_without_preset_nodes_skips_llm(client, monkeypatch):
    """该专家没预设凭证节点 → 完全不花 LLM（也不报错）。"""
    from agent.ops import api as ops_api

    calls: list[str] = []

    async def _llm(messages):
        calls.append("hit")
        return "{}"

    monkeypatch.setattr(ops_api, "_make_summarize_llm", lambda: _llm)
    _upload(client, "IMG_9.jpg", member="团外专家", text="")
    r = client.post("/ops/case/classify-credentials", json={"case_id": _CASE})
    assert r.status_code == 200
    assert calls == []
    assert r.json()["classify_stats"]["ai"] == 0


# ---------------------------------------------------------------------------
# 台账详情：凭证摘要
# ---------------------------------------------------------------------------


def test_task_detail_carries_credential_summary(client):
    _upload(client, "营业执照.jpg")
    client.post("/ops/case/classify-credentials", json={"case_id": _CASE})
    tasks = client.get("/ops/tasks", params={"case_id": _CASE})
    rows = tasks.json()["tasks"]
    assert rows, tasks.text
    detail = client.get(f"/ops/tasks/{rows[0]['id']}")
    assert detail.status_code == 200
    creds = detail.json()["credentials"]
    assert creds["required_total"] == 3
    assert creds["required_ok"] == 1
    assert creds["has_preset"] is True


# ---------------------------------------------------------------------------
# 影像元数据与资产 id 口径
# ---------------------------------------------------------------------------


def test_asset_kind_split_by_id_prefix(client):
    from agent.ops import api as ops_api

    assert ops_api._asset_kind("CF-20261109-ab") == ops_api.ASSET_FILE
    assert ops_api._asset_kind("CI-20261109-ab") == ops_api.ASSET_IMAGE
    assert ops_api._asset_kind("DR-20261109-ab") == ops_api.ASSET_FILE


def test_credential_terms_drop_generic_tokens():
    from agent.ops import api as ops_api

    node = {"name": "居民身份证（人像面）", "keywords": ["身份证"]}
    terms = ops_api._credential_terms(node)
    assert "身份证" in terms
    assert "人像面" in terms
    # 泛词单独出现不足以判定是哪张凭证
    assert "影像" not in ops_api._credential_terms({"name": "客户身份影像", "keywords": []})


def test_match_credential_prefers_file_name_over_text():
    from agent.ops import api as ops_api

    nodes = [
        {"name": "营业执照", "keywords": ["执照"]},
        {"name": "授权委托书", "keywords": ["授权"]},
    ]
    assert ops_api._match_credential(nodes, "授权委托书.pdf", "营业执照 某某公司") == "授权委托书"
    assert ops_api._match_credential(nodes, "IMG_1.jpg", "上面写着授权委托书") == "授权委托书"
    assert ops_api._match_credential(nodes, "IMG_1.jpg", "") == ""


def test_ocr_image_meta_degrades_without_engine(tmp_path, monkeypatch):
    from agent.image_processing import ocr as ocr_mod

    ocr_mod.reset_ocr_engine()
    monkeypatch.setattr(ocr_mod, "_get_rapidocr", lambda: None, raising=True)
    bad = tmp_path / "not-really.png"
    bad.write_bytes(b"not an image")
    meta = ocr_mod.ocr_image_meta(str(bad))
    assert meta["engine"] == "none"
    assert meta["blocks"] == []
    assert meta["error"]


def test_ocr_image_meta_serializes_boxes(tmp_path, monkeypatch):
    """numpy 坐标必须能 json 落库（sqlite 存 JSON 文本）。"""
    import numpy as np

    from agent.image_processing import ocr as ocr_mod

    monkeypatch.setattr(
        ocr_mod,
        "recognize_image_sync",
        lambda _p: (
            "某某公司",
            [{"text": "某某公司", "bbox": np.array([[1, 2], [30, 2], [30, 9], [1, 9]], dtype="float32"), "confidence": np.float32(0.87)}],
            np.float32(0.87),
        ),
    )
    img = tmp_path / "a.png"
    img.write_bytes(b"x")
    meta = ocr_mod.ocr_image_meta(str(img))
    assert meta["engine"] == "rapidocr"
    assert meta["confidence"] == pytest.approx(0.87, abs=1e-3)
    json.dumps(meta)  # 不抛 = 可落库
    assert meta["blocks"][0]["bbox"] == [[1, 2], [30, 2], [30, 9], [1, 9]]
