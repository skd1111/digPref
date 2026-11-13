"""seed_demo_data.py —— 往运行库灌一批「真实口径」的示例数据（2026-11-09）。

用途：业务汇总（business_tasks）与需求工作台（reqcards.db）空着不好体验，
这里按后端真实存储层写入一批可交互的记录 —— 不是前端 mock，而是真实落库，
详情/筛选/状态流转/版本历史都能跑通。

写什么：
  1. ops.db / business_tasks：5 条业务台账，覆盖四态（正在办理 / 挂起 /
     人工确认 / 结束），绑定已存在的 Case（demo__fin_flow 等），
     人工确认那条的 pending_items 引用真实被打回的材料；
  2. ops.db / business_records：给「结束」那条补一份业务小结，明细可展开；
  3. reqcards.db：1 个批次 + 6 张需求卡片（draft→…→done 逐级流转，
     因此版本快照也是真实产生的）。

幂等：业务按 case_id 判断（已存在则跳过）；需求卡片按批次名判断。

用法：
    python infra/scripts/seed_demo_data.py                # 自动探测数据根
    python infra/scripts/seed_demo_data.py --data-root "C:\\Users\\me\\AppData\\Local\\Enterprise AI IDE"
    python infra/scripts/seed_demo_data.py --dry-run      # 只打印将要写入什么

数据根优先级：--data-root > EAIDE_DATA_ROOT > %LOCALAPPDATA%\\Enterprise AI IDE
（Tauri 打包版实际根）> %APPDATA%\\eaide（独立运行兜底）。
"""

from __future__ import annotations

import argparse
import json
import os
import secrets
import sqlite3
import sys
import time
from pathlib import Path
from typing import Any

PROJECT = "demo"


def default_data_root() -> Path:
    env = os.environ.get("EAIDE_DATA_ROOT")
    if env:
        return Path(env).expanduser()
    local = os.environ.get("LOCALAPPDATA")
    if local:
        tauri = Path(local) / "Enterprise AI IDE"
        if tauri.exists():
            return tauri
    appdata = os.environ.get("APPDATA")
    if appdata:
        return Path(appdata) / "eaide"
    return Path.home() / ".eaide"


def ensure_agent_importable() -> None:
    src = Path(__file__).resolve().parents[2] / "services" / "agent" / "src"
    if str(src) not in sys.path:
        sys.path.insert(0, str(src))


def days_ago(days: float) -> int:
    return int(time.time() - days * 86400)


# ---------------------------------------------------------------------------
# 业务汇总（ops.db）
# ---------------------------------------------------------------------------

# 与 opsModules.ts 的真实大类/小类 id 对齐（筛选行才能命中）
BUSINESS_ROWS: list[dict[str, Any]] = [
    {
        "case_id": "demo__fin_flow",
        "category": "credit_due",
        "category_label": "信贷尽调",
        "feature_id": "fin_flow",
        "feature_label": "财务与流水分析",
        "title": "财务与流水分析 · 宏远贸易",
        "operator": "张敏（对公客户经理）",
        "status": "in_progress",
        "status_note": "",
        "days": 0.2,
    },
    {
        "case_id": "demo__cust_profile",
        "category": "aml",
        "category_label": "反洗钱辅助分析",
        "feature_id": "cust_profile",
        "feature_label": "客户画像摘要",
        "title": "客户画像摘要 · 恒信供应链",
        "operator": "李强（反洗钱专岗）",
        "status": "in_progress",
        "status_note": "",
        "days": 1.4,
    },
    {
        "case_id": "demo__due_report",
        "category": "corp_account",
        "category_label": "对公账户管理",
        "feature_id": "annual_review",
        "feature_label": "账户年检/持续尽调",
        "title": "对公账户年检 · 华泰实业",
        "operator": "周雯（账户管理岗）",
        "status": "manual_review",
        "status_note": "材料被打回：营业执照已过有效期，需补件后重新提交",
        "days": 2.3,
        # 引用真实存在的被打回材料（ops.db case_files）
        "pending_items": [
            {
                "file_id": "CF-20261109142850-f58f",
                "file_name": "对公账户业务审查表.md",
                "member_key": "账户业务主管",
                "kind": "rejected",
                "note": "营业执照已过有效期，信用代码位数不清晰，请补件后重新提交",
            }
        ],
    },
    {
        "case_id": "demo__credit_materials",
        "category": "credit_due",
        "category_label": "信贷尽调",
        "feature_id": "credit_materials",
        "feature_label": "客户资料收集",
        "title": "客户资料收集 · 瑞丰机械",
        "operator": "王蕾（信贷客户经理）",
        "status": "suspended",
        "status_note": "客户补充财报未到，先办理其他业务，明天上午恢复",
        "days": 0.9,
    },
    {
        "case_id": "demo__image_classify",
        "category": "doc_precheck",
        "category_label": "资料预审中心",
        "feature_id": "image_classify",
        "feature_label": "影像自动分类",
        "title": "影像自动分类 · 集中作业中心 11 月批次",
        "operator": "陈静（预审主管）",
        "status": "closed",
        "status_note": "全部影像件分类完成并归档",
        "days": 4.1,
    },
]

