"""test_reqflow_export.py —— 批次需求文档导出（MD / DOCX）测试。"""

from __future__ import annotations

from agent.reqflow.exporter import export_docx, export_markdown
from agent.reqflow.models import ReqBatch, ReqCard


def _batch_and_cards():
    batch = ReqBatch(id="BAT-1", name="2026-08 批次", project_name="proj")
    cards = [
        ReqCard(
            id="REQ-1",
            batch_id="BAT-1",
            project_name="proj",
            system_name="订单系统",
            title="部分取消",
            status="done",
            priority="P1",
            business_value="减少整单取消",
            change_points="创建订单支持按行取消",
            feasibility="feasible",
            feasibility_notes="依赖库存接口幂等",
            impact="影响退款流程",
            external_systems=["支付网关"],
            feature_ids=["f1"],
            version=3,
        ),
        ReqCard(
            id="REQ-2",
            batch_id="BAT-1",
            project_name="proj",
            system_name="订单系统",
            title="草稿需求",
            status="draft",
            feature_ids=["fx"],
        ),
    ]
    return batch, cards


def test_export_markdown_structure():
    batch, cards = _batch_and_cards()
    md = export_markdown(batch, cards, feature_names={"f1": "创建订单"})
    assert "# 需求文档 · 2026-08 批次" in md
    assert "REQ-1" in md and "部分取消" in md
    assert "已完成" in md  # 状态中文
    assert "创建订单" in md  # 功能点名称替换 id
    assert "支付网关" in md
    assert "批次概览" in md and "2" in md  # 概览含总数


def test_export_markdown_missing_feature_label():
    batch, cards = _batch_and_cards()
    md = export_markdown(batch, cards, feature_names={})
    # 功能点不存在时显示「功能点已不存在」
    assert "功能点已不存在" in md


def test_export_docx_bytes():
    batch, cards = _batch_and_cards()
    data = export_docx(batch, cards, feature_names={"f1": "创建订单"})
    assert isinstance(data, bytes)
    assert data[:2] == b"PK"  # docx = zip
