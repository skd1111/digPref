# 财税审核测试素材索引（Fiscal-Tax Audit Fixtures）

> 用途：为 EAIDE Phase 5「审核专家模式」提供财税领域测试数据；可作为 `audit_expert.api` / `test_audit_expert_v*.py` 的输入 fixture，或人工走查的剧本。
>
> **版权声明**：本目录文件基于公开政府法规、新闻案例与判例检索整理，仅为摘要与测试场景构造；引用时仍以官方原文为准。本目录不复述全部法规原文，仅列关键条款编号、要点与可机审的判定要素。

---

## 目录结构

```
fiscal-tax/
├── README.md                                  ← 本文件（索引 + 审核评分表）
├── regulations/
│   ├── 税收征管法-要点.md                     ← 1992/1995/2001/2013/2015 修正版
│   ├── 发票管理办法-要点.md                   ← 2010 修订 + 2019 修正（国务院令 710）
│   ├── 增值税法与暂行条例-要点.md             ← 暂行条例 2017 + 增值税法 2024（2026.1.1）
│   ├── 企业所得税法-要点.md                   ← 2007 + 2017/2018 修订 + 实施条例 2025.1.20
│   ├── 个人所得税法-要点.md                   ← 2018 修订
│   └── 税收征管法实施细则-要点.md             ← 国务院令第 362 号（2002 颁布 + 多次修订）
├── accounting-standards/
│   ├── 企业会计准则-基本准则.md               ← 财政部 2014 修订（11 章 50 条）
│   └── 审计准则核心要点.md                   ← 中国注册会计师审计准则 1211 号风险评估等
├── internal-control/
│   └── 企业内部控制基本规范.md                ← 财政部 2008 + 18 项应用指引清单
├── cases/
│   ├── 2024年典型曝光案例.md                  ← 加油站 2722 户、主播 169 名 8.99 亿等
│   ├── 虚开发票案例与司法解释.md              ← 刑法 205 条 + 最高法 2025.11 典型案例
│   └── 偷税案例与处罚标准.md                  ← 征管法第 63/64/65/66 条对应
└── test-scenarios/
    ├── 合规业务-正向审核.md                   ← 5 个干净剧本，期望走绿色快通道
    ├── 违规业务-高风险审核.md                 ← 5 个高危剧本，期望拦截 + 升级审批
    └── 灰色业务-人工复核.md                   ← 5 个边界剧本，期望规则不全覆盖 → 人工
```

---

## 审核评分维度（5 维）

EAIDE 现有 audit_expert V0/V1 5 项合规规则可扩展，下表是建议的「财税版」6 维评分模型：

| 维度 | 满分 | 红线 | 触发条件举例 |
|---|---|---|---|
| **法规符合性** | 30 | 单项 < 18 即拒 | 违反征管法 / 发票办法 / 会计法 等强行性规定 |
| **金额风险** | 25 | > 50 万 / 单笔 | 单笔金额 ≥ 5 万、累计 ≥ 50 万、关联交易 ≥ 100 万 |
| **凭证完整性** | 15 | 任一缺失即拒 | 发票/合同/付款单/审批单/验收单 缺失任一 |
| **审批层级** | 15 | 未达层级即拒 | 总经理 / 董事会 / 股东会 审批缺失 |
| **时效合规** | 10 | 超期即拒 | 跨月/跨年/申报期外/汇算清缴期外 |
| **可追溯性** | 5 | 无法溯源即拒 | 操作人/时间/IP/复核人/SHA-256 缺失任一 |

总分计算：`score = Σ(维度分) - Σ(违规扣分)`；决策门槛：

```
score >= 90  → ✅ 合规，自动通过（仍需 audit log 留痕）
score 70-89  → ⚠️  需补充材料后人工复核
score 50-69  → 🔶 升级到财务主管审批
score 30-49  → 🔴 升级到 CFO/总经理审批 + 风险提示
score < 30   → 🚫 必拒 + 触发 Phase 5 V0 SHA-256 链式审计 + 法务介入
```

---

## 与 EAIDE 审核专家模块的对接

### 数据模型对应

| EAIDE audit_expert 字段 | 财税测试字段填充示例 |
|---|---|
| `title` | "支付 XX 公司咨询费 ¥120,000.00" |
| `description` | "2024-Q3 业务咨询费报销，附 6% 增值税专票 + 合同" |
| `risk_level` | `low` / `medium` / `high` / `critical` |
| `evidence` | `[{type:invoice, hash:..., url:...}, {type:contract, hash:...}]` |
| `compliance` | `[{rule:CODE-205, level:violation}, {rule:MISSING_EVIDENCE, level:warning}]` |
| `actor_id` | 报销人 ID（需与 IAM Phase 10 用户表对接） |
| `totp_code` | 高风险操作 MFA（V1 接 RFC 6238） |
| `rsa_signature` | V1 RSA-2048-PSS-SHA256 签名（OS Keyring 私钥） |

### REST 端点调用示例（直接拷就能用）

```bash
# 1. 创建审核任务
curl -X POST http://127.0.0.1:8765/audit/tasks \
  -H "Content-Type: application/json" \
  -d @test-scenarios/合规业务-正向审核.md | head -100

# 2. 查询合规检查
curl http://127.0.0.1:8765/audit/compliance?task_id=<id>

# 3. 双人复核决定（V1 dual-second）
curl -X POST http://127.0.0.1:8765/audit/dual-second \
  -H "Content-Type: application/json" \
  -d '{"task_id":"...", "actor_id":"cfo", "decision":"approve", "totp_code":"123456", "rsa_signature":"..."}'

# 4. 校验签名链
curl http://127.0.0.1:8765/audit/verify
```

### Python SDK 一行测试

```python
from agent.audit_expert import (
    AuditExpertStorage, ComplianceChecker, ApprovalStatus,
    create_task, record_decision, verify_chain, get_stats,
)

task = create_task(
    title="测试：高风险付款报销",
    risk_level="critical",
    actor_id="user-007",
    payload={
        "金额": 1_200_000.00,
        "类别": "工程款",
        "对方单位": "测试对手方",
        "凭证": ["专票", "合同", "验收单"],
        "审批层级": ["部门经理", "财务总监", "CFO"],
    },
)
print(get_stats())  # 应该看到 tasks + 1
```

---

## 数据来源

| 来源类型 | 链接 |
|---|---|
| 中国政府网·法规 | https://www.gov.cn/ |
| 国家税务总局 | https://www.chinatax.gov.cn/ |
| 财政部会计司 | http://kjs.mof.gov.cn/ |
| 中国证监会 | https://www.csrc.gov.cn/ |
| 全国人大·法律法规库 | https://flk.npc.gov.cn/ |
| 最高人民法院典型案例 | （每月发布） |
| 央视网 / 新华网 | （重大稽查案件通报） |

**重要提示**：财税法规更新频繁（2024 增值税法颁布、2025 企税法实施条例修订、2026 多项新法生效）。本目录快照时间 **2026-08-18**，使用前请核对最新版本。