CLOSED_RECORD = {
    "business_type": "doc_precheck",
    "title": "影像自动分类 · 集中作业中心 11 月批次",
    "summary": (
        "本批次共预审 12 份影像件，自动分类准确 11 份（91.7%），"
        "1 份开户许可证因复印件倾斜由人工改判；缺失材料 0 份，风险点 1 项（印章模糊）。"
    ),
    "materials_checked": ["营业执照", "开户许可证", "法人身份证", "授权委托书"],
    "materials_missing": [],
    "risk_points": ["开户许可证复印件印章模糊，建议重新扫描"],
    "created_by": "资料预审专家团",
}


def seed_business(db_path: str, *, dry_run: bool) -> list[str]:
    """写入业务台账 + 一条完成小结；返回写入/跳过的说明。"""
    from agent.ops.tasks import TaskStorage

    storage = TaskStorage(db_path)
    ops_conn = sqlite3.connect(db_path, timeout=10)
    log: list[str] = []
    try:
        for row in BUSINESS_ROWS:
            case_id = row["case_id"]
            existing = storage.get_by_case(case_id)
            if existing is not None:
                log.append(f"跳过（已存在）：{case_id} → {existing.title}")
                continue

            if dry_run:
                log.append(f"[dry-run] 将写入：{case_id} / {row['title']} / {row['status']}")
                continue

            task = storage.ensure(
                case_id=case_id,
                project_name=PROJECT,
                feature_id=row["feature_id"],
                feature_label=row["feature_label"],
                category=row["category"],
                category_label=row["category_label"],
                title=row["title"],
                operator=row["operator"],
            )
            storage.update(
                task.id,
                status=row["status"],
                status_note=row["status_note"],
                pending_items=row.get("pending_items"),
            )
            if row["status"] == "suspended":
                storage.set_suspended(task.id, note=row["status_note"])
            elif row["status"] == "closed":
                record_id = _insert_record(ops_conn, case_id=case_id)
                storage.set_closed(task.id, note=row["status_note"], record_id=record_id)

            # 把时间打散到近几天（ensure 用的是当前时间，纯列表看着像同一秒建的）
            created = days_ago(row["days"])
            ops_conn.execute(
                "UPDATE business_tasks SET created_at = ?, updated_at = ? WHERE id = ?",
                (created, created + 1800, task.id),
            )
            ops_conn.commit()
            log.append(f"写入：{case_id} / {row['title']} / {row['status']}")
    finally:
        ops_conn.close()
    return log


def _insert_record(conn: sqlite3.Connection, *, case_id: str) -> str:
    ts = days_ago(4.0)
    record_id = f"OPR-{time.strftime('%Y%m%d%H%M%S', time.localtime(ts))}-{secrets.token_hex(2)}"
    conn.execute(
        """
        INSERT INTO business_records(
            id, project_name, feature_id, business_type, title, summary,
            materials_checked, materials_missing, risk_points, result,
            skill_id, session_id, source, created_by, created_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'done', '', ?, 'ai', ?, ?, ?)
        """,
        (
            record_id,
            PROJECT,
            "image_classify",
            CLOSED_RECORD["business_type"],
            CLOSED_RECORD["title"],
            CLOSED_RECORD["summary"],
            json.dumps(CLOSED_RECORD["materials_checked"], ensure_ascii=False),
            json.dumps(CLOSED_RECORD["materials_missing"], ensure_ascii=False),
            json.dumps(CLOSED_RECORD["risk_points"], ensure_ascii=False),
            "",
            CLOSED_RECORD["created_by"],
            ts,
            ts,
        ),
    )
    conn.commit()
    return record_id


