/**
 * AuditDashboard —— 审核专家主布局（Phase 5 MVP，前端 + mock 数据）。
 *
 * 布局（按 ROADMAP §6.8.2 三栏）：
 *   ┌─ 左 240px ─┬─ 中 (flex-1) ────────┬─ 右 320px ─┐
 *   │  审批工作台 │  ApprovalCard 顶部   │ Evidence   │
 *   │  待审/历史  │  DiffViewer (flex-1) │ Timeline   │
 *   │  风险告警  │  ApprovalActions 底部 │ Compliance │
 *   │            │                      │ 资金影响   │
 *   └────────────┴──────────────────────┴────────────┘
 */
import { useEffect, useState } from "react";
import { useAuditStore } from "@/store/auditStore";
import { useCollabStore } from "@/store/collabStore";
import { DocReviewDashboard } from "./DocReviewDashboard";
import { ApprovalQueue } from "@/components/audit/ApprovalQueue";
import { AuditApprovalCard } from "@/components/audit/AuditApprovalCard";
import { DiffViewer } from "@/components/audit/DiffViewer";
import { ApprovalActions } from "@/components/audit/ApprovalActions";
import { EvidenceTimeline } from "@/components/audit/EvidenceTimeline";
import { CompliancePanel } from "@/components/audit/CompliancePanel";
import { CollabDrawer } from "@/components/collab/CollabDrawer";

function AuditApprovalDashboard(): JSX.Element {
  const selectedTaskId = useAuditStore((s) => s.selectedTaskId);
  const task = useAuditStore((s) =>
    s.tasks.find((t) => t.id === selectedTaskId),
  );
  const collabContexts = useCollabStore((s) => s.contexts);
  const openDrawer = useCollabStore((s) => s.openDrawer);
  const closeDrawer = useCollabStore((s) => s.closeDrawer);
  const drawerContextId = useCollabStore((s) => s.drawerContextId);

  // Phase 9 联动：选中审批任务时，查找是否有关联的 `approval_ticket` 锚点
  // 找到则自动打开 CollabDrawer；切换到没有关联的任务时关闭
  useEffect(() => {
    if (!task) {
      if (drawerContextId) closeDrawer();
      return;
    }
    const linkedCtx = collabContexts.find(
      (c) =>
        c.anchor_type === "approval_ticket" && c.related_ticket_id === task.id,
    );
    if (linkedCtx && linkedCtx.id !== drawerContextId) {
      openDrawer(linkedCtx.id);
    } else if (!linkedCtx && drawerContextId) {
      closeDrawer();
    }
  }, [task, collabContexts, openDrawer, closeDrawer, drawerContextId]);

  return (
    <div
      className="audit-dashboard flex h-full"
      style={{ backgroundColor: "#ffffff" }}
    >
      {/* 左栏：审批工作台 240px */}
      <div
        className="flex-shrink-0 border-r"
        style={{ width: 280, borderColor: "#d4d4d4" }}
      >
        <ApprovalQueue />
      </div>

      {/* 中栏：审批详情 */}
      <div className="flex flex-1 flex-col overflow-hidden">
        {task ? (
          <>
            <AuditApprovalCard taskId={task.id} />
            <div className="flex-1 overflow-hidden" style={{ minHeight: 200 }}>
              <DiffViewer
                before={task.diff.before}
                after={task.diff.after}
                language={task.diff.language}
                summary={task.diff.summary}
              />
            </div>
            <ApprovalActions task={task} />
          </>
        ) : (
          <div
            className="flex flex-1 items-center justify-center text-ui"
            style={{ color: "#616161" }}
          >
            ← 请从左侧选择审批任务
          </div>
        )}
      </div>

      {/* 右栏：审计与合规 320px */}
      <div
        className="flex flex-shrink-0 flex-col border-l"
        style={{ width: 320, borderColor: "#d4d4d4" }}
      >
        {task ? (
          <>
            <div className="flex-1 overflow-hidden" style={{ minHeight: 200 }}>
              <EvidenceTimeline taskId={task.id} />
            </div>
            <CompliancePanel taskId={task.id} />
          </>
        ) : (
          <div
            className="flex flex-1 items-center justify-center p-4 text-center text-2xs"
            style={{ color: "#616161" }}
          >
            选择任务后查看 Evidence Chain + 合规检查
          </div>
        )}
      </div>

      {/* Phase 9 协作抽屉：有 approval_ticket 锚点时自动打开 */}
      <CollabDrawer fallbackCreate />
    </div>
  );
}

export function AuditDashboard(): JSX.Element {
  const [docTab, setDocTab] = useState(false);
  return (
    <div className="flex h-full flex-col">
      <div
        className="flex flex-shrink-0 border-b"
        style={{ borderColor: "#d4d4d4", backgroundColor: "#f3f3f3" }}
      >
        <TabButton
          active={!docTab}
          onClick={() => setDocTab(false)}
          label="审批工作台"
        />
        <TabButton
          active={docTab}
          onClick={() => setDocTab(true)}
          label="文档审核"
        />
      </div>
      {docTab ? <DocReviewDashboard /> : <AuditApprovalDashboard />}
    </div>
  );
}

function TabButton({
  active,
  onClick,
  label,
}: {
  active: boolean;
  onClick: () => void;
  label: string;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      className="px-4 py-2 text-ui font-semibold"
      style={{
        color: active ? "#007acc" : "#616161",
        borderBottom: active ? "2px solid #007acc" : "2px solid transparent",
      }}
    >
      {label}
    </button>
  );
}
