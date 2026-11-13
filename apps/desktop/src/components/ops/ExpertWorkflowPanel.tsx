/**
 * ExpertWorkflowPanel —— 运营模式中间区「专家验收工作流」（2026-08-10）。
 *
 * 取代传统大 Chat：客户经理办业务不需要对话框，只需要满足专家团的交付物。
 *
 * 界面形态（v2，2026-08-10 用户反馈改造）：
 *   - 顶部专家团页签：每个专家团一个页签，切换页签切换整组专家；
 *     上传后出了审核结果 → 页签红点提示（查看该页签即清零）
 *   - 主体横向布局：当前团的每位专家一张卡，横向排列（像一排真人在帮你审核）
 *   - 拟人化：头像 + 打招呼语 + 审核意见以专家口吻的气泡呈现
 *   - 底部完成条：全部专家验收通过 → 生成交付物并导出 zip
 *
 * 状态来源：opsCaseStore（后端 ops.db + ops-cases 目录，全程写审计日志）。
 */
import { useEffect, useMemo, useRef, useState } from "react";
import { save } from "@tauri-apps/plugin-dialog";
import { useExpertTeamStore } from "@/store/expertTeamStore";
import { useOpsCaseStore } from "@/store/opsCaseStore";
import { useOpsStore } from "@/store/opsStore";
import { ipc } from "@/ipc/invoke";
import { ExpertTeamSelector } from "@/components/chat/ExpertTeamSelector";
import { AiThinkingIndicator } from "@/components/chat/AiStatus";
import { RiskAuditReportModal } from "@/components/ops/RiskAuditReportModal";
import { CredentialPreviewModal } from "@/components/ops/CredentialPreviewModal";
import { credentialNodeKey, useCredentialStore } from "@/store/credentialStore";
import {
  CASE_FILE_STATUS_META,
  type OpsCaseDraft,
  type OpsCaseFile,
  type OpsCaseQa,
  type OpsDraftField,
  type OpsTaskStartMeta,
} from "@/types/ops";
import type { ExpertMember, ExpertTeam } from "@/types/expertTeam";
import type { Feature } from "@/types/biznav";
import type { findOpsItem } from "@/components/ops/opsModules";
import {
  PackageIcon,
  ReceiptIcon,
  RotateCcwIcon,
  SettingsIcon,
  UsersIcon,
} from "@/components/ui/Icons";

interface ExpertWorkflowPanelProps {
  selection: {
    staticHit: ReturnType<typeof findOpsItem>;
    featureHit: Feature | null;
  } | null;
  projectName: string;
  /** 保存功能点的专家团预设（紧凑入口） */
  onSaveTeamPreset: (ids: string[]) => void;
}

/** 专家卡内的成员标识（团 + 成员，跨团重名不冲突） */
interface MemberRef {
  team: ExpertTeam;
  member: ExpertMember;
}

/** 拟人化头像配色：按名字稳定取一组渐变 */
const AVATAR_GRADIENTS = [
  "linear-gradient(135deg, #0e7490, #10a37f)",
  "linear-gradient(135deg, #0451a5, #0891b2)",
  "linear-gradient(135deg, #7c3aed, #db2777)",
  "linear-gradient(135deg, #b45309, #d97706)",
  "linear-gradient(135deg, #065f46, #059669)",
  "linear-gradient(135deg, #9d174d, #f59e0b)",
];

function avatarGradient(name: string): string {
  let hash = 0;
  for (const ch of name) hash = (hash + ch.charCodeAt(0)) % 997;
  return AVATAR_GRADIENTS[hash % AVATAR_GRADIENTS.length];
}

/** 「下一步」待办色调：按紧急度区分（打回补正 / 待传材料 / 待确认 / 审核中） */
const ACTION_TONE: Record<
  "fix" | "upload" | "confirm" | "wait",
  { icon: string; color: string; border: string; bg: string }
> = {
  fix: { icon: "✗", color: "#b91c1c", border: "#f3c8c8", bg: "#fdf3f3" },
  upload: { icon: "⬆", color: "#0451a5", border: "#d6e4f7", bg: "#f3f7fd" },
  confirm: { icon: "☑", color: "#047857", border: "#c6e8db", bg: "#eef9f4" },
  wait: { icon: "⟳", color: "#6b7280", border: "#e7e5e4", bg: "#f5f5f4" },
};

/**
 * 专家卡状态句（2026-11 交互优化）：替掉「你好，我负责X，把材料传给我」的静态问候语。
 * 卡片头部是最贵的位置，静态招呼语 6 张卡重复 6 遍零信息量；
 * 改为一眼看出这位专家此刻缺什么 / 卡在哪一步。
 */
function expertStatusLine(args: {
  accepted: boolean;
  busy: "uploading" | "reviewing" | "asking" | undefined;
  files: OpsCaseFile[];
  outputs: string[];
}): { text: string; color: string; bg: string; border: string } {
  const { accepted, busy, files, outputs } = args;
  const reviewing = files.filter((f) => f.status === "reviewing").length;
  const pending = files.filter((f) => f.status === "pending").length;
  const passed = files.filter((f) => f.status === "passed").length;
  const rejected = files.filter((f) => f.status === "rejected").length;

  if (accepted) {
    return {
      text: "✓ 已验收，交付标准全部确认，产物已入交付物柜。",
      color: "#047857",
      bg: "#eef9f4",
      border: "#c6e8db",
    };
  }
  if (busy === "uploading") {
    return {
      text: "⟳ 正在接收你的材料…",
      color: "#0451a5",
      bg: "#f3f7fd",
      border: "#d6e4f7",
    };
  }
  if (busy === "reviewing" || reviewing > 0) {
    return {
      text: `⟳ 正在审阅 ${reviewing || files.length} 份材料（先过系统合规审核）…`,
      color: "#0451a5",
      bg: "#f3f7fd",
      border: "#d6e4f7",
    };
  }
  if (files.length === 0) {
    return {
      text:
        outputs.length === 0
          ? "我还没有配置交付物，暂不能验收 —— 请到「专家团预设」补充。"
          : `等你上传材料。我要验收的是「${outputs[0]}」${outputs.length > 1 ? ` 等 ${outputs.length} 项` : ""}。`,
      color: "#0451a5",
      bg: "#f3f7fd",
      border: "#d6e4f7",
    };
  }
  if (rejected > 0) {
    return {
      text: `${rejected} 份材料被打回，展开材料看原因后补正重传（${passed} 份已通过）。`,
      color: "#b91c1c",
      bg: "#fdf3f3",
      border: "#f3c8c8",
    };
  }
  if (pending > 0) {
    return {
      text: `${pending} 份材料排队待审，${passed} 份已通过。`,
      color: "#b25c1a",
      bg: "#fdf6ee",
      border: "#f0dcc4",
    };
  }
  if (passed === files.length) {
    return {
      text: `${passed} 份材料全部通过 —— 请逐项确认下方交付标准完成验收。`,
      color: "#047857",
      bg: "#eef9f4",
      border: "#c6e8db",
    };
  }
  return {
    text: `${passed}/${files.length} 份通过，其余待处理。`,
    color: "#4b5563",
    bg: "#f5f5f4",
    border: "#e7e5e4",
  };
}

/** 剪贴板粘贴的文件命名美化：截图类通用名加时间戳，避免多张同名互盖 */
function renamePastedFile(f: File): File {
  const generic =
    /^(image|截图|粘贴的图片|blob)\.(png|jpg|jpeg|gif|webp)$/i.test(f.name);
  if (!generic) return f;
  const ts = new Date().toTimeString().slice(0, 8).replace(/:/g, "");
  const ext = f.name.split(".").pop() ?? "png";
  return new File([f], `粘贴材料-${ts}.${ext}`, { type: f.type });
}

/** 专家头像（首字圆形，验收通过后变绿勾） */
function ExpertAvatar({
  name,
  accepted,
  size = 34,
}: {
  name: string;
  accepted: boolean;
  size?: number;
}): JSX.Element {
  return (
    <span
      className="flex flex-shrink-0 items-center justify-center rounded-full font-semibold text-white"
      style={{
        width: size,
        height: size,
        background: accepted ? "#10a37f" : avatarGradient(name),
        fontSize: size * 0.42,
        boxShadow: "0 1px 3px rgba(0,0,0,0.18)",
      }}
      aria-hidden="true"
    >
      {accepted ? "✓" : name.slice(0, 1)}
    </span>
  );
}

export function ExpertWorkflowPanel({
  selection,
  projectName,
  onSaveTeamPreset,
}: ExpertWorkflowPanelProps): JSX.Element {
  const selectedItemId =
    selection?.featureHit?.id ?? selection?.staticHit?.item.id ?? null;
  const loadCase = useOpsCaseStore((s) => s.loadCase);

  // 切换业务 → 加载对应 Case（后端按 project_name + feature_id 定位）
  useEffect(() => {
    void loadCase(projectName, selectedItemId ?? "");
  }, [projectName, selectedItemId, loadCase]);

  if (!selection) {
    return (
      <div
        className="flex h-full flex-col items-center justify-center text-2xs"
        style={{ color: "#616161", backgroundColor: "#ffffff" }}
      >
        <div
          className="mb-2 flex items-center justify-center text-3xl"
          aria-hidden="true"
        >
          <UsersIcon size={40} />
        </div>
        <div>从左侧业务列表选择一个功能点开始办理</div>
        <div className="mt-1">选中后按专家上传材料、逐项验收，无需对话框</div>
      </div>
    );
  }

  return (
    <WorkflowBody
      key={selectedItemId ?? "none"}
      selection={selection}
      projectName={projectName}
      onSaveTeamPreset={onSaveTeamPreset}
    />
  );
}

// ---------------------------------------------------------------------------
// 主体（key 按业务切换重建，验收勾选等本地状态天然隔离）
// ---------------------------------------------------------------------------