# ---------------------------------------------------------------------------
# 需求卡片（reqcards.db）
# ---------------------------------------------------------------------------

BATCH_NAME = "2026-11 对公业务数字化改造"

# 每张卡：字段 + 要经过的状态路径（逐级流转会真实产生版本快照）
CARD_ROWS: list[dict[str, Any]] = [
    {
        "system_name": "核心账务系统",
        "title": "对公账户开立流程线上化",
        "priority": "P0",
        "feature_ids": ["corp_open"],
        "business_value": "客户经理开户办理时长由 3 个工作日缩短到 4 小时，柜面手工录入减少 70%。",
        "change_points": (
            "新增线上预审与材料影像上传；OCR 自动核验营业执照/法人身份证；"
            "开户任务按网点分派并支持退回补件；开户结果回写人行账户管理系统。"
        ),
        "feasibility": "feasible",
        "feasibility_notes": "现有影像件平台已具备 OCR 能力，改造集中在流程编排，预计 6 人周。",
        "impact": "开户模块、影像件平台、任务分派中心",
        "external_systems": ["人行账户管理系统", "企业信用信息公示系统"],
        "status_path": ["pending_approval", "approved", "developing", "done"],
        "changed_by": "科技部-李工",
        "days": 6.0,
    },
    {
        "system_name": "客户信息系统",
        "title": "企业客户风险画像按日自动更新",
        "priority": "P1",
        "feature_ids": ["cust_profile"],
        "business_value": "反洗钱专岗手工拉取流水的时间从每天 2 小时降到零，画像口径全行统一。",
        "change_points": "接入日终流水与工商变更数据；画像分从月度改为 T+1；异常变动自动推送预警任务。",
        "feasibility": "feasible",
        "feasibility_notes": "依赖数据中台日终批量，接口已联调。",
        "impact": "客户画像、反洗钱预警任务",
        "external_systems": ["工商登记数据服务"],
        "status_path": ["pending_approval", "approved", "developing"],
        "changed_by": "业务部-赵经理",
        "days": 4.5,
    },
    {
        "system_name": "信贷管理系统",
        "title": "贷前调查报告自动生成",
        "priority": "P0",
        "feature_ids": ["due_report"],
        "business_value": "尽调报告初稿撰写由 2 天压缩到 1 小时，客户经理只做事实核对。",
        "change_points": "汇总财务与流水分析结果、征信摘要、现场尽调记录；按模板输出 Word；关键结论标注证据出处。",
        "feasibility": "risky",
        "feasibility_notes": "报告模板各分行不统一，需先收敛模板；模型输出需人工复核。",
        "impact": "贷前尽调、报告模板库",
        "external_systems": ["征信查询平台"],
        "status_path": ["pending_approval", "approved"],
        "changed_by": "风险部-孙总",
        "days": 3.0,
    },
    {
        "system_name": "反洗钱监测系统",
        "title": "可疑交易线索自动分派到属地支行",
        "priority": "P1",
        "feature_ids": ["suspicious_draft"],
        "business_value": "线索分派从人工邮件改为系统派单，平均响应时间由 1.5 天降到 4 小时。",
        "change_points": "按客户归属机构自动派单；超时未核实自动升级；核实记录与线索一一关联归档。",
        "feasibility": "feasible",
        "feasibility_notes": "与现有预警任务列表共用数据模型，工作量约 3 人周。",
        "impact": "反洗钱预警任务、核实记录",
        "external_systems": [],
        "status_path": ["pending_approval"],
        "changed_by": "合规部-郑主管",
        "days": 2.0,
    },
    {
        "system_name": "移动银行",
        "title": "对公客户移动端预约取号",
        "priority": "P2",
        "feature_ids": ["corp_change"],
        "business_value": "对公客户到店平均等待时间缩短 15 分钟，网点高峰分流。",
        "change_points": "移动端新增预约入口；按业务类型（变更/销户/年检）分配网点时段；到店扫码取号。",
        "feasibility": "feasible",
        "feasibility_notes": "移动端已有预约框架，本次只增加对公业务类型。",
        "impact": "移动银行预约模块、网点叫号系统",
        "external_systems": ["网点叫号系统"],
        "status_path": [],
        "changed_by": "渠道部-钱经理",
        "days": 1.2,
    },
    {
        "system_name": "客户信息系统",
        "title": "客户信息变更双人复核",
        "priority": "P2",
        "feature_ids": ["corp_change"],
        "business_value": "降低客户关键信息被单人篡改的操作风险。",
        "change_points": "变更申请提交后强制第二人复核；复核不通过退回并留痕；变更历史全量可追溯。",
        "feasibility": "risky",
        "feasibility_notes": "现有网点人员紧张，双人复核可能拉长办理时长；建议仅关键字段强制。",
        "impact": "客户信息变更、授权复核",
        "external_systems": [],
        "status_path": ["pending_approval", "rejected"],
        "changed_by": "运营部-何主管",
        "days": 0.6,
    },
]


def seed_requirement_cards(db_path: str, *, dry_run: bool) -> list[str]:
    from agent.reqflow.storage import ReqCardStorage

    storage = ReqCardStorage(db_path)
    log: list[str] = []

    if any(b.name == BATCH_NAME for b in storage.list_batches(PROJECT)):
        log.append(f"跳过（批次已存在）：{BATCH_NAME}")
        return log

    if dry_run:
        log.append(f"[dry-run] 将写入批次：{BATCH_NAME}（{len(CARD_ROWS)} 张卡片）")
        return log

    batch = storage.create_batch(project_name=PROJECT, name=BATCH_NAME, created_by="业务部-赵经理")
    for row in CARD_ROWS:
        card = storage.create_card(
            batch_id=batch.id,
            project_name=PROJECT,
            system_name=row["system_name"],
            title=row["title"],
            feature_ids=row["feature_ids"],
            business_value=row["business_value"],
            change_points=row["change_points"],
            feasibility=row["feasibility"],
            feasibility_notes=row["feasibility_notes"],
            impact=row["impact"],
            external_systems=row["external_systems"],
            priority=row["priority"],
            conversation_summary=(
                f"与{row['system_name']}负责人对齐：确认改造范围与可行性，"
                f"优先级 {row['priority']}，落库为需求卡片待审批。"
            ),
            created_by="业务部-赵经理",
        )
        for status in row["status_path"]:
            storage.update_card(card.id, changed_by=row["changed_by"], status=status)
        final = storage.get_card(card.id)
        log.append(
            f"写入：{card.id} {row['title']}（{final.status if final else '?'}"
            f"，v{final.version if final else '?'}）"
        )
    return log


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main() -> int:
    global PROJECT
    parser = argparse.ArgumentParser(description="往运行库灌示例数据（业务汇总 + 需求卡片）")
    parser.add_argument("--data-root", default="", help="数据根目录（默认自动探测）")
    parser.add_argument(
        "--project",
        default=PROJECT,
        help="工程名（需求工作台按它查批次；默认 demo，导入过程目录名不同时请指定）",
    )
    parser.add_argument("--dry-run", action="store_true", help="只打印，不写入")
    args = parser.parse_args()

    PROJECT = args.project

    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

    root = Path(args.data_root).expanduser() if args.data_root else default_data_root()
    ops_db = root / "ops.db"
    req_db = root / "reqcards.db"
    print(f"数据根：{root}（工程：{PROJECT}）")
    if not ops_db.exists():
        print(f"[错误] 找不到 {ops_db}（先启动一次 Agent 让运行库初始化）")
        return 1

    ensure_agent_importable()

    print("\n== 业务汇总（business_tasks）")
    for line in seed_business(str(ops_db), dry_run=args.dry_run):
        print("  -", line)

    print("\n== 需求卡片（reqcards.db）")
    for line in seed_requirement_cards(str(req_db), dry_run=args.dry_run):
        print("  -", line)

    print("\n完成。回到应用：业务汇总 / 需求工作台 切走再切回即可看到。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