function WorkflowBody({
  selection,
  projectName,
  onSaveTeamPreset,
}: ExpertWorkflowPanelProps & {
  selection: NonNullable<ExpertWorkflowPanelProps["selection"]>;
}): JSX.Element {
  const teams = useExpertTeamStore((s) => s.teams);
  const selectedTeamIds = useExpertTeamStore((s) => s.selectedTeamIds);
  const recommending = useExpertTeamStore((s) => s.recommending);
  const caseId = useOpsCaseStore((s) => s.case_id);
  const files = useOpsCaseStore((s) => s.files);
  const qa = useOpsCaseStore((s) => s.qa);
  const drafts = useOpsCaseStore((s) => s.drafts);
  /** 最近一次点交付物直开的草稿 → 右侧抽屉直接打开（2026-11-09 起不再自动全屏） */
  const lastDirectDraftId = useOpsCaseStore((s) => s.lastDirectDraftId);
  /** 本次会话新到达的草稿 → 自动展开草稿区；启动恢复的历史草稿不展开（BUGFIX #134） */
  const lastNewDraftId = useOpsCaseStore((s) => s.lastNewDraftId);
  /** 消费直开/新草稿标记后清标：离开再回来（组件重挂）不再重复弹抽屉 */
  const consumeDraftSignals = useOpsCaseStore((s) => s.consumeDraftSignals);
  const busyMembers = useOpsCaseStore((s) => s.busyMembers);
  const exporting = useOpsCaseStore((s) => s.exporting);
  const caseError = useOpsCaseStore((s) => s.error);
  const unreadByTeam = useOpsCaseStore((s) => s.unreadByTeam);
  /** 系统合规审核发现风险点 → 自动弹出的报告（材料 id） */
  const riskAuditFileId = useOpsCaseStore((s) => s.riskAuditFileId);
  const dismissRiskAudit = useOpsCaseStore((s) => s.dismissRiskAudit);
  /** 凭证预览（2026-11-09）：顶部入口 + 弹窗开关 + 预选节点 */
  const [credentialOpen, setCredentialOpen] = useState(false);
  const [credentialNodeKeyState, setCredentialNodeKeyState] = useState("");
  const [checkedOutputs, setCheckedOutputs] = useState<Set<string>>(
    () => new Set(),
  );
  const [presetOpen, setPresetOpen] = useState(false);
  const [moreOpen, setMoreOpen] = useState(false);
  const [activeTeamId, setActiveTeamId] = useState<string>("");
  /** 当前要弹合规审核报告的材料（审核出风险才置位） */
  const auditFile = useMemo(
    () =>
      riskAuditFileId ? files.find((f) => f.id === riskAuditFileId) : undefined,
    [riskAuditFileId, files],
  );

  const featureName =
    selection.featureHit?.name ?? selection.staticHit?.item.label ?? "";

  /** 业务台账状态（业务汇总，2026-11-09）：挂起 → 当前业务界面只读冻结 */
  const task = useOpsCaseStore((s) => s.task);
  const suspended = task?.status === "suspended";
  const suspendTask = useOpsCaseStore((s) => s.suspendTask);
  const resumeTask = useOpsCaseStore((s) => s.resumeTask);

  /** 业务上下文（建档用）：业务大类 / 小类 / 标题 —— 上传第一份材料时登记进业务台账 */
  const taskMeta = useMemo<OpsTaskStartMeta>(() => {
    const item = selection.staticHit?.item;
    const module = selection.staticHit?.module;
    return {
      project_name: projectName,
      feature_id: selection.featureHit?.id ?? item?.id ?? "",
      feature_label: item?.label ?? selection.featureHit?.name ?? "",
      category: module?.id ?? selection.featureHit?.category ?? "",
      category_label: module?.label ?? selection.featureHit?.category ?? "",
      title: featureName,
    };
  }, [selection, projectName, featureName]);

  // 已选专家团（保持选择顺序）
  const selectedTeams = useMemo<ExpertTeam[]>(
    () =>
      selectedTeamIds
        .map((id) => teams.find((t) => t.id === id))
        .filter((t): t is ExpertTeam => Boolean(t)),
    [teams, selectedTeamIds],
  );

  // 当前页签：默认第一个团；选择变化后当前团不在列表则回落第一个
  useEffect(() => {
    if (selectedTeams.length === 0) {
      if (activeTeamId !== "") setActiveTeamId("");
      return;
    }
    if (!selectedTeams.some((t) => t.id === activeTeamId)) {
      setActiveTeamId(selectedTeams[0].id);
    }
  }, [selectedTeams, activeTeamId]);

  // 正在查看的团来了新结果 → 直接清零（badge 只提醒未查看的页签）
  useEffect(() => {
    if (activeTeamId && unreadByTeam[activeTeamId]) {
      useOpsCaseStore.getState().markTeamRead(activeTeamId);
    }
  }, [activeTeamId, unreadByTeam]);

  const activeTeam = selectedTeams.find((t) => t.id === activeTeamId) ?? null;
  const activeMembers = useMemo<MemberRef[]>(
    () =>
      activeTeam
        ? activeTeam.members.map((m) => ({ team: activeTeam, member: m }))
        : [],
    [activeTeam],
  );

  // 交付物柜（BUGFIX #79）：通过验收的材料聚合展示，可预览/单独另存
  const [cabinetOpen, setCabinetOpen] = useState(false);
  const passedFiles = useMemo(
    () => files.filter((f) => f.status === "passed"),
    [files],
  );

  // 凭证预览（2026-11-09）：顶部按钮的「缺 N 张」与弹窗共用一份口径，
  // 不在本地另算估计（两边数字不一致最误导人）。
  const credentialTeamIds = useMemo(
    () => selectedTeams.map((t) => t.id),
    [selectedTeams],
  );
  // 材料增减 / 后台归类落库 / 影像识别完成 都会让凭证挂载关系变化 → 静默刷新
  const credentialStamp = useMemo(
    () =>
      files
        .map((f) => `${f.id}:${f.credential ?? ""}:${f.ocr?.blocks ?? 0}`)
        .join("|"),
    [files],
  );
  useEffect(() => {
    if (!caseId) return;
    void useCredentialStore
      .getState()
      .load(caseId, credentialTeamIds, { silent: true });
  }, [caseId, credentialTeamIds, credentialStamp]);
  const credentialStats = useCredentialStore((s) => s.view?.stats ?? null);
  /** 顶部按钮徽标：必传凭证已齐度 + 缺张数（没预设时不吹数字） */
  const credentialMissing = credentialStats?.missing.length ?? 0;
  const credentialBadge =
    credentialStats && credentialStats.required_total > 0
      ? ` ${credentialStats.required_ok}/${credentialStats.required_total}`
      : "";
  const openCredentials = (nodeKey = ""): void => {
    setCredentialNodeKeyState(nodeKey);
    setCredentialOpen(true);
  };

  // 草稿页签（BUGFIX #87）：多草稿不接龙，页签切换；新草稿出现自动切过去
  const [activeDraftId, setActiveDraftId] = useState("");
  useEffect(() => {
    if (drafts.length === 0) {
      if (activeDraftId !== "") setActiveDraftId("");
      return;
    }
    if (!drafts.some((d) => d.id === activeDraftId)) {
      setActiveDraftId(drafts[drafts.length - 1].id);
    }
  }, [drafts, activeDraftId]);
  const activeDraft = drafts.find((d) => d.id === activeDraftId) ?? null;
  // 草稿区默认收起（BUGFIX #134）：启动/切业务恢复的历史草稿不自动弹出表单，
  // 只留一条提醒栏；本会话新草稿到达（专家回答/点交付物直开）或手动点开才展开
  const [draftsOpen, setDraftsOpen] = useState(false);
  useEffect(() => {
    if (lastNewDraftId) {
      setDraftsOpen(true);
      // 草稿抽屉与交付物柜都是右侧浮层，同时开会叠在一起
      setCabinetOpen(false);
      setActiveDraftId(lastNewDraftId);
      consumeDraftSignals();
    }
  }, [lastNewDraftId, consumeDraftSignals]);
  // 全屏填写状态仍挂在面板层按草稿 id 记（2026-11-05 修复：页签切换重挂载
  // 不再导致回回弹全屏）。2026-11-09 起只有用户手动点草稿卡「⛶ 全屏」才进全屏；
  // 点交付物直开的草稿只把右侧抽屉拉出来，不再铺满整屏遮住办理区。
  const [expandedDraftId, setExpandedDraftId] = useState("");
  const autoExpandedDraftRef = useRef("");
  useEffect(() => {
    if (!lastDirectDraftId) {
      autoExpandedDraftRef.current = "";
      return;
    }
    if (autoExpandedDraftRef.current === lastDirectDraftId) return;
    autoExpandedDraftRef.current = lastDirectDraftId;
    // 点交付物 → 侧边抽屉出现即可（同时清掉可能残留的全屏状态）
    setExpandedDraftId("");
    setDraftsOpen(true);
    setCabinetOpen(false);
    setActiveDraftId(lastDirectDraftId);
    // 标记消费即清标：从其他界面回到业务办理（组件重挂）不再重复弹抽屉
    consumeDraftSignals();
  }, [lastDirectDraftId, consumeDraftSignals]);
  // 草稿关联的材料行：审核意见收进草稿卡展示，材料行不再重复气泡（BUGFIX #86）
  const draftFileIds = useMemo(
    () => new Set(drafts.map((d) => d.file_id).filter(Boolean)),
    [drafts],
  );

  // P0 消灭二次搬运（2026-08-10）：全局粘贴入件 —— 在任意系统复制截图/文件后
  // 直接 Ctrl+V，材料自动进当前团的带队专家（第一位），免去「打开选择器」两步。
  // 焦点在输入框/提问框时不拦截（正常打字优先）。
  useEffect(() => {
    const onPaste = (e: ClipboardEvent): void => {
      if (!activeTeam || activeTeam.members.length === 0) return;
      const target = e.target as HTMLElement | null;
      if (
        target &&
        (target.tagName === "INPUT" || target.tagName === "TEXTAREA")
      )
        return;
      const items = e.clipboardData?.items;
      if (!items) return;
      const files: File[] = [];
      for (const item of items) {
        if (item.kind === "file") {
          const f = item.getAsFile();
          if (f) files.push(renamePastedFile(f));
        }
      }
      if (files.length === 0) return;
      const lead = activeTeam.members[0];
      void useOpsCaseStore
        .getState()
        .attachFiles(activeTeam.id, lead.name, files, taskMeta)
        .then(() => {
          window.alert(
            `已粘贴 ${files.length} 份材料给「${lead.name}」（${activeTeam.name}带队专家）。\n如需交给其他专家，在对应专家卡里重新上传即可。`,
          );
        });
    };
    window.addEventListener("paste", onPaste);
    return () => window.removeEventListener("paste", onPaste);
  }, [activeTeam]);

  // 全部成员（跨团，用于总进度与导出判定）
  const allMembers = useMemo<MemberRef[]>(
    () =>
      selectedTeams.flatMap((t) =>
        t.members.map((m) => ({ team: t, member: m })),
      ),
    [selectedTeams],
  );

  // 单个专家是否验收完成：交付标准全部逐项确认
  const isMemberAccepted = (teamId: string, member: ExpertMember): boolean => {
    const outputs = member.outputs ?? [];
    if (outputs.length === 0) return false;
    return outputs.every((o) =>
      checkedOutputs.has(`${teamId}::${member.name}::${o}`),
    );
  };
  const acceptedCount = allMembers.filter((r) =>
    isMemberAccepted(r.team.id, r.member),
  ).length;
  const allAccepted =
    allMembers.length > 0 && acceptedCount === allMembers.length;

  /**
   * 「下一步」待办（2026-11 交互优化）：
   * 原界面只给一个「已验收 0/6」的静态计数，用户看到 6 张空卡不知道从哪开始。
   * 这里把每个未验收专家的实际阻塞点算出来，按紧急度排序（打回 > 传材料 > 待确认 > 审核中）。
   */
  const nextActions = useMemo(() => {
    type Tone = "fix" | "upload" | "confirm" | "wait";
    const rank: Record<Tone, number> = {
      fix: 0,
      upload: 1,
      confirm: 2,
      wait: 3,
    };
    const acts: Array<{
      key: string;
      teamId: string;
      memberName: string;
      label: string;
      tone: Tone;
    }> = [];
    for (const r of allMembers) {
      if (isMemberAccepted(r.team.id, r.member)) continue;
      const mine = files.filter((f) => f.member_key === r.member.name);
      const rejected = mine.filter((f) => f.status === "rejected").length;
      const running = mine.filter(
        (f) => f.status === "reviewing" || f.status === "pending",
      ).length;
      const base = {
        key: `${r.team.id}::${r.member.name}`,
        teamId: r.team.id,
        memberName: r.member.name,
      };
      if (rejected > 0) {
        acts.push({
          ...base,
          tone: "fix",
          label: `${r.member.name} · ${rejected} 份待补正`,
        });
      } else if (running > 0) {
        acts.push({
          ...base,
          tone: "wait",
          label: `${r.member.name} · ${running} 份审核中`,
        });
      } else if (mine.length === 0) {
        acts.push({
          ...base,
          tone: "upload",
          label: `${r.member.name} · 待上传材料`,
        });
      } else {
        acts.push({
          ...base,
          tone: "confirm",
          label: `${r.member.name} · 待确认交付标准`,
        });
      }
    }
    return acts.sort((a, b) => rank[a.tone] - rank[b.tone]);
    // isMemberAccepted 依赖 checkedOutputs，已拆开列出避免闭包陈旧
  }, [allMembers, files, checkedOutputs]);

  /** 点待办 → 切到对应团并把该专家卡滚到可视区 */
  const focusMember = (teamId: string, memberName: string): void => {
    setActiveTeamId(teamId);
    window.setTimeout(() => {
      document
        .getElementById(`expert-card-${teamId}-${memberName}`)
        ?.scrollIntoView({
          behavior: "smooth",
          block: "nearest",
          inline: "center",
        });
    }, 60);
  };

  const toggleOutput = (ref: MemberRef, output: string): void => {
    const key = `${ref.team.id}::${ref.member.name}::${output}`;
    setCheckedOutputs((prev) => {
      const next = new Set(prev);
      if (next.has(key)) next.delete(key);
      else next.add(key);
      return next;
    });
  };

  // 导出清单文案（进 zip README + 业务记录 materials_checked）
  const checklist = useMemo(
    () =>
      [...checkedOutputs].map((k) => {
        const [, memberName, output] = k.split("::");
        return `${memberName} → ${output}`;
      }),
    [checkedOutputs],
  );

  const teamNames = useMemo(
    () => selectedTeams.map((t) => t.name).join("、"),
    [selectedTeams],
  );

  // 多文档交叉比对（防呆提效，2026-08-10）：材料变化后自动查不一致项，
  // 导出前在底部完成条标红提醒（把「事后退件」提前到办理中）。失败静默不阻塞。
  const [inconsistencies, setInconsistencies] = useState<
    Array<{ field: string; values: Array<{ file: string; value: string }> }>
  >([]);
  useEffect(() => {
    if (!caseId || files.length === 0) {
      setInconsistencies([]);
      return;
    }
    let cancelled = false;
    void ipc
      .opsCaseCrosscheck(caseId)
      .then((r) => {
        if (!cancelled) setInconsistencies(r.inconsistencies ?? []);
      })
      .catch(() => undefined);
    return () => {
      cancelled = true;
    };
  }, [caseId, files]);

  /** 重新开始办理（BUGFIX #85）：确认后清空全部材料/问答/草稿 */
  const handleClearCase = async (): Promise<void> => {
    const bizId = selection.featureHit?.id ?? selection.staticHit?.item.id;
    if (!bizId) return;
    if (
      !window.confirm(
        "确定重新开始办理该业务？\n全部材料、专家问答与草稿将被清空，且不可恢复。",
      )
    ) {
      return;
    }
    await useOpsCaseStore.getState().clearCase(projectName, bizId);
  };

  /** 全部验收后：save 对话框选路径 → 后端打包 zip → 自动生成可审计业务记录 */
  const handleExport = async (): Promise<void> => {
    let picked: string | null = null;
    try {
      picked = await save({
        defaultPath: `${featureName}-交付物.zip`,
        filters: [{ name: "压缩包", extensions: ["zip"] }],
      });
    } catch {
      // 非 Tauri 环境（vitest）无对话框，静默降级
      return;
    }
    if (!picked) return;
    const ok = await useOpsCaseStore.getState().exportCase(picked, {
      featureName,
      teamName: teamNames,
      teamId: activeTeamId,
      checklist,
    });
    if (!ok) return;
    // 业务办理完成 → 生成可审计的业务记录卡片（结果 done）
    await useOpsStore.getState().createRecord({
      project_name: projectName,
      feature_id: selection.featureHit?.id ?? selection.staticHit?.item.id,
      business_type: selection.staticHit
        ? selection.staticHit.module.label
        : (selection.featureHit?.category ?? ""),
      title: featureName,
      summary:
        `专家团验收全部通过（${acceptedCount}/${allMembers.length} 位专家），` +
        `交付物已导出：${picked}`,
      materials_checked: checklist,
      materials_missing: [],
      risk_points: files
        .filter((f) => f.status === "rejected")
        .map((f) => `${f.member_key}/${f.file_name} 曾被打回`),
      result: "done",
      skill_id: "",
      session_id: caseId,
      source: "ai",
      created_by: "",
    });
    window.alert(`交付物导出成功：${picked}\n已生成可审计的业务记录卡片。`);
  };

  return (
    <div
      className="relative flex h-full min-h-0 flex-col"
      style={{ backgroundColor: "#f7f7f6" }}
    >
      {/* ===== 头部：业务 + 总进度 + 专家团选择 ===== */}
      <div
        className="flex-shrink-0 border-b px-4 pb-1.5 pt-2"
        style={{ borderColor: "#e0e0e0", backgroundColor: "#ffffff" }}
      >
        <div className="flex items-center gap-2">
          <span className="text-ui font-semibold" style={{ color: "#1f1f1f" }}>
            {featureName}
          </span>
          <span className="text-2xs" style={{ color: "#9ca3af" }}>
            专家正在为你审核材料 · 无需对话，按交付物办理
          </span>
          <span
            className="ml-auto rounded-full px-2 py-0.5 text-2xs font-semibold"
            style={{
              backgroundColor: allAccepted ? "#10a37f22" : "#f3f4f6",
              color: allAccepted ? "#10a37f" : "#6b7280",
            }}
          >
            {allAccepted
              ? "✓ 全部验收通过"
              : `已验收 ${acceptedCount}/${allMembers.length} 位专家`}
          </span>
          {/* 凭证预览（2026-11-09）：该业务要传哪几张图、实传了哪几张、点要素看影像 */}
          <button
            type="button"
            onClick={() => openCredentials()}
            className="rounded border px-2 py-0.5 text-2xs"
            style={{
              borderColor:
                credentialMissing > 0
                  ? "#e8b4b4"
                  : credentialBadge
                    ? "#10a37f66"
                    : "#e0e0e0",
              color:
                credentialMissing > 0
                  ? "#cd3131"
                  : credentialBadge
                    ? "#10a37f"
                    : "#616161",
              backgroundColor: "#ffffff",
            }}
            title={
              credentialMissing > 0
                ? `凭证还缺 ${credentialMissing} 张：${credentialStats?.missing
                    .map((m) => m.credential)
                    .join("、")}\n点开可逐张看影像与已采要素`
                : "查看本业务的凭证清单、影像与已采要素（点要素名可在图上定位）"
            }
          >
            <span className="flex items-center gap-1">
              <ReceiptIcon size={12} />
              凭证预览{credentialBadge}
              {credentialMissing > 0 && (
                <span
                  className="rounded-full px-1 text-[10px] font-bold"
                  style={{ backgroundColor: "#cd3131", color: "#ffffff" }}
                >
                  缺{credentialMissing}
                </span>
              )}
            </span>
          </button>
          <button
            type="button"
            onClick={() => setCabinetOpen((v) => !v)}
            className="rounded border px-2 py-0.5 text-2xs"
            style={{
              borderColor: passedFiles.length > 0 ? "#10a37f66" : "#e0e0e0",
              color: passedFiles.length > 0 ? "#10a37f" : "#616161",
              backgroundColor: "#ffffff",
            }}
            title="已验收通过的材料（可预览/单独另存）"
          >
            <span className="flex items-center gap-1">
              <PackageIcon size={12} />
              交付物柜{passedFiles.length > 0 ? ` ${passedFiles.length}` : ""}
            </span>
          </button>
          <button
            type="button"
            onClick={() => setPresetOpen((v) => !v)}
            className="rounded border px-2 py-0.5 text-2xs"
            style={{
              borderColor: "#e0e0e0",
              color: "#616161",
              backgroundColor: "#ffffff",
            }}
            title="为该业务预设专家团（选中时自动选团）"
          >
            <span className="flex items-center gap-1">
              <SettingsIcon size={12} />
              专家团预设
            </span>
          </button>
          {/* 挂起（业务汇总）：客户经理需暂停一下时冻结当前业务，去办别的业务。
              台账是「业务发起即建档」（上传第一份材料时建）—— 没建台账时按钮仍显示但置灰，
              否则新业务会以为没这个功能（2026-11-09 用户反馈「找不到挂起」） */}
          <button
            type="button"
            onClick={() => void (suspended ? resumeTask() : suspendTask())}
            disabled={!task}
            className="rounded border px-2 py-0.5 text-2xs font-semibold disabled:cursor-not-allowed"
            style={{
              borderColor: !task
                ? "#e0e0e0"
                : suspended
                  ? "#10a37f66"
                  : "#e0c48a",
              color: !task ? "#9ca3af" : suspended ? "#10a37f" : "#b25c1a",
              backgroundColor: "#ffffff",
              opacity: !task ? 0.6 : 1,
            }}
            title={
              !task
                ? "上传第一份材料后可用：业务发起即建档，之后才能挂起并在「业务汇总」里跟踪"
                : suspended
                  ? "恢复办理：解除挂起，回到挂起前的材料与进度"
                  : "挂起本业务：当前界面冻结，可先去办理其他业务"
            }
          >
            {suspended ? "▶ 恢复办理" : "⏸ 挂起"}
          </button>
          {/* 危险/低频操作收进溢出菜单（2026-11 交互优化）：「重新开始办理」会清空全部材料，
              不适合与常规按钮同权重并排，避免误点 */}
          <div className="relative">
            <button
              type="button"
              onClick={() => setMoreOpen((v) => !v)}
              className="rounded border px-2 py-0.5 text-2xs"
              style={{
                borderColor: "#e0e0e0",
                color: "#616161",
                backgroundColor: "#ffffff",
              }}
              title="更多操作"
              aria-expanded={moreOpen}
            >
              ⋯
            </button>
            {moreOpen && (
              <>
                <div
                  className="fixed inset-0 z-20"
                  onClick={() => setMoreOpen(false)}
                  aria-hidden="true"
                />
                <div
                  className="absolute right-0 top-full z-30 mt-1 w-36 rounded border py-1 shadow-lg"
                  style={{ borderColor: "#e0e0e0", backgroundColor: "#ffffff" }}
                >
                  <button
                    type="button"
                    onClick={() => {
                      setMoreOpen(false);
                      void handleClearCase();
                    }}
                    className="flex w-full items-center gap-1.5 px-2 py-1 text-left text-2xs hover:bg-[#f5f5f4]"
                    style={{ color: "#b25c1a" }}
                    title="清空当前业务的全部材料/问答/草稿，重新开始办理"
                  >
                    <RotateCcwIcon size={12} />
                    重新开始办理
                  </button>
                </div>
              </>
            )}
          </div>
        </div>
        <div className="mt-1 flex items-center gap-2">
          <ExpertTeamSelector />
          {/* 全局上传说明只说一次（2026-11 审美改）：原先每张专家卡重复一遍，5 张卡
              同一句话出现 5 次；提到头部右侧，卡片里只留差异信息 */}
          <span className="ml-auto text-[10px]" style={{ color: "#9ca3af" }}>
            支持拖拽文件到卡片 · 任意系统复制后直接 Ctrl+V 粘贴入件
          </span>
        </div>
        {presetOpen && (
          <TeamPresetEditor
            selection={selection}
            teams={teams}
            onSave={onSaveTeamPreset}
          />
        )}
        {/* 挂起横幅（业务汇总）：当前业务只读冻结，可去办别的业务，回头点「恢复办理」接着办 */}
        {suspended && (
          <div
            className="mt-1 flex items-center gap-2 rounded border px-2 py-1 text-2xs"
            style={{
              borderColor: "#e0c48a",
              backgroundColor: "#fdf8ef",
              color: "#b25c1a",
            }}
          >
            <span>
              ⏸ 本业务已挂起 ——
              当前界面只读，可先去办理其他业务；回来点「恢复办理」接着办。
            </span>
            <button
              type="button"
              onClick={() => void resumeTask()}
              className="ml-auto rounded border px-2 py-0.5 font-semibold"
              style={{
                borderColor: "#10a37f66",
                color: "#10a37f",
                backgroundColor: "#ffffff",
              }}
              title="恢复办理：解除挂起，回到挂起前的材料与进度"
            >
              ▶ 恢复办理
            </button>
          </div>
        )}
        {caseError && (
          <div className="mt-1 text-2xs" style={{ color: "#cd3131" }}>
            ⚠ {caseError}
          </div>
        )}
      </div>

      {/* ===== 下一步引导（2026-11 交互优化）：把「已验收 0/6」这张静态计分牌，
          换成「现在该干什么」的可点清单 —— 否则用户面对 6 张空卡无从下手 ===== */}
      {allMembers.length > 0 && (
        <div
          className="flex flex-shrink-0 items-center gap-2 border-b px-4 py-1.5"
          style={{ borderColor: "#e8e8e6", backgroundColor: "#fbfbfa" }}
        >
          <span
            className="flex-shrink-0 text-[10px] font-semibold"
            style={{ color: "#6b7280" }}
          >
            下一步
          </span>
          {nextActions.length === 0 ? (
            <span
              className="text-[11px] font-semibold"
              style={{ color: "#047857" }}
            >
              ✓ 全部专家已验收 —— 可生成交付物
            </span>
          ) : (
            <div className="flex min-w-0 flex-1 items-center gap-1.5 overflow-x-auto">
              {nextActions.slice(0, 4).map((a) => {
                const tone = ACTION_TONE[a.tone];
                return (
                  <button
                    key={a.key}
                    type="button"
                    onClick={() => focusMember(a.teamId, a.memberName)}
                    className="flex flex-shrink-0 items-center gap-1 rounded-full border px-2 py-0.5 text-[10px] transition-colors hover:brightness-95"
                    style={{
                      color: tone.color,
                      borderColor: tone.border,
                      backgroundColor: tone.bg,
                    }}
                    title="点击定位到该专家卡"
                  >
                    <span aria-hidden="true">{tone.icon}</span>
                    {a.label}
                  </button>
                );
              })}
              {nextActions.length > 4 && (
                <span
                  className="flex-shrink-0 text-[10px]"
                  style={{ color: "#9ca3af" }}
                >
                  共 {nextActions.length} 项待办
                </span>
              )}
            </div>
          )}
        </div>
      )}

      {/* ===== 专家团页签：每团一个页签，有新审核结果时红点提示 ===== */}
      {selectedTeams.length > 0 && (
        <div
          className="flex flex-shrink-0 items-end gap-1 overflow-x-auto px-3 pt-2"
          style={{ backgroundColor: "#ffffff" }}
        >
          {selectedTeams.map((t) => {
            const active = t.id === activeTeamId;
            const unread = unreadByTeam[t.id] ?? 0;
            const teamAccepted = t.members.filter((m) =>
              isMemberAccepted(t.id, m),
            ).length;
            return (
              <button
                key={t.id}
                type="button"
                onClick={() => {
                  setActiveTeamId(t.id);
                  useOpsCaseStore.getState().markTeamRead(t.id);
                }}
                className="flex flex-shrink-0 items-center gap-1.5 rounded-t-lg border px-3 py-1.5 text-2xs transition-colors"
                style={{
                  backgroundColor: active ? "#f7f7f6" : "#ffffff",
                  borderColor: active ? "#e0e0e0" : "transparent",
                  borderBottomColor: active ? "#f7f7f6" : "transparent",
                  color: active ? "#1f1f1f" : "#6b7280",
                  fontWeight: active ? 600 : 400,
                  marginBottom: -1,
                }}
                title={t.description || t.name}
              >
                <span>👥 {t.name}</span>
                {t.members.length > 0 && teamAccepted === t.members.length ? (
                  // 全员验收完成 → 绿勾终态（BUGFIX #79）
                  <span style={{ color: "#10a37f", fontWeight: 700 }}>
                    ✓ {teamAccepted}/{t.members.length}
                  </span>
                ) : (
                  <span style={{ color: "#9ca3af" }}>
                    {teamAccepted}/{t.members.length}
                  </span>
                )}
                {unread > 0 && (
                  <span
                    className="rounded-full px-1.5 py-px text-[10px] font-bold"
                    style={{ backgroundColor: "#dc2626", color: "#ffffff" }}
                    title={`${unread} 条新审核结果待查看`}
                  >
                    {unread} 条新结果
                  </span>
                )}
              </button>
            );
          })}
        </div>
      )}

      {/* ===== 主体：当前团的专家横向排列（像一排真人在帮你审核） ===== */}
      {/* 2026-11-09（用户要求）：交付物页签区直接放在专家卡下面铺平（替代右侧抽屉），
          专家卡在上、交付物表单在下，上下对照填写；卡片底部拉平等高 */}
      <div className="relative flex min-h-0 flex-1 flex-col">
        <div className="min-h-0 flex-1 overflow-x-auto overflow-y-hidden">
          {allMembers.length === 0 ? (
            recommending ? (
              // AI 推荐进行中（LLM 数秒）：展示思考动效，避免界面像卡住
              <div className="flex h-full flex-col items-center justify-center gap-2 p-3">
                <AiThinkingIndicator
                  compact
                  label={`专家团准备中 · 正在为「${featureName}」匹配专家`}
                />
                <div className="text-2xs" style={{ color: "#9ca3af" }}>
                  本地 → 内网 → 云端三级匹配，通常需要几秒…
                </div>
              </div>
            ) : (
              <div className="flex h-full items-center justify-center p-3">
                <div
                  className="rounded-lg border px-4 py-6 text-center text-2xs"
                  style={{
                    borderColor: "#e0e0e0",
                    backgroundColor: "#ffffff",
                    color: "#9ca3af",
                  }}
                >
                  当前未选择专家团
                  <div className="mt-1">
                    在上方专家团选择器中手动指定，或为业务预设专家团（AI
                    也会自动推荐）
                  </div>
                </div>
              </div>
            )
          ) : (
            // 自适应铺满（2026-11 交互优化）：原 w-[360px] 固定宽使 6 位专家必然横向滚动，
            // 改为 flex-1 + basis 280 均分屏宽，一屏看全全员；不够宽才降级为滚动。
            // 2026-11-09（用户要求）：底部拉平 —— 紧凑卡不再 alignSelf 短一截，全员等高铺满
            <div className="flex h-full items-stretch gap-2.5 p-2.5">
              {activeMembers.map((ref) => {
                const cardId = `expert-card-${ref.team.id}-${ref.member.name}`;
                const myFiles = files.filter(
                  (f) => f.member_key === ref.member.name,
                );
                const memberAccepted = isMemberAccepted(
                  ref.team.id,
                  ref.member,
                );
                // 无材料且无在途动作、未验收 → 紧凑卡（2026-11 审美改）：
                // 空卡只留头像 + 状态句 + 上传入口，不再各占一整列空白
                const compact =
                  !memberAccepted &&
                  myFiles.length === 0 &&
                  !busyMembers[ref.member.name];
                return (
                  <div
                    key={cardId}
                    id={cardId}
                    className="flex min-w-0 flex-1 basis-[280px] flex-col"
                    style={{ maxWidth: 460 }}
                  >
                    <ExpertCaseCard
                      refData={ref}
                      caseId={caseId}
                      files={myFiles}
                      draftFileIds={draftFileIds}
                      qa={qa.filter((q) => q.member_key === ref.member.name)}
                      busy={busyMembers[ref.member.name]}
                      accepted={memberAccepted}
                      compact={compact}
                      checkedOutputs={checkedOutputs}
                      taskMeta={taskMeta}
                      suspended={suspended}
                      onToggleOutput={(o) => toggleOutput(ref, o)}
                      onOpenCredential={openCredentials}
                    />
                  </div>
                );
              })}
            </div>
          )}
        </div>

        {/* ===== 交付物页签区（2026-11-09 用户要求）：直接放在专家卡下面铺平
            （替代右侧抽屉）：专家卡在上、交付物表单在下，点交付物即在此展开，
            需要更大空间时仍可点草稿卡「⛶ 全屏」 ===== */}
        {drafts.length > 0 && draftsOpen && (
          <div
            className="flex flex-shrink-0 flex-col border-t"
            style={{
              flex: "0 0 45%",
              borderColor: "#d4d4d4",
              backgroundColor: "#fafaf9",
            }}
          >
            <div
              className="flex flex-shrink-0 items-center gap-2 border-b px-3 py-2"
              style={{ borderColor: "#e0e0e0", backgroundColor: "#ffffff" }}
            >
              <span
                className="flex-shrink-0 text-2xs font-semibold"
                style={{ color: "#1f1f1f" }}
              >
                📝 交付草稿
              </span>
              <span
                className="min-w-0 truncate text-[10px]"
                style={{ color: "#9ca3af" }}
              >
                填写 → 提交专家审核 → 通过后并入交付物
              </span>
              <button
                type="button"
                onClick={() => setDraftsOpen(false)}
                className="ml-auto flex-shrink-0 rounded px-1.5 py-0.5 text-[10px] hover:bg-[#f5f5f4]"
                style={{ color: "#616161" }}
                title="收起交付物区（草稿不会丢失，下次点提醒栏展开）"
              >
                ▸ 收起
              </button>
            </div>
            {/* 草稿页签：一份草稿一个页签，不再上下接龙（BUGFIX #87） */}
            <div
              className="flex flex-shrink-0 items-end gap-1 overflow-x-auto border-b px-2 pt-1.5"
              style={{ borderColor: "#e0e0e0", backgroundColor: "#ffffff" }}
            >
              {drafts.map((d) => {
                const active = d.id === activeDraftId;
                const lf = files.find((f) => f.id === d.file_id);
                const dotColor =
                  d.status === "passed"
                    ? "#10a37f"
                    : lf?.status === "rejected"
                      ? "#cd3131"
                      : lf?.status === "reviewing"
                        ? "#0451a5"
                        : "#9ca3af";
                return (
                  <button
                    key={d.id}
                    type="button"
                    onClick={() => setActiveDraftId(d.id)}
                    className="flex flex-shrink-0 items-center gap-1 rounded-t border px-2 py-1 text-[10px]"
                    style={{
                      backgroundColor: active ? "#ffffff" : "#f3f3f2",
                      borderColor: active ? "#d4d4d4" : "transparent",
                      color: active ? "#1f1f1f" : "#6b7280",
                      fontWeight: active ? 600 : 400,
                    }}
                    title={d.title}
                  >
                    <span
                      className={
                        lf?.status === "reviewing" ? "animate-pulse" : ""
                      }
                      style={{ color: dotColor }}
                    >
                      ●
                    </span>
                    <span className="max-w-[160px] truncate">{d.title}</span>
                  </button>
                );
              })}
            </div>
            <div className="min-h-0 flex-1 overflow-y-auto p-3">
              {activeDraft && (
                <DraftFormCard
                  key={activeDraft.id}
                  draft={activeDraft}
                  expanded={activeDraft.id === expandedDraftId}
                  onExpandedChange={(v) =>
                    setExpandedDraftId(v ? activeDraft.id : "")
                  }
                  taskMeta={taskMeta}
                />
              )}
            </div>
          </div>
        )}
      </div>

      {/* ===== 交付草稿（BUGFIX #78）：要「模板/清单」不给一大段文字，界面直填 ===== */}
      {drafts.length > 0 && !draftsOpen && (
        // 收起态提醒栏（BUGFIX #134）：历史草稿不自动铺屏，点开才展开
        <div
          className="flex flex-shrink-0 items-center gap-2 border-t px-3 py-1"
          style={{ borderColor: "#e0e0e0", backgroundColor: "#fafaf9" }}
        >
          <button
            type="button"
            onClick={() => {
              setDraftsOpen(true);
              setCabinetOpen(false);
            }}
            className="rounded px-1.5 py-0.5 text-2xs font-semibold transition-colors hover:text-[#10a37f]"
            style={{ color: "#1f1f1f" }}
            title="展开交付草稿抽屉（启动时不自动弹出，避免遮挡办理区）"
          >
            📝 交付草稿（{drafts.length}）· 继续填写
          </button>
        </div>
      )}
      {/* 草稿区改为右侧抽屉，已上提至主体容器内，此处不再内嵌铺屏 */}

      {/* ===== 底部完成条：交叉比对提醒 + 生成交付物并导出 ===== */}
      <div
        className="flex flex-shrink-0 items-center gap-3 border-t px-4 py-2"
        style={{ borderColor: "#e0e0e0", backgroundColor: "#ffffff" }}
      >
        <span
          className="min-w-0 flex-1 truncate text-2xs"
          style={{ color: "#616161" }}
        >
          {allAccepted
            ? "所有专家已验收通过，业务办理完成"
            : `还有 ${allMembers.length - acceptedCount} 位专家未验收`}
        </span>
        {inconsistencies.length > 0 && (
          <span
            className="flex-shrink-0 rounded-full px-2 py-0.5 text-2xs font-semibold"
            style={{ backgroundColor: "#fdecec", color: "#cd3131" }}
            title={inconsistencies
              .map(
                (inc) =>
                  `字段「${inc.field}」不一致：` +
                  inc.values.map((v) => `${v.file}=${v.value}`).join("；"),
              )
              .join("\n")}
          >
            ⚠ 跨材料要素不一致 {inconsistencies.length} 项（详见报告初稿）
          </span>
        )}
        <button
          type="button"
          onClick={() => void handleExport()}
          disabled={!allAccepted || exporting || files.length === 0}
          className="flex-shrink-0 rounded px-4 py-1.5 text-2xs font-semibold"
          style={{
            // 未满足导出条件 → 灰底，不再用半透明绿（禁用态仍全彩会误导可点）
            backgroundColor:
              allAccepted && files.length > 0 ? "#10a37f" : "#c2cdc8",
            color: "#ffffff",
            opacity: exporting ? 0.7 : 1,
            cursor: exporting
              ? "wait"
              : allAccepted && files.length > 0
                ? "pointer"
                : "not-allowed",
          }}
          title={
            allAccepted && files.length > 0
              ? "打包全部交付文件 + 检查结果 + 问答记录为 zip"
              : files.length === 0
                ? "至少上传一份材料后才能导出"
                : "需全部专家验收通过"
          }
        >
          {exporting ? "⏳ 打包导出中…" : "📦 生成交付物并导出"}
        </button>
      </div>

      {/* ===== 交付物柜抽屉（BUGFIX #79）：通过验收的产物聚合，预览/另存 ===== */}
      {cabinetOpen && (
        <DeliverableCabinet
          files={passedFiles}
          onClose={() => setCabinetOpen(false)}
        />
      )}

      {/* ===== 凭证预览（2026-11-09）：凭证清单 · 影像放大高亮 · 拖动改归属 ===== */}
      {credentialOpen && (
        <CredentialPreviewModal
          caseId={caseId}
          teamIds={credentialTeamIds}
          readOnly={suspended}
          initialNodeKey={credentialNodeKeyState}
          onClose={() => {
            setCredentialOpen(false);
            setCredentialNodeKeyState("");
          }}
        />
      )}

      {/* ===== 系统合规审核报告（2026-11-05）：先过审核模块，有风险点才弹 ===== */}
      {auditFile && (
        <RiskAuditReportModal file={auditFile} onClose={dismissRiskAudit} />
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// 专家团预设编辑（紧凑内嵌）
// ---------------------------------------------------------------------------

function TeamPresetEditor({
  selection,
  teams,
  onSave,
}: {
  selection: NonNullable<ExpertWorkflowPanelProps["selection"]>;
  teams: ExpertTeam[];
  onSave: (ids: string[]) => void;
}): JSX.Element {
  const feature = selection.featureHit;
  return (
    <div
      className="mt-2 rounded border p-2"
      style={{ borderColor: "#e0e0e0", backgroundColor: "#fafafa" }}
    >
      <div className="mb-1 text-2xs" style={{ color: "#616161" }}>
        专家团预设（选中该业务时自动选中；不预设则由 AI 判断）：
      </div>
      {!feature ? (
        <div className="text-2xs" style={{ color: "#9ca3af" }}>
          静态导航项不支持预设，选中时由 AI 自动判断专家团
        </div>
      ) : teams.length === 0 ? (
        <div className="text-2xs" style={{ color: "#9ca3af" }}>
          系统还没有专家团，请到 设置 → 专家团 导入/新建
        </div>
      ) : (
        <div className="flex flex-wrap gap-x-4 gap-y-0.5">
          {teams.map((t) => {
            const preset = feature.expert_team_ids ?? [];
            const checked = preset.includes(t.id);
            return (
              <label
                key={t.id}
                className="flex cursor-pointer items-center gap-1.5 text-2xs"
                style={{ color: "#1f1f1f" }}
              >
                <input
                  type="checkbox"
                  checked={checked}
                  onChange={() => {
                    const next = checked
                      ? preset.filter((x) => x !== t.id)
                      : [...preset, t.id];
                    onSave(next);
                  }}
                />
                <span className="font-semibold">{t.name}</span>
                {!t.enabled && (
                  <span style={{ color: "#cd3131" }}>（已停用）</span>
                )}
              </label>
            );
          })}
        </div>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// 单个专家验收卡（拟人化）：头像打招呼 + 材料上传 + AI 审核气泡 + 交付标准 + 提问
// ---------------------------------------------------------------------------

function ExpertCaseCard({
  refData,
  caseId,
  files,
  draftFileIds,
  qa,
  busy,
  accepted,
  compact,
  checkedOutputs,
  taskMeta,
  suspended,
  onToggleOutput,
  onOpenCredential,
}: {
  refData: MemberRef;
  caseId: string;
  files: OpsCaseFile[];
  /** 草稿提交生成的材料行 id（审核意见在草稿卡展示，BUGFIX #86） */
  draftFileIds: Set<string>;
  qa: OpsCaseQa[];
  busy: "uploading" | "reviewing" | "asking" | undefined;
  accepted: boolean;
  /** 紧凑态（无材料且无在途动作）：只留头像 + 状态句 + 上传入口 */
  compact: boolean;
  checkedOutputs: Set<string>;
  /** 业务上下文（业务汇总建档用） */
  taskMeta: OpsTaskStartMeta;
  /** 业务已挂起（业务汇总）：上传/提问只读 */
  suspended: boolean;
  onToggleOutput: (output: string) => void;
  /** 打开凭证预览（传节点 key 可直接定位到该材料所属凭证） */
  onOpenCredential: (nodeKey: string) => void;
}): JSX.Element {
  const { team, member } = refData;
  const fileInputRef = useRef<HTMLInputElement>(null);
  const [question, setQuestion] = useState("");
  const [dragOver, setDragOver] = useState(false);
  /** 正在直开表单的交付物名（防连点 + 行内加载提示） */
  const [openingOutput, setOpeningOutput] = useState("");
  const outputs = member.outputs ?? [];
  const passedFiles = files.filter((f) => f.status === "passed").length;
  const rejectedFiles = files.filter((f) => f.status === "rejected").length;
  /** 提问框默认收起（2026-11 交互优化）：6 张卡常驻 6 个输入框噪音过大，
      且产品主张「无需对话」，提问应作为次要入口按需展开 */
  const [askOpen, setAskOpen] = useState(false);
  /** 展开时自动聚焦（2026-11-09 动画改造：两态 pane 常驻叠放，
      autoFocus 只在首次挂载生效，改用 ref 在展开时手动聚焦） */
  const askInputRef = useRef<HTMLInputElement>(null);
  useEffect(() => {
    if (askOpen) askInputRef.current?.focus();
  }, [askOpen]);
  const statusLine = expertStatusLine({ accepted, busy, files, outputs });
  // 交付标准块：完整卡与紧凑卡共用 —— 点条目直开草稿/表单不依赖先传材料，
  // 紧凑卡必须保留（回归测试发现：收掉会断「点交付物直开表单」链路）
  const outputsBlock = (
    <div>
      <div
        className="mb-0.5 text-[10px] font-semibold"
        style={{ color: "#059669" }}
        title="点条目可直接完成（有表单的直开表单）；逐项勾选 = 验收确认"
      >
        交付标准
      </div>
      {outputs.length === 0 ? (
        <div className="text-[10px]" style={{ color: "#9ca3af" }}>
          该专家未定义交付物，无法验收 —— 请到 设置 → 专家团 补充
        </div>
      ) : (
        <div className="space-y-0.5">
          {outputs.map((o) => {
            const isChecked = checkedOutputs.has(
              `${team.id}::${member.name}::${o}`,
            );
            const hasForm = (member.output_forms?.[o] ?? []).length > 0;
            return (
              <div
                key={o}
                className="flex items-start gap-1.5 text-[10px]"
                style={{ color: isChecked ? "#059669" : "#333333" }}
              >
                <input
                  type="checkbox"
                  checked={isChecked}
                  onChange={() => onToggleOutput(o)}
                  className="mt-0.5"
                />
                {isChecked ? (
                  <span style={{ textDecoration: "line-through" }}>{o}</span>
                ) : (
                  <button
                    type="button"
                    disabled={busy === "asking" || openingOutput !== ""}
                    onClick={() => void handleOutputClick(o)}
                    title={
                      hasForm
                        ? `点击直接填写「${o}」表单`
                        : `点击让${member.name}自动生成「${o}」`
                    }
                    className="text-left underline decoration-dotted underline-offset-2 transition-colors hover:text-[#10a37f] disabled:opacity-60"
                  >
                    {openingOutput === o
                      ? "正在打开表单…"
                      : hasForm
                        ? `${o} ✍`
                        : o}
                  </button>
                )}
              </div>
            );
          })}
        </div>
      )}
    </div>
  );

  const handlePickFiles = async (list: FileList | null): Promise<void> => {
    if (!list || list.length === 0) return;
    await useOpsCaseStore
      .getState()
      .attachFiles(team.id, member.name, [...list], taskMeta);
    if (fileInputRef.current) fileInputRef.current.value = "";
  };

  // P0 消灭二次搬运（2026-08-10）：拖拽文件到专家卡直接入件，免去点按钮+选择器
  const handleDrop = async (e: React.DragEvent): Promise<void> => {
    e.preventDefault();
    setDragOver(false);
    const dropped = [...(e.dataTransfer?.files ?? [])];
    if (dropped.length > 0) {
      await useOpsCaseStore
        .getState()
        .attachFiles(team.id, member.name, dropped, taskMeta);
    }
  };

  const handleAsk = async (): Promise<void> => {
    const q = question.trim();
    if (!q) return;
    setQuestion("");
    await useOpsCaseStore.getState().askExpert(team.id, member.name, q);
  };

  // 点交付物直接完成（2026-08-14）：团里定义了表单模板 → 零 LLM 直开表单；
  // 未定义模板 → 自动拼好提问发给该专家（后端识别模板意图生成草稿），
  // 用户不再需要手动输入框打字 + 点「问专家」。
  const handleOutputClick = async (o: string): Promise<void> => {
    if (busy === "asking" || openingOutput !== "") return;
    const formFields = member.output_forms?.[o] ?? [];
    if (formFields.length > 0) {
      setOpeningOutput(o);
      try {
        await useOpsCaseStore
          .getState()
          .createDirectDraft(team.id, member.name, o);
      } finally {
        setOpeningOutput("");
      }
      return;
    }
    await useOpsCaseStore
      .getState()
      .askExpert(
        team.id,
        member.name,
        `请帮我完成交付物「${o}」（如需填写请给出草稿表单）`,
      );
  };

  return (
    <div
      className="flex h-full w-full flex-col overflow-hidden rounded-xl border"
      style={{
        borderColor: dragOver ? "#10a37f" : accepted ? "#10a37f66" : "#e0e0e0",
        backgroundColor: "#ffffff",
        boxShadow: dragOver
          ? "0 0 0 3px rgba(16,163,127,0.15)"
          : "0 1px 2px rgba(0,0,0,0.04)",
        transition: "border-color 0.15s, box-shadow 0.15s",
      }}
      onDragOver={(e) => {
        e.preventDefault();
        setDragOver(true);
      }}
      onDragLeave={() => setDragOver(false)}
      onDrop={(e) => void handleDrop(e)}
    >
      {/* 卡头：头像 + 姓名职务 + 状态 */}
      <div className="flex items-center gap-2.5 px-3 pt-3">
        <ExpertAvatar name={member.name} accepted={accepted} />
        <div className="min-w-0 flex-1">
          <div className="flex items-center gap-1.5">
            <span
              className="truncate text-ui font-semibold"
              style={{ color: "#1f1f1f" }}
            >
              {member.name}
            </span>
            <span
              className="flex-shrink-0 rounded-full px-1.5 py-px text-[10px] font-semibold"
              style={{
                backgroundColor: accepted ? "#10a37f22" : "#f3f4f6",
                color: accepted ? "#10a37f" : "#6b7280",
              }}
            >
              {accepted
                ? "已验收"
                : files.length === 0
                  ? "待交材料"
                  : `${passedFiles} 通过${rejectedFiles ? ` · ${rejectedFiles} 打回` : ""}`}
            </span>
          </div>
          <div
            className="truncate text-2xs"
            style={{ color: "#616161" }}
            title={member.role}
          >
            {member.role}
          </div>
        </div>
      </div>

      {/* 状态句（替掉静态问候语）：一眼看出这位专家此刻缺什么 / 卡在哪 */}
      <div
        className="mx-3 mt-2 rounded-lg px-2.5 py-1.5 text-[11px] leading-snug"
        style={{
          backgroundColor: statusLine.bg,
          color: statusLine.color,
          border: `1px solid ${statusLine.border}`,
        }}
      >
        {statusLine.text}
      </div>

      {/* 卡身：无材料且无在途动作 → 紧凑态（2026-11 审美改）：只留上传入口，
          材料/交付标准/问答在上传后展开 —— 空卡不再各占一整列空白；
          有内容 → 三段式（BUGFIX #81）—— 上传/交付标准常驻，问答独立滚动 */}
      {compact ? (
        <div className="flex-shrink-0 space-y-2 px-3 pb-2.5 pt-2">
          <input
            ref={fileInputRef}
            type="file"
            multiple
            className="hidden"
            onChange={(e) => void handlePickFiles(e.target.files)}
          />
          <button
            type="button"
            onClick={() => fileInputRef.current?.click()}
            disabled={!caseId || busy === "uploading" || suspended}
            className="w-full rounded-lg border border-dashed px-2 py-1.5 text-2xs font-semibold transition-colors hover:border-[#10a37f] hover:text-[#10a37f]"
            style={{
              borderColor: dragOver ? "#10a37f" : "#c9d4d0",
              color: dragOver ? "#10a37f" : "#0451a5",
              backgroundColor: dragOver ? "#eef9f4" : "#fafcfb",
              opacity: !caseId || suspended ? 0.5 : 1,
            }}
            title={
              suspended ? "本业务已挂起，恢复办理后才能上传材料" : undefined
            }
          >
            {dragOver ? "⬇ 松开即上传" : "⬆ 上传材料"}
          </button>
          {/* 交付标准常驻：点条目直开草稿/表单不依赖先传材料 */}
          {outputsBlock}
        </div>
      ) : (
        <div
          className="mt-2 flex min-h-0 flex-1 flex-col border-t"
          style={{ borderColor: "#f0f0f0" }}
        >
          {/* ① 常驻区：上传材料 + 材料列表 + 交付标准（不随问答滚动） */}
          <div
            className="flex-shrink-0 space-y-2 border-b px-3 py-2"
            style={{ borderColor: "#f0f0f0" }}
          >
            {/* 上传材料入口 */}
            <input
              ref={fileInputRef}
              type="file"
              multiple
              className="hidden"
              onChange={(e) => void handlePickFiles(e.target.files)}
            />
            <button
              type="button"
              onClick={() => fileInputRef.current?.click()}
              disabled={!caseId || busy === "uploading" || suspended}
              className="w-full rounded-lg border border-dashed px-2 py-1.5 text-2xs font-semibold transition-colors hover:border-[#10a37f] hover:text-[#10a37f]"
              style={{
                borderColor: dragOver ? "#10a37f" : "#c9d4d0",
                color: dragOver ? "#10a37f" : "#0451a5",
                backgroundColor: dragOver ? "#eef9f4" : "#fafcfb",
                opacity: !caseId || suspended ? 0.5 : 1,
              }}
              title={
                suspended ? "本业务已挂起，恢复办理后才能上传材料" : undefined
              }
            >
              {dragOver ? "⬇ 松开即上传" : "⬆ 上传材料"}
            </button>

            {/* 上传/审核中的思考动效 */}
            {busy === "uploading" && (
              <AiThinkingIndicator compact label="上传材料中" />
            )}
            {busy === "reviewing" && (
              <AiThinkingIndicator
                compact
                label={`${member.name}正在审核 · 先过系统合规审核`}
              />
            )}

            {/* 材料文件列表（多份时区内自滚，入口与交付标准仍常驻） */}
            {files.length > 0 && (
              <div className="max-h-44 space-y-1.5 overflow-y-auto">
                {files.map((f) => (
                  <CaseFileRow
                    key={f.id}
                    file={f}
                    expertName={member.name}
                    draftLinked={draftFileIds.has(f.id)}
                    onOpenCredential={onOpenCredential}
                  />
                ))}
              </div>
            )}

            {/* 交付标准：共用块（紧凑卡也保留） */}
            {outputsBlock}
          </div>

          {/* ② 滚动区：问答记录独立滚动，不遮挡上方常驻区 */}
          <div className="min-h-0 flex-1 overflow-y-auto px-3 py-2">
            {qa.length > 0 && (
              <div className="space-y-1.5">
                {qa.map((item) => (
                  <div key={item.id}>
                    <div
                      className="ml-6 rounded-lg rounded-tr-none border px-2 py-1 text-[10px]"
                      style={{
                        borderColor: "#eef2f0",
                        backgroundColor: "#f5f5f4",
                        color: "#6b7280",
                      }}
                    >
                      问：{item.question}
                    </div>
                    <div className="mt-1 flex items-start gap-1.5">
                      <ExpertAvatar
                        name={member.name}
                        accepted={accepted}
                        size={18}
                      />
                      <div
                        className="min-w-0 flex-1 whitespace-pre-wrap rounded-lg rounded-tl-none border px-2 py-1 text-[10px]"
                        style={{
                          borderColor: "#e3efe9",
                          backgroundColor: "#f2f7f5",
                          color: "#202124",
                        }}
                      >
                        {item.answer}
                      </div>
                    </div>
                  </div>
                ))}
              </div>
            )}
          </div>

          {/* ③ 常驻区：提问入口（默认收起，按需展开 —— 避免 6 个输入框压过材料区）。
            2026-11-09（用户要求）：收起⇄展开加高度+透明度过渡动画，不再瞬间跳变；
            两态 pane 常驻叠放（ask-dock：grid 0fr⇄1fr 裁切高度），以 opacity + pointer-events 切换 */}
          <div
            className="flex-shrink-0 border-t px-3 py-1.5"
            style={{ borderColor: "#f0f0f0" }}
          >
            {busy === "asking" ? (
              <AiThinkingIndicator compact label={`${member.name}思考中`} />
            ) : (
              <div
                className="ask-dock"
                style={{ gridTemplateRows: askOpen ? "1fr" : "0fr" }}
              >
                <div className="ask-dock-clip">
                  {/* 收起态：追问入口（淡出时 pointer-events 穿透，避免透明层挡住输入行） */}
                  <div
                    className="ask-dock-pane"
                    style={{
                      opacity: askOpen ? 0 : 1,
                      pointerEvents: askOpen ? "none" : "auto",
                    }}
                  >
                    <button
                      type="button"
                      onClick={() => setAskOpen(true)}
                      className="flex w-full items-center gap-1 rounded px-1 py-0.5 text-[10px] transition-colors hover:text-[#10a37f]"
                      style={{ color: "#9ca3af" }}
                      title={`向${member.name}追问（可选，材料优先）`}
                    >
                      <span aria-hidden="true">💬</span>向{member.name}追问
                      {qa.length > 0 && (
                        <span style={{ color: "#0451a5" }}>
                          · 已有 {qa.length} 条问答
                        </span>
                      )}
                    </button>
                  </div>
                  {/* 展开态：提问输入行（高度展开 + 淡入，发送按钮同步黑化） */}
                  <div
                    className="ask-dock-pane"
                    style={{
                      opacity: askOpen ? 1 : 0,
                      pointerEvents: askOpen ? "auto" : "none",
                    }}
                  >
                    <div className="flex items-center gap-1.5">
                      <input
                        ref={askInputRef}
                        type="text"
                        value={question}
                        disabled={suspended}
                        onChange={(e) => setQuestion(e.target.value)}
                        onKeyDown={(e) => {
                          if (e.key === "Enter") void handleAsk();
                        }}
                        placeholder={`向${member.name}提问…（回车发送）`}
                        className="min-w-0 flex-1 rounded border px-2 py-1 text-2xs outline-none focus:border-[#10a37f] disabled:opacity-50"
                        style={{
                          backgroundColor: "#ffffff",
                          borderColor: "#e0e0e0",
                          color: "#202124",
                        }}
                      />
                      <button
                        type="button"
                        onClick={() => void handleAsk()}
                        disabled={!question.trim() || suspended}
                        className="flex-shrink-0 rounded px-2 py-1 text-2xs font-semibold disabled:opacity-40"
                        style={{ backgroundColor: "#1f1f1f", color: "#ffffff" }}
                      >
                        发送
                      </button>
                      <button
                        type="button"
                        onClick={() => setAskOpen(false)}
                        className="flex-shrink-0 rounded px-1 py-1 text-[10px]"
                        style={{ color: "#9ca3af" }}
                        title="收起提问框"
                      >
                        ✕
                      </button>
                    </div>
                  </div>
                </div>
              </div>
            )}
          </div>
        </div>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// 交付物柜（BUGFIX #79）：验收通过的产物聚合抽屉，支持预览（md 直显）与另存
// ---------------------------------------------------------------------------

function DeliverableCabinet({
  files,
  onClose,
}: {
  files: OpsCaseFile[];
  onClose: () => void;
}): JSX.Element {
  const [preview, setPreview] = useState<{ name: string; text: string } | null>(
    null,
  );
  const [busyId, setBusyId] = useState("");

  const handlePreview = async (f: OpsCaseFile): Promise<void> => {
    setBusyId(f.id);
    try {
      const r = await ipc.opsCaseFileContent(f.id);
      const bytes = Uint8Array.from(atob(r.content_base64), (c) =>
        c.charCodeAt(0),
      );
      setPreview({
        name: r.file_name,
        text: new TextDecoder("utf-8").decode(bytes),
      });
    } catch {
      window.alert("预览失败：文件内容读取不了");
    } finally {
      setBusyId("");
    }
  };

  const handleSaveAs = async (f: OpsCaseFile): Promise<void> => {
    let picked: string | null = null;
    try {
      picked = await save({ defaultPath: f.file_name });
    } catch {
      return; // 非 Tauri 环境静默降级
    }
    if (!picked) return;
    setBusyId(f.id);
    try {
      await ipc.opsCaseFileSaveAs(f.id, picked);
      window.alert(`已保存到：${picked}`);
    } catch (e) {
      window.alert(`保存失败：${e instanceof Error ? e.message : String(e)}`);
    } finally {
      setBusyId("");
    }
  };

  return (
    <>
      <div
        className="absolute bottom-0 right-0 top-0 z-20 flex w-[320px] flex-col border-l shadow-xl"
        style={{ borderColor: "#e0e0e0", backgroundColor: "#ffffff" }}
      >
        <div
          className="flex flex-shrink-0 items-center justify-between border-b px-3 py-2"
          style={{ borderColor: "#e0e0e0", backgroundColor: "#fafaf9" }}
        >
          <span className="text-2xs font-semibold" style={{ color: "#1f1f1f" }}>
            📦 交付物柜
            <span className="ml-1 font-normal" style={{ color: "#9ca3af" }}>
              验收通过即入柜，导出 zip 时也包含
            </span>
          </span>
          <button
            type="button"
            onClick={onClose}
            className="rounded px-1.5 text-2xs hover:bg-vscode-border"
            style={{ color: "#616161" }}
          >
            ✕
          </button>
        </div>
        <div className="flex-1 space-y-1.5 overflow-y-auto p-2">
          {files.length === 0 && (
            <div
              className="px-2 py-8 text-center text-2xs"
              style={{ color: "#9ca3af" }}
            >
              还没有验收通过的产物
              <div className="mt-1">
                上传材料或提交草稿，专家验收通过后自动入柜
              </div>
            </div>
          )}
          {files.map((f) => (
            <div
              key={f.id}
              className="rounded-lg border px-2 py-1.5"
              style={{ borderColor: "#e2e8e6", backgroundColor: "#fbfdfc" }}
            >
              <div className="flex items-center gap-1.5">
                <span aria-hidden="true">✅</span>
                <span
                  className="min-w-0 flex-1 truncate text-2xs font-semibold"
                  style={{ color: "#202124" }}
                  title={f.file_name}
                >
                  {f.file_name}
                </span>
              </div>
              <div className="mt-0.5 text-[10px]" style={{ color: "#9ca3af" }}>
                验收专家：{f.member_key} ·{" "}
                {f.reviewed_by === "ai" ? "AI 验收" : "人工验收"}
              </div>
              <div className="mt-1 flex gap-1.5">
                <button
                  type="button"
                  onClick={() => void handlePreview(f)}
                  disabled={busyId === f.id}
                  className="rounded border px-2 py-0.5 text-[10px] disabled:opacity-50"
                  style={{
                    borderColor: "#d4d4d4",
                    color: "#0451a5",
                    backgroundColor: "#ffffff",
                  }}
                >
                  👁 预览
                </button>
                <button
                  type="button"
                  onClick={() => void handleSaveAs(f)}
                  disabled={busyId === f.id}
                  className="rounded border px-2 py-0.5 text-[10px] disabled:opacity-50"
                  style={{
                    borderColor: "#d4d4d4",
                    color: "#059669",
                    backgroundColor: "#ffffff",
                  }}
                >
                  💾 另存…
                </button>
              </div>
            </div>
          ))}
        </div>
      </div>
      {/* 预览弹窗：md/txt 直显文本 */}
      {preview && (
        <div
          className="absolute inset-0 z-30 flex items-center justify-center"
          style={{ backgroundColor: "rgba(0,0,0,0.4)" }}
          onClick={() => setPreview(null)}
        >
          <div
            className="flex max-h-[85%] w-[640px] flex-col rounded-lg shadow-2xl"
            style={{ backgroundColor: "#ffffff", border: "1px solid #d4d4d4" }}
            onClick={(e) => e.stopPropagation()}
          >
            <div
              className="flex flex-shrink-0 items-center justify-between border-b px-3 py-2"
              style={{ borderColor: "#e0e0e0", backgroundColor: "#fafaf9" }}
            >
              <span
                className="truncate text-2xs font-semibold"
                style={{ color: "#1f1f1f" }}
              >
                {preview.name}
              </span>
              <button
                type="button"
                onClick={() => setPreview(null)}
                className="rounded px-1.5 text-2xs hover:bg-vscode-border"
                style={{ color: "#616161" }}
              >
                ✕
              </button>
            </div>
            <pre
              className="flex-1 overflow-auto whitespace-pre-wrap p-3 text-2xs"
              style={{ color: "#202124" }}
            >
              {preview.text}
            </pre>
          </div>
        </div>
      )}
    </>
  );
}

// ---------------------------------------------------------------------------
// 审核步骤链（BUGFIX #79）：接收 → 解析 → 提取 → 结论，过程可见防黑盒
// ---------------------------------------------------------------------------

type StepState = "done" | "doing" | "todo" | "warn" | "bad";

function ReviewSteps({ file }: { file: OpsCaseFile }): JSX.Element {
  const reviewing = file.status === "reviewing";
  const hasFields = (file.extracted_fields?.length ?? 0) > 0;
  const steps: Array<{ label: string; state: StepState }> = [
    { label: "接收", state: "done" },
    { label: "解析", state: reviewing ? "doing" : "done" },
    {
      label: "提取要素",
      state: reviewing ? "todo" : hasFields ? "done" : "todo",
    },
    {
      label: "结论",
      state: reviewing
        ? "todo"
        : file.status === "passed"
          ? "done"
          : file.status === "rejected"
            ? "bad"
            : "warn",
    },
  ];
  const color: Record<StepState, string> = {
    done: "#10a37f",
    doing: "#0451a5",
    todo: "#9ca3af",
    warn: "#b25c1a",
    bad: "#cd3131",
  };
  const icon: Record<StepState, string> = {
    done: "✓",
    doing: "⟳",
    todo: "○",
    warn: "!",
    bad: "✗",
  };
  return (
    <div className="mt-1 flex items-center gap-1">
      {steps.map((s, i) => (
        <span key={s.label} className="flex items-center gap-1">
          {i > 0 && <span style={{ color: "#d4d4d4" }}>→</span>}
          <span
            className="flex items-center gap-0.5 text-[10px]"
            style={{ color: color[s.state] }}
            title={`${s.label}：${s.state === "done" ? "完成" : s.state === "doing" ? "进行中" : s.state === "bad" ? "未通过" : s.state === "warn" ? "待确认" : "未到"}`}
          >
            <span className={s.state === "doing" ? "animate-spin" : ""}>
              {icon[s.state]}
            </span>
            {s.label}
          </span>
        </span>
      ))}
    </div>
  );
}

// ---------------------------------------------------------------------------
// 交付草稿表单（BUGFIX #78）：专家给的结构化模板界面直填，
// 提交后自动成为材料走专家审核，通过即并入交付物 zip
// ---------------------------------------------------------------------------

/** 单个字段控件（text/textarea/select/date/file） */
function DraftFieldInput({
  field,
  value,
  fileName,
  disabled,
  expanded = false,
  onChange,
  onFilePick,
}: {
  field: OpsDraftField;
  value: string;
  /** file 类型：已选文件名 */
  fileName?: string;
  disabled: boolean;
  /** 全屏模式：更大的控件与聚焦环（BUGFIX #93 设计感） */
  expanded?: boolean;
  onChange: (v: string) => void;
  /** file 类型：选中文件回调 */
  onFilePick?: (f: File) => void;
}): JSX.Element {
  const fileInputRef = useRef<HTMLInputElement>(null);
  const commonStyle = {
    backgroundColor: disabled ? "#f3f3f3" : "#ffffff",
    borderColor: "#d4d4d4",
    color: "#202124",
  };
  const commonClass = expanded
    ? "w-full rounded-md border px-3 py-2 text-2xs outline-none transition-shadow focus:border-[#10a37f] focus:ring-2 focus:ring-[#10a37f26] disabled:opacity-60"
    : "w-full rounded border px-2 py-1 text-2xs outline-none focus:border-[#10a37f] disabled:opacity-60";
  if (field.type === "file") {
    // 证件/文件类字段（BUGFIX #85）：上传控件而非文本框；提交时自动入材料走审核
    return (
      <>
        <input
          ref={fileInputRef}
          type="file"
          className="hidden"
          onChange={(e) => {
            const f = e.target.files?.[0];
            if (f && onFilePick) onFilePick(f);
            e.target.value = "";
          }}
        />
        <button
          type="button"
          disabled={disabled}
          onClick={() => fileInputRef.current?.click()}
          className={
            expanded
              ? "w-full truncate rounded-md border border-dashed px-3 py-2 text-2xs transition-colors hover:border-[#10a37f] disabled:opacity-60"
              : "w-full truncate rounded border border-dashed px-2 py-1 text-2xs disabled:opacity-60"
          }
          style={{
            borderColor: fileName ? "#10a37f66" : "#c9d4d0",
            backgroundColor: fileName ? "#eef9f4" : "#fafcfb",
            color: fileName ? "#059669" : "#0451a5",
          }}
          title={field.hint || "选择文件，提交时自动交给专家审核"}
        >
          {fileName ? `📎 ${fileName}（点击更换）` : "📎 点击选择文件"}
        </button>
      </>
    );
  }
  if (field.type === "select") {
    return (
      <select
        value={value}
        disabled={disabled}
        onChange={(e) => onChange(e.target.value)}
        className={commonClass}
        style={commonStyle}
      >
        <option value="">请选择…</option>
        {(field.options ?? []).map((o) => (
          <option key={o} value={o}>
            {o}
          </option>
        ))}
      </select>
    );
  }
  if (field.type === "textarea") {
    return (
      <textarea
        value={value}
        disabled={disabled}
        rows={expanded ? 3 : 2}
        onChange={(e) => onChange(e.target.value)}
        placeholder={field.hint || undefined}
        className={commonClass}
        style={commonStyle}
      />
    );
  }
  return (
    <input
      type={field.type === "date" ? "date" : "text"}
      value={value}
      disabled={disabled}
      onChange={(e) => onChange(e.target.value)}
      placeholder={field.hint || undefined}
      className={commonClass}
      style={commonStyle}
    />
  );
}

/** 审核意见拆条（BUGFIX #86）：按换行拆，单段就整条展示（悬浮看全文） */
function splitReviewNotes(note: string): string[] {
  const lines = note
    .split(/\n+/)
    .map((s) => s.trim())
    .filter(Boolean);
  return lines.length > 0 ? lines : [];
}

function DraftFormCard({
  draft,
  expanded,
  onExpandedChange,
  taskMeta,
}: {
  draft: OpsCaseDraft;
  /** 全屏填写由面板持有：直开草稿只自动展开一次，页签切换不重复弹出 */
  expanded: boolean;
  onExpandedChange: (v: boolean) => void;
  /** 业务上下文：草稿 OCR 上传 / 草稿内文件随提交上传时补建档（BUGFIX #212） */
  taskMeta: OpsTaskStartMeta;
}): JSX.Element {
  const [values, setValues] = useState<Record<string, string>>(
    draft.values ?? {},
  );
  const [saving, setSaving] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [snapshotOpen, setSnapshotOpen] = useState(false);
  // file 类型字段选中的文件（提交时自动作为材料上传给出题专家）
  const [draftFiles, setDraftFiles] = useState<Record<string, File>>({});
  // 上传文件 OCR 自动填入（2026-11-05）：识别结果直接落到字段，可一键撤销
  const [ocrBusy, setOcrBusy] = useState(false);
  const [ocrNote, setOcrNote] = useState("");
  const [ocrError, setOcrError] = useState("");
  const [ocrPreview, setOcrPreview] = useState("");
  const [ocrPreviewOpen, setOcrPreviewOpen] = useState(false);
  const [ocrFilled, setOcrFilled] = useState<Set<string>>(() => new Set());
  const [ocrUndo, setOcrUndo] = useState<Record<string, string> | null>(null);
  const ocrInputRef = useRef<HTMLInputElement>(null);
  const linkedFile = useOpsCaseStore((s) =>
    s.files.find((f) => f.id === draft.file_id),
  );
  // 首次挂载时的非空值 = 自动预填（来自已上传材料要素）：标「自动预填」供核对（BUGFIX #79）
  const prefilledRef = useRef<Set<string>>(
    new Set(
      Object.entries(draft.values ?? {})
        .filter(([, v]) => String(v ?? "").trim())
        .map(([k]) => k),
    ),
  );
  // 逐字段显现动效（BUGFIX #79）：模拟表单逐步生成，等待感知更短
  const [visibleCount, setVisibleCount] = useState(0);
  useEffect(() => {
    if (visibleCount >= draft.template.length) return undefined;
    const t = setTimeout(() => setVisibleCount((v) => v + 1), 90);
    return () => clearTimeout(t);
  }, [visibleCount, draft.template.length]);
  const passed = draft.status === "passed";
  const rejected = !passed && linkedFile?.status === "rejected";
  // 草稿关联材料正在审核 → 草稿卡内展示 thinking（BUGFIX #86）
  const reviewingDraft = linkedFile?.status === "reviewing";
  /** 重新审核：审核长时间无响应时（BUGFIX #202）的自救出口 */
  const reReviewLinkedFile = (): void => {
    if (!linkedFile) return;
    void useOpsCaseStore.getState().reviewFile(linkedFile.id);
  };
  const reviewNotes = splitReviewNotes(linkedFile?.review_note ?? "");
  // file 字段：选中了文件或已有文件名即算已填
  const missing = draft.template.filter((f) => {
    if (!f.required) return false;
    if (f.type === "file") {
      return !draftFiles[f.name] && !(values[f.name] ?? "").trim();
    }
    return !(values[f.name] ?? "").trim();
  });

  const handleSave = async (): Promise<void> => {
    setSaving(true);
    await useOpsCaseStore.getState().saveDraft(draft.id, values);
    setSaving(false);
  };

  const handleSubmit = async (): Promise<void> => {
    setSubmitting(true);
    // file 字段选中的文件：先作为材料上传给出题专家（自动走 AI 审核）
    const picked = Object.values(draftFiles);
    if (picked.length > 0 && draft.team_id && draft.member_key) {
      await useOpsCaseStore
        .getState()
        .attachFiles(draft.team_id, draft.member_key, picked, taskMeta);
    }
    await useOpsCaseStore.getState().saveDraft(draft.id, values);
    await useOpsCaseStore.getState().submitDraft(draft.id);
    setSubmitting(false);
  };

  // 上传文件 → 本地 OCR/文档解析 → 模型抽字段 → 自动填入（覆盖已有值可撤销）
  const handleOcrPick = async (file: File | undefined): Promise<void> => {
    if (!file || ocrBusy) return;
    setOcrBusy(true);
    setOcrError("");
    setOcrNote("");
    setOcrPreview("");
    setOcrPreviewOpen(false);
    try {
      const r = await useOpsCaseStore.getState().ocrFillDraft(draft.id, file, taskMeta);
      const names = new Set(draft.template.map((f) => f.name));
      const incoming = Object.entries(r.values).filter(
        ([k, v]) => names.has(k) && v.trim(),
      );
      setOcrPreview(r.text_preview);
      if (incoming.length === 0) {
        setOcrNote(
          `已识别「${file.name}」（${r.text_chars} 字），但没找到能对应到表单的字段，请手动填写或换一份材料`,
        );
        return;
      }
      // 覆盖前留快照，支持一键撤销
      const overwritten = incoming.filter(
        ([k, v]) => (values[k] ?? "").trim() !== "" && values[k] !== v,
      ).length;
      setOcrUndo({ ...values });
      const merged = { ...values };
      for (const [k, v] of incoming) merged[k] = v;
      setValues(merged);
      setOcrFilled((s) => new Set([...s, ...incoming.map(([k]) => k)]));
      setOcrNote(
        `已从「${file.name}」自动填入 ${incoming.length} 项` +
          (overwritten > 0
            ? `（覆盖 ${overwritten} 项原有内容，可撤销）`
            : "") +
          ` · ${r.source === "llm" ? "模型抽取" : "规则兑底"}，请核对后提交` +
          // 凭证预览（2026-11-09）：底图已留档，点字段名能在图上框出来源区域
          (r.credential_image
            ? ` · 影像已归档到凭证「${r.credential_image.credential || "未归属"}」`
            : ""),
      );
      void useOpsCaseStore.getState().saveDraft(draft.id, merged);
    } catch (e) {
      setOcrError(e instanceof Error ? e.message : String(e));
    } finally {
      setOcrBusy(false);
      if (ocrInputRef.current) ocrInputRef.current.value = "";
    }
  };

  const handleOcrUndo = (): void => {
    if (!ocrUndo) return;
    setValues(ocrUndo);
    setOcrFilled(new Set());
    setOcrUndo(null);
    setOcrNote("已撤销本次 OCR 自动填入");
    void useOpsCaseStore.getState().saveDraft(draft.id, ocrUndo);
  };

  // 必填进度（全屏页头进度条 + 页脚提示，BUGFIX #93 设计感）
  const requiredFields = draft.template.filter((f) => f.required);
  const filledRequired = requiredFields.filter((f) =>
    f.type === "file"
      ? draftFiles[f.name] || (values[f.name] ?? "").trim()
      : (values[f.name] ?? "").trim(),
  ).length;
  const progressPct =
    requiredFields.length === 0
      ? 100
      : Math.round((filledRequired / requiredFields.length) * 100);

  const statusBadge = passed
    ? { label: "✓ 已验收 · 已入交付物", color: "#10a37f", bg: "#10a37f22" }
    : rejected
      ? {
          // 意见详情在「专家审核意见」区逐条展示（BUGFIX #86），徽标只做短标签
          label: "✗ 被打回 · 见下方意见",
          color: "#cd3131",
          bg: "#fdecec",
        }
      : draft.status === "submitted"
        ? { label: "⏳ 专家审核中", color: "#0451a5", bg: "#e8f0fb" }
        : { label: "✎ 填写中", color: "#6b7280", bg: "#f3f4f6" };

  // 字段网格（紧凑卡与全屏共用；全屏两列大控件，textarea 整行，BUGFIX #93）
  const fieldGrid = (
    <div
      className={
        expanded
          ? "grid grid-cols-2 gap-x-5 gap-y-4"
          : "grid grid-cols-2 gap-x-3 gap-y-1.5"
      }
    >
      {draft.template.slice(0, Math.max(visibleCount, 1)).map((f) => (
        <div key={f.name} className={f.type === "textarea" ? "col-span-2" : ""}>
          <label
            className={
              expanded
                ? "mb-1.5 flex items-center gap-1.5 text-[11px] font-medium"
                : "mb-0.5 block text-[10px]"
            }
            style={{ color: expanded ? "#374151" : "#616161" }}
          >
            {f.label}
            {f.required && <span style={{ color: "#cd3131" }}>*</span>}
            {prefilledRef.current.has(f.name) && (
              <span
                className="rounded px-1 text-[9px]"
                style={{ backgroundColor: "#e8f0fb", color: "#0451a5" }}
                title="已从你上传的材料中自动预填，请核对后提交"
              >
                {expanded ? "🔄 自动预填" : "自动预填"}
              </span>
            )}
            {ocrFilled.has(f.name) && (
              <span
                className="rounded px-1 text-[9px]"
                style={{ backgroundColor: "#e6f6f0", color: "#059669" }}
                title="由上传文件 OCR 识别自动填入，请核对后提交"
              >
                {expanded ? "📄 OCR 填入" : "OCR 填入"}
              </span>
            )}
          </label>
          <DraftFieldInput
            field={f}
            value={values[f.name] ?? ""}
            fileName={draftFiles[f.name]?.name}
            disabled={passed || saving || submitting}
            expanded={expanded}
            onChange={(v) => {
              setValues((prev) => ({ ...prev, [f.name]: v }));
              // 用户手工改过的字段撤掉 OCR 徽标（表示已人工确认）
              setOcrFilled((s) => {
                if (!s.has(f.name)) return s;
                const next = new Set(s);
                next.delete(f.name);
                return next;
              });
            }}
            onFilePick={(file) => {
              setDraftFiles((prev) => ({ ...prev, [f.name]: file }));
              setValues((prev) => ({ ...prev, [f.name]: file.name }));
            }}
          />
        </div>
      ))}
    </div>
  );

  // 审核意见块（BUGFIX #86）：全屏时升级为 Alert 卡片逐条完整展示，紧凑卡保持单行截断
  const notesBlock = !reviewingDraft && reviewNotes.length > 0 && (
    <div
      className={
        expanded ? "rounded-xl border p-4" : "mb-1.5 rounded border p-1.5"
      }
      style={{
        borderColor: rejected ? "#f3c8c8" : "#e3efe9",
        backgroundColor: rejected ? "#fdf3f3" : "#f2f7f5",
      }}
    >
      <div
        className={
          expanded
            ? "mb-1.5 flex items-center gap-1.5 text-[12px] font-semibold"
            : "mb-0.5 text-[10px] font-semibold"
        }
        style={{ color: rejected ? "#b91c1c" : "#374151" }}
      >
        {expanded && <span aria-hidden="true">⚠️</span>}
        专家审核意见（{reviewNotes.length} 条）
      </div>
      <div className={expanded ? "space-y-1.5" : "space-y-0.5"}>
        {reviewNotes.map((n, i) => (
          <div
            key={i}
            className={
              expanded
                ? "flex items-start gap-1.5 text-[11px]"
                : "truncate text-[10px]"
            }
            style={{ color: rejected ? "#7f1d1d" : "#374151" }}
            title={expanded ? undefined : n}
          >
            <span className="flex-shrink-0">{rejected ? "✗" : "✓"}</span>
            <span>{n}</span>
          </div>
        ))}
      </div>
    </div>
  );

  // 上传文件 OCR 自动填入条（所有专家表单通用，2026-11-05）：
  // 点按钮选文件 → 本地 OCR/文档解析 → 模型抽字段 → 自动填，可撤销/看识别原文。
  const ocrBar = !passed && (
    <div
      className={
        expanded ? "rounded-xl border p-4" : "mb-1.5 rounded border p-1.5"
      }
      style={{
        borderColor: ocrError ? "#f3c8c8" : "#e3efe9",
        backgroundColor: ocrError ? "#fdf3f3" : "#f7fbf9",
      }}
    >
      <input
        ref={ocrInputRef}
        type="file"
        accept=".png,.jpg,.jpeg,.webp,.bmp,.tif,.tiff,.pdf,.doc,.docx,.txt,.md,.csv,.html,.htm,.xlsx,.pptx"
        className="hidden"
        onChange={(e) => void handleOcrPick(e.target.files?.[0])}
      />
      <div className="flex flex-wrap items-center gap-2">
        <button
          type="button"
          onClick={() => ocrInputRef.current?.click()}
          disabled={ocrBusy || saving || submitting}
          className={
            expanded ? "btn btn-primary btn-md" : "btn btn-primary btn-sm"
          }
          title="上传已填好的纸质单据/证照照片、扫描件或电子文档，自动识别并填入表单"
        >
          {ocrBusy ? "⏳ 识别并提取中…" : "📄 上传文件 OCR 自动填入"}
        </button>
        <span className="text-[10px]" style={{ color: "#6b7280" }}>
          {ocrBusy
            ? "本地 OCR + 模型抽取，扫描件约需 10-30 秒，请稍候"
            : "支持图片 / PDF / Word / Excel / PPT / 文本；识别结果自动填入，可撤销"}
        </span>
        {ocrUndo && !ocrBusy && (
          <button
            type="button"
            onClick={handleOcrUndo}
            className="btn btn-ghost btn-xs"
            title="撤销本次识别填入的值（恢复到上传前）"
          >
            ↩ 撤销本次识别
          </button>
        )}
        {ocrPreview && (
          <button
            type="button"
            onClick={() => setOcrPreviewOpen((v) => !v)}
            className="btn btn-ghost btn-xs"
          >
            {ocrPreviewOpen ? "▾ 收起识别文本" : "▸ 查看识别文本"}
          </button>
        )}
      </div>
      {ocrError ? (
        <div
          className={expanded ? "mt-2 text-[11px]" : "mt-1 text-[10px]"}
          style={{ color: "#b91c1c" }}
        >
          识别失败：{ocrError}
        </div>
      ) : (
        ocrNote && (
          <div
            className={expanded ? "mt-2 text-[11px]" : "mt-1 text-[10px]"}
            style={{ color: "#059669" }}
          >
            {ocrNote}
          </div>
        )
      )}
      {ocrPreviewOpen && ocrPreview && (
        <pre
          className="mt-2 max-h-40 overflow-auto rounded border p-2 text-[10px] whitespace-pre-wrap"
          style={{
            borderColor: "#e7e5e4",
            backgroundColor: "#fafaf9",
            color: "#4b5563",
          }}
        >
          {ocrPreview}
          {ocrPreview.length >= 800 ? "\n…（仅预览前 800 字）" : ""}
        </pre>
      )}
    </div>
  );

  const submitTitle =
    missing.length > 0
      ? `必填项未完成：${missing.map((m) => m.label).join("、")}`
      : "提交后自动交给专家审核，通过即入交付物";
  const submitLabel = submitting
    ? "⏳ 提交审核中…"
    : rejected
      ? "修改后重新提交"
      : "提交给专家审核";

  // ============ 全屏模式（BUGFIX #93 设计重构）：页头 + 滚动表单区 + 常驻操作页脚 ============
  if (expanded) {
    const accent = passed
      ? "#10a37f"
      : rejected
        ? "#cd3131"
        : draft.status === "submitted"
          ? "#0451a5"
          : "#9ca3af";
    return (
      <div
        className="fixed inset-0 z-40 flex flex-col"
        style={{ backgroundColor: "#f4f4f2" }}
      >
        {/* 页头：状态强调色条 + 标题副题 + 必填进度 + 状态徽标 + 退出 */}
        <div
          className="flex-shrink-0 border-b bg-white"
          style={{ borderColor: "#e5e5e2", borderTop: `3px solid ${accent}` }}
        >
          <div className="mx-auto flex max-w-4xl items-center gap-3 px-6 py-3">
            <span
              className="flex h-9 w-9 flex-shrink-0 items-center justify-center rounded-lg text-[16px]"
              style={{ backgroundColor: "#f2f7f5" }}
              aria-hidden="true"
            >
              📝
            </span>
            <div className="min-w-0 flex-1">
              <div
                className="truncate text-[13px] font-semibold"
                style={{ color: "#1f1f1f" }}
              >
                {draft.title}
              </div>
              <div className="mt-0.5 text-[10px]" style={{ color: "#9ca3af" }}>
                {draft.member_key ? `出题专家：${draft.member_key} · ` : ""}共{" "}
                {draft.template.length} 项
                {requiredFields.length > 0
                  ? ` · 必填 ${requiredFields.length} 项`
                  : ""}
              </div>
            </div>
            {requiredFields.length > 0 && (
              <div
                className="flex flex-shrink-0 items-center gap-1.5"
                title={`必填进度：${filledRequired}/${requiredFields.length}`}
              >
                <div
                  className="h-1.5 w-24 overflow-hidden rounded-full"
                  style={{ backgroundColor: "#e7e5e4" }}
                >
                  <div
                    className="h-full rounded-full transition-all"
                    style={{
                      width: `${progressPct}%`,
                      backgroundColor:
                        progressPct === 100 ? "#10a37f" : "#0451a5",
                    }}
                  />
                </div>
                <span className="text-[10px]" style={{ color: "#6b7280" }}>
                  {filledRequired}/{requiredFields.length}
                </span>
              </div>
            )}
            <span
              className="flex-shrink-0 rounded-full px-2 py-0.5 text-[10px] font-semibold"
              style={{
                backgroundColor: statusBadge.bg,
                color: statusBadge.color,
              }}
              title={linkedFile?.review_note || undefined}
            >
              {statusBadge.label}
            </span>
            <button
              type="button"
              onClick={() => onExpandedChange(false)}
              className="flex-shrink-0 rounded-md border px-2 py-1 text-[10px] transition-colors hover:bg-[#f5f5f4]"
              style={{
                borderColor: "#d4d4d4",
                color: "#616161",
                backgroundColor: "#ffffff",
              }}
              title="退出全屏填写"
            >
              ✕ 退出全屏
            </button>
          </div>
        </div>
        {/* 主体：居中表单卡 + 意见 Alert + 打回对照 */}
        <div className="min-h-0 flex-1 overflow-y-auto px-6 py-5">
          <div className="mx-auto max-w-4xl space-y-3">
            {reviewingDraft && (
              <div
                className="rounded-xl border bg-white p-3"
                style={{ borderColor: "#e3efe9" }}
              >
                <div className="flex items-center gap-2">
                  <div className="min-w-0 flex-1">
                    <AiThinkingIndicator
                      compact
                      label={`${draft.member_key || "专家"}正在审核草稿`}
                    />
                  </div>
                  {/* BUGFIX #202：审核长时间无响应时给出自救出口，不让用户对着转圈干等 */}
                  <button
                    type="button"
                    onClick={reReviewLinkedFile}
                    className="btn btn-secondary btn-sm flex-shrink-0"
                    title="重新发起审核（审核长时间无响应时可用）"
                  >
                    ↻ 重新审核
                  </button>
                </div>
              </div>
            )}
            {notesBlock}
            {ocrBar}
            <div
              className="rounded-xl border bg-white p-6"
              style={{
                borderColor: "#e7e5e4",
                boxShadow: "0 1px 3px rgba(0,0,0,0.05)",
              }}
            >
              {fieldGrid}
            </div>
            {/* 打回重提对照（BUGFIX #79）：展开上次提交内容，知道自己改哪里 */}
            {rejected && draft.last_snapshot && (
              <div
                className="rounded-xl border bg-white p-4"
                style={{ borderColor: "#f0efed" }}
              >
                <button
                  type="button"
                  onClick={() => setSnapshotOpen((v) => !v)}
                  className="text-[11px] font-medium"
                  style={{ color: "#0451a5" }}
                >
                  {snapshotOpen
                    ? "▾ 收起上次提交内容"
                    : `▸ 对照上次提交内容（第 ${draft.submit_count ?? 1} 次提交）`}
                </button>
                {snapshotOpen && (
                  <pre
                    className="mt-2 max-h-56 overflow-auto rounded-md border p-3 text-[11px] whitespace-pre-wrap"
                    style={{
                      borderColor: "#f0efed",
                      backgroundColor: "#fafaf9",
                      color: "#4b5563",
                    }}
                  >
                    {draft.last_snapshot}
                  </pre>
                )}
              </div>
            )}
          </div>
        </div>
        {/* 页脚：常驻操作栏（进度提示 + 暂存/提交） */}
        {!passed && (
          <div
            className="flex-shrink-0 border-t bg-white"
            style={{ borderColor: "#e5e5e2" }}
          >
            <div className="mx-auto flex max-w-4xl items-center gap-3 px-6 py-3">
              {missing.length > 0 ? (
                <span
                  className="min-w-0 truncate text-[10px]"
                  style={{ color: "#b25c1a" }}
                >
                  ⚠ 还有 {missing.length} 项必填未完成：
                  {missing.map((m) => m.label).join("、")}
                </span>
              ) : (
                <span className="text-[10px]" style={{ color: "#10a37f" }}>
                  ✓ 必填项已完成，可提交专家审核
                </span>
              )}
              <div className="ml-auto flex flex-shrink-0 items-center gap-2">
                <button
                  type="button"
                  onClick={() => void handleSave()}
                  disabled={saving || submitting}
                  className="btn btn-secondary btn-md"
                >
                  {saving ? "保存中…" : "暂存"}
                </button>
                <button
                  type="button"
                  onClick={() => void handleSubmit()}
                  disabled={submitting || missing.length > 0}
                  className="btn btn-success btn-md"
                  title={submitTitle}
                >
                  {submitLabel}
                </button>
              </div>
            </div>
          </div>
        )}
      </div>
    );
  }

  // ============ 紧凑卡模式：专家卡内小空间，保持原有密度 ============
  return (
    <div
      className="rounded-lg border p-2.5"
      style={{
        borderColor: passed ? "#10a37f66" : rejected ? "#e8b4b4" : "#e0e0e0",
        backgroundColor: "#ffffff",
      }}
    >
      {/* 审核中：草稿卡内 thinking 状态，不再把意见散在材料行里（BUGFIX #86） */}
      {reviewingDraft && (
        <div className="mb-1.5">
          <AiThinkingIndicator
            compact
            label={`${draft.member_key || "专家"}正在审核草稿`}
          />
          {/* BUGFIX #202：审核长时间无响应时给出自救出口 */}
          <button
            type="button"
            onClick={reReviewLinkedFile}
            className="mt-1 text-[10px]"
            style={{ color: "#0451a5" }}
            title="重新发起审核（审核长时间无响应时可用）"
          >
            ↻ 重新审核
          </button>
        </div>
      )}
      {notesBlock}
      <div className="mb-1.5 flex items-center gap-2">
        <span className="text-2xs font-semibold" style={{ color: "#1f1f1f" }}>
          {draft.title}
        </span>
        <span className="text-[10px]" style={{ color: "#9ca3af" }}>
          {draft.member_key ? `出题专家：${draft.member_key}` : ""}
        </span>
        <button
          type="button"
          onClick={() => onExpandedChange(true)}
          className="ml-auto flex-shrink-0 btn btn-secondary btn-xs"
          title="全屏填写（空间更大）"
        >
          ⛶ 全屏
        </button>
        <span
          className="flex-shrink-0 rounded-full px-2 py-0.5 text-[10px] font-semibold"
          style={{ backgroundColor: statusBadge.bg, color: statusBadge.color }}
          title={linkedFile?.review_note || undefined}
        >
          {statusBadge.label}
        </span>
      </div>
      {ocrBar}
      {fieldGrid}
      {/* 打回重提对照（BUGFIX #79）：展开上次提交内容，知道自己改哪里 */}
      {rejected && draft.last_snapshot && (
        <div className="mt-1.5">
          <button
            type="button"
            onClick={() => setSnapshotOpen((v) => !v)}
            className="text-[10px]"
            style={{ color: "#0451a5" }}
          >
            {snapshotOpen
              ? "▾ 收起上次提交内容"
              : `▸ 对照上次提交内容（第 ${draft.submit_count ?? 1} 次提交）`}
          </button>
          {snapshotOpen && (
            <pre
              className="mt-1 max-h-40 overflow-auto rounded border p-2 text-[10px] whitespace-pre-wrap"
              style={{
                borderColor: "#f0efed",
                backgroundColor: "#fafaf9",
                color: "#4b5563",
              }}
            >
              {draft.last_snapshot}
            </pre>
          )}
        </div>
      )}
      {!passed && (
        <div className="mt-2 flex items-center gap-2">
          <button
            type="button"
            onClick={() => void handleSave()}
            disabled={saving || submitting}
            className="btn btn-secondary btn-sm"
          >
            {saving ? "保存中…" : "暂存"}
          </button>
          <button
            type="button"
            onClick={() => void handleSubmit()}
            disabled={submitting || missing.length > 0}
            className="btn btn-success btn-sm"
            title={submitTitle}
          >
            {submitLabel}
          </button>
          {missing.length > 0 && (
            <span className="text-[10px]" style={{ color: "#b25c1a" }}>
              还有 {missing.length} 项必填未完成
            </span>
          )}
        </div>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// 单个材料文件行：状态徽标 + 专家口吻审核意见气泡 + 重审/人工改判/删除
// ---------------------------------------------------------------------------

function CaseFileRow({
  file,
  expertName,
  draftLinked = false,
  onOpenCredential,
}: {
  file: OpsCaseFile;
  expertName: string;
  /** 草稿提交生成的材料：审核意见在草稿卡展示，此处不再重复气泡（BUGFIX #86） */
  draftLinked?: boolean;
  /** 打开凭证预览并定位到本材料所属节点 */
  onOpenCredential?: (nodeKey: string) => void;
}): JSX.Element {
  const [noteExpanded, setNoteExpanded] = useState(false);
  const [marksOpen, setMarksOpen] = useState(false);
  /** 审核明细默认收起（2026-11 交互优化）：材料行只留「结论 + 结果 + 两个入口」，
      步骤链 / 关键要素 / 证据链 / 人工干预点「查看审核明细」才展开 ——
      否则一张材料（尤其被打回的）就能把整张专家卡撑满。 */
  const [detailOpen, setDetailOpen] = useState(false);
  const meta = CASE_FILE_STATUS_META[file.status];
  const overrideFile = useOpsCaseStore((s) => s.overrideFile);
  const deleteFile = useOpsCaseStore((s) => s.deleteFile);
  const reviewFile = useOpsCaseStore((s) => s.reviewFile);
  /** 行内警示：低置信要素数 + 合规风险点（收起状态下也要能看到，否则等于没提醒） */
  const lowConfCount = (file.extracted_fields ?? []).filter(
    (f) => f.confidence < 0.6,
  ).length;
  const riskCount = file.risk_report?.findings?.length ?? 0;
  /** 结果行：直接给结论/原因，不展开也能看到；
      草稿材料的意见在草稿卡展示，这里只给指引，避免同一段话出现两次；
      意见与结论徽标同文（如都是「通过」）时不重复铺框。 */
  const noteText = (file.review_note ?? "").trim();
  const showNote =
    !!noteText &&
    file.status !== "reviewing" &&
    !draftLinked &&
    noteText !== meta.label;
  const resultText = showNote
    ? noteText
    : file.status === "reviewing"
      ? "专家审核中…"
      : file.status === "pending"
        ? "待专家审核"
        : draftLinked
          ? "审核意见见草稿卡"
          : "";
  /** 结果行配色与结论状态一致（打回红 / 通过绿 / 待审橙） */
  const resultAccent =
    file.status === "rejected"
      ? "#cd3131"
      : file.status === "passed"
        ? "#10a37f"
        : "#b25c1a";
  const resultBg =
    file.status === "rejected"
      ? "#fdf5f5"
      : file.status === "passed"
        ? "#f4f8f7"
        : "#fdfaf5";

  return (
    <div
      className="rounded-lg border px-2 py-1"
      style={{ borderColor: "#e7e5e4", backgroundColor: "#fafaf9" }}
    >
      {/* 摘要行：结论徽标 + 待核提示（点文件名同样可展开审核明细） */}
      <div className="flex items-center gap-1">
        <button
          type="button"
          onClick={() => setDetailOpen((v) => !v)}
          className="flex min-w-0 flex-1 items-center gap-1 text-left"
          title={detailOpen ? "收起审核明细" : "查看审核明细"}
        >
          <span
            className="flex-shrink-0 text-[9px]"
            style={{ color: "#9ca3af" }}
            aria-hidden="true"
          >
            {detailOpen ? "▾" : "▸"}
          </span>
          <span
            className="min-w-0 truncate text-2xs"
            style={{ color: "#202124" }}
          >
            {file.file_name}
          </span>
        </button>
        <span
          className="flex-shrink-0 rounded-full px-1.5 text-[10px] font-semibold"
          style={{ backgroundColor: `${meta.color}18`, color: meta.color }}
        >
          {meta.icon} {meta.label}
          {file.reviewed_by === "ai"
            ? " · AI"
            : file.reviewed_by === "human"
              ? " · 人工"
              : ""}
        </span>
        {lowConfCount > 0 && (
          <span
            className="flex-shrink-0 text-[10px] font-semibold"
            style={{ color: "#cd3131" }}
            title={`${lowConfCount} 项低置信要素，展开审核明细可逐项核对`}
          >
            ⚠{lowConfCount} 待核
          </span>
        )}
      </div>
      {/* 结果行（默认可见）：直接显示结论/原因，长文点开看全文 */}
      {resultText && (
        <div
          onClick={showNote ? () => setNoteExpanded((v) => !v) : undefined}
          className="mt-1 whitespace-pre-wrap rounded border-l-2 px-1.5 py-1 text-[10px]"
          style={{
            borderLeftColor: resultAccent,
            backgroundColor: resultBg,
            color: "#374151",
            cursor: showNote ? "pointer" : "default",
            display: "-webkit-box",
            WebkitLineClamp: noteExpanded ? undefined : 2,
            WebkitBoxOrient: "vertical",
            overflow: "hidden",
          }}
          title={
            showNote ? `${expertName} 的审核意见，点击展开/收起` : undefined
          }
        >
          {resultText}
        </div>
      )}
      {/* 入口行：系统合规审核风险（独立按钮）+ 查看审核明细 */}
      <div className="mt-1 flex flex-wrap items-center gap-1.5">
        {/* 系统合规审核风险入口（2026-11-05）：有风险点才显示，点开回看报告与原文证据 */}
        {riskCount > 0 && (
          <button
            type="button"
            onClick={() => useOpsCaseStore.getState().showRiskAudit(file.id)}
            className="btn btn-xs btn-danger"
            title="查看系统合规审核报告（风险点 + 原文证据）"
          >
            ⚠ 合规风险 {riskCount}
          </button>
        )}
        {/* 凭证入口（2026-11-09）：已挂到凭证节点的材料可直接看影像与要素定位 */}
        {!!file.credential && onOpenCredential && (
          <button
            type="button"
            onClick={() =>
              onOpenCredential(
                credentialNodeKey(
                  file.team_id,
                  file.member_key,
                  file.credential ?? "",
                ),
              )
            }
            className="text-[10px] font-semibold"
            style={{ color: "#059669" }}
            title={`已挂到凭证「${file.credential}」· 查看影像、点要素名定位高亮区域`}
          >
            🪪 {file.credential}
          </button>
        )}
        <button
          type="button"
          onClick={() => setDetailOpen((v) => !v)}
          className="text-[10px]"
          style={{ color: "#0451a5" }}
          title="查看审核步骤链 / 关键要素 / 证据链 / 人工干预入口"
        >
          {detailOpen ? "收起审核明细 ▴" : "查看审核明细 ▾"}
        </button>
      </div>
      {detailOpen && (
        <div className="mt-1 space-y-1">
          {/* 审核步骤链：接收→解析→提取→结论，过程可见（BUGFIX #79） */}
          <ReviewSteps file={file} />
          {/* 关键要素卡（厚资料变薄数据，2026-08-10）：低置信标红置顶，员工只核标红项 */}
          {(file.extracted_fields?.length ?? 0) > 0 && (
            <div
              className="mt-1 rounded-lg border px-2 py-1"
              style={{ borderColor: "#f0efed", backgroundColor: "#ffffff" }}
            >
              <div
                className="mb-0.5 text-[10px] font-semibold"
                style={{ color: "#6b7280" }}
              >
                关键要素
                <span className="ml-1 font-normal" style={{ color: "#9ca3af" }}>
                  （标红项置信度低，请重点核对）
                </span>
              </div>
              <div className="flex flex-wrap gap-1">
                {[...(file.extracted_fields ?? [])]
                  .sort((a, b) => a.confidence - b.confidence)
                  .map((f, i) => {
                    const low = f.confidence < 0.6;
                    return (
                      <span
                        key={`${f.field}-${i}`}
                        className="rounded px-1.5 py-0.5 text-[10px]"
                        style={{
                          backgroundColor: low ? "#fdf0f0" : "#f3f6f5",
                          border: `1px solid ${low ? "#e8b4b4" : "#e2e8e6"}`,
                          color: low ? "#cd3131" : "#374151",
                        }}
                        title={`置信度 ${(f.confidence * 100).toFixed(0)}%${low ? " · 低置信，请人工核对" : ""}`}
                      >
                        {low && "⚠ "}
                        {f.field}：{f.value}
                      </span>
                    );
                  })}
              </div>
            </div>
          )}
          {/* 证据链（防黑盒不信任）：审核依据的原文摘录，员工可自己验真 */}
          {(file.evidence?.length ?? 0) > 0 && (
            <div className="mt-1 space-y-0.5">
              <div
                className="text-[10px] font-semibold"
                style={{ color: "#6b7280" }}
              >
                审核依据（原文摘录）
              </div>
              {(file.evidence ?? []).map((s, i) => (
                <div
                  key={i}
                  className="border-l-2 pl-1.5 text-[10px]"
                  style={{ borderColor: "#10a37f66", color: "#4b5563" }}
                >
                  “{s}”
                </div>
              ))}
            </div>
          )}
          {/* 人工干预（改判/重审/打回定位）收在详情区，避免摘要行被按钮挤爆 */}
          <div className="flex flex-wrap items-center gap-2 pt-0.5">
            {(file.status === "pending" ||
              file.status === "rejected" ||
              file.status === "reviewing") && (
              <button
                type="button"
                onClick={() => void reviewFile(file.id)}
                className="text-[10px]"
                style={{ color: "#0451a5" }}
                title="让 AI 专家重新审核（审核长时间无响应时可用此重试）"
              >
                ↻ 重审
              </button>
            )}
            {file.status === "rejected" &&
              (file.reject_marks?.length ?? 0) > 0 && (
                <button
                  type="button"
                  onClick={() => setMarksOpen(true)}
                  className="text-[10px] font-semibold"
                  style={{ color: "#cd3131" }}
                  title="在文档内高亮打回位置与修改建议"
                >
                  📍 {file.reject_marks!.length} 处打回
                </button>
              )}
            {file.status !== "passed" && (
              <button
                type="button"
                onClick={() =>
                  void overrideFile(file.id, "passed", "人工验收通过")
                }
                className="text-[10px]"
                style={{ color: "#059669" }}
                title="人工改判为通过"
              >
                ✓ 通过
              </button>
            )}
            {file.status !== "rejected" && (
              <button
                type="button"
                onClick={() =>
                  void overrideFile(file.id, "rejected", "人工打回")
                }
                className="text-[10px]"
                style={{ color: "#b25c1a" }}
                title="人工改判为打回"
              >
                ✗ 打回
              </button>
            )}
            <button
              type="button"
              onClick={() => void deleteFile(file.id)}
              className="text-[10px]"
              style={{ color: "#cd3131" }}
              title="删除材料"
            >
              ✕ 删除
            </button>
          </div>
        </div>
      )}
      {marksOpen && (
        <RejectMarkPreview file={file} onClose={() => setMarksOpen(false)} />
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// 打回定位预览（BUGFIX #80，Documenso 式）：文档内高亮问题行 + 逐条修改建议
// ---------------------------------------------------------------------------

function RejectMarkPreview({
  file,
  onClose,
}: {
  file: OpsCaseFile;
  onClose: () => void;
}): JSX.Element {
  const [content, setContent] = useState<string | null>(null);
  const [failed, setFailed] = useState(false);
  const marks = file.reject_marks ?? [];

  useEffect(() => {
    let mounted = true;
    ipc
      .opsCaseFileContent(file.id)
      .then((r) => {
        if (!mounted) return;
        const bytes = Uint8Array.from(atob(r.content_base64), (c) =>
          c.charCodeAt(0),
        );
        setContent(new TextDecoder("utf-8").decode(bytes));
      })
      .catch(() => {
        if (mounted) setFailed(true);
      });
    return () => {
      mounted = false;
    };
  }, [file.id]);

  // 空白容忍匹配：行内去掉空白后包含 mark.quote（去空白）即命中
  const hitMark = (line: string): { quote: string; advice: string } | null => {
    const norm = line.replace(/\s+/g, "");
    if (!norm) return null;
    for (const m of marks) {
      const q = m.quote.replace(/\s+/g, "");
      if (q && norm.includes(q)) return m;
    }
    return null;
  };

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center"
      style={{ backgroundColor: "rgba(0,0,0,0.4)" }}
      onClick={onClose}
    >
      <div
        className="flex max-h-[85%] w-[680px] flex-col rounded-lg shadow-2xl"
        style={{ backgroundColor: "#ffffff", border: "1px solid #d4d4d4" }}
        onClick={(e) => e.stopPropagation()}
      >
        <div
          className="flex flex-shrink-0 items-center justify-between border-b px-3 py-2"
          style={{ borderColor: "#e0e0e0", backgroundColor: "#fdf5f5" }}
        >
          <span
            className="truncate text-2xs font-semibold"
            style={{ color: "#cd3131" }}
          >
            📍 打回位置：{file.file_name}
            <span className="ml-1.5 font-normal" style={{ color: "#9ca3af" }}>
              红色高亮即问题处，按建议修改后重传或重审
            </span>
          </span>
          <button
            type="button"
            onClick={onClose}
            className="rounded px-1.5 text-2xs hover:bg-vscode-border"
            style={{ color: "#616161" }}
          >
            ✕
          </button>
        </div>

        {failed && (
          <div
            className="p-4 text-center text-2xs"
            style={{ color: "#cd3131" }}
          >
            文档内容读取失败，无法定位打回位置
          </div>
        )}
        {!failed && content === null && (
          <div
            className="p-4 text-center text-2xs"
            style={{ color: "#9ca3af" }}
          >
            正在读取文档…
          </div>
        )}

        {content !== null && (
          <div className="flex-1 overflow-auto p-3">
            {content.split("\n").map((line, i) => {
              const hit = hitMark(line);
              return (
                <div
                  key={i}
                  className="rounded px-1.5 py-px text-2xs whitespace-pre-wrap"
                  style={
                    hit
                      ? {
                          backgroundColor: "#fdecec",
                          border: "1px solid #e8b4b4",
                          color: "#7f1d1d",
                        }
                      : { color: "#374151" }
                  }
                >
                  {line || "\u00A0"}
                  {hit?.advice && (
                    <span
                      className="ml-2 rounded px-1 text-[9px]"
                      style={{ backgroundColor: "#cd3131", color: "#ffffff" }}
                    >
                      建议：{hit.advice}
                    </span>
                  )}
                </div>
              );
            })}
          </div>
        )}

        {/* 底部：逐条打回清单（文档内未命中时也能看到建议） */}
        <div
          className="flex-shrink-0 border-t p-2"
          style={{ borderColor: "#e0e0e0", backgroundColor: "#fafafa" }}
        >
          {marks.map((m, i) => (
            <div key={i} className="mb-1 flex items-start gap-1.5 text-[10px]">
              <span
                className="flex-shrink-0 font-semibold"
                style={{ color: "#cd3131" }}
              >
                {i + 1}.
              </span>
              <span style={{ color: "#4b5563" }}>
                “{m.quote}”
                {m.advice && (
                  <span style={{ color: "#b25c1a" }}> → {m.advice}</span>
                )}
              </span>
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}
