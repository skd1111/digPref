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
import {
  CASE_FILE_STATUS_META,
  type OpsCaseDraft,
  type OpsCaseFile,
  type OpsCaseQa,
  type OpsDraftField,
} from "@/types/ops";
import type { ExpertMember, ExpertTeam } from "@/types/expertTeam";
import type { Feature } from "@/types/biznav";
import type { findOpsItem } from "@/components/ops/opsModules";

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

/** 剪贴板粘贴的文件命名美化：截图类通用名加时间戳，避免多张同名互盖 */
function renamePastedFile(f: File): File {
  const generic = /^(image|截图|粘贴的图片|blob)\.(png|jpg|jpeg|gif|webp)$/i.test(f.name);
  if (!generic) return f;
  const ts = new Date()
    .toTimeString()
    .slice(0, 8)
    .replace(/:/g, "");
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
  const selectedItemId = selection?.featureHit?.id ?? selection?.staticHit?.item.id ?? null;
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
        <div className="mb-2 text-3xl">👥</div>
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
}: ExpertWorkflowPanelProps & { selection: NonNullable<ExpertWorkflowPanelProps["selection"]> }): JSX.Element {
  const teams = useExpertTeamStore((s) => s.teams);
  const selectedTeamIds = useExpertTeamStore((s) => s.selectedTeamIds);
  const recommending = useExpertTeamStore((s) => s.recommending);
  const caseId = useOpsCaseStore((s) => s.case_id);
  const files = useOpsCaseStore((s) => s.files);
  const qa = useOpsCaseStore((s) => s.qa);
  const drafts = useOpsCaseStore((s) => s.drafts);
  const busyMembers = useOpsCaseStore((s) => s.busyMembers);
  const exporting = useOpsCaseStore((s) => s.exporting);
  const caseError = useOpsCaseStore((s) => s.error);
  const unreadByTeam = useOpsCaseStore((s) => s.unreadByTeam);
  const [checkedOutputs, setCheckedOutputs] = useState<Set<string>>(() => new Set());
  const [presetOpen, setPresetOpen] = useState(false);
  const [activeTeamId, setActiveTeamId] = useState<string>("");

  const featureName =
    selection.featureHit?.name ?? selection.staticHit?.item.label ?? "";

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
    () => (activeTeam ? activeTeam.members.map((m) => ({ team: activeTeam, member: m })) : []),
    [activeTeam],
  );

  // 交付物柜（BUGFIX #79）：通过验收的材料聚合展示，可预览/单独另存
  const [cabinetOpen, setCabinetOpen] = useState(false);
  const passedFiles = useMemo(() => files.filter((f) => f.status === "passed"), [files]);

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
      if (target && (target.tagName === "INPUT" || target.tagName === "TEXTAREA")) return;
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
        .attachFiles(activeTeam.id, lead.name, files)
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
    () => selectedTeams.flatMap((t) => t.members.map((m) => ({ team: t, member: m }))),
    [selectedTeams],
  );

  // 单个专家是否验收完成：交付标准全部逐项确认
  const isMemberAccepted = (teamId: string, member: ExpertMember): boolean => {
    const outputs = member.outputs ?? [];
    if (outputs.length === 0) return false;
    return outputs.every((o) => checkedOutputs.has(`${teamId}::${member.name}::${o}`));
  };
  const acceptedCount = allMembers.filter((r) =>
    isMemberAccepted(r.team.id, r.member),
  ).length;
  const allAccepted = allMembers.length > 0 && acceptedCount === allMembers.length;

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
    <div className="relative flex h-full min-h-0 flex-col" style={{ backgroundColor: "#f7f7f6" }}>
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
            📦 交付物柜{passedFiles.length > 0 ? ` ${passedFiles.length}` : ""}
          </button>
          <button
            type="button"
            onClick={() => void handleClearCase()}
            className="rounded border px-2 py-0.5 text-2xs"
            style={{ borderColor: "#e0e0e0", color: "#b25c1a", backgroundColor: "#ffffff" }}
            title="清空当前业务的全部材料/问答/草稿，重新开始办理"
          >
            ↺ 重新开始办理
          </button>
          <button
            type="button"
            onClick={() => setPresetOpen((v) => !v)}
            className="rounded border px-2 py-0.5 text-2xs"
            style={{ borderColor: "#e0e0e0", color: "#616161", backgroundColor: "#ffffff" }}
            title="为该业务预设专家团（选中时自动选团）"
          >
            ⚙ 专家团预设
          </button>
        </div>
        <div className="mt-1 flex items-center gap-2">
          <ExpertTeamSelector />
        </div>
        {presetOpen && (
          <TeamPresetEditor selection={selection} teams={teams} onSave={onSaveTeamPreset} />
        )}
        {caseError && (
          <div className="mt-1 text-2xs" style={{ color: "#cd3131" }}>
            ⚠ {caseError}
          </div>
        )}
      </div>

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
      <div className="min-h-0 flex-1 overflow-x-auto overflow-y-hidden">
        {allMembers.length === 0 ? (
          recommending ? (
            // AI 推荐进行中（LLM 数秒）：展示思考动效，避免界面像卡住
            <div className="flex h-full flex-col items-center justify-center gap-2 p-3">
              <AiThinkingIndicator compact label={`专家团准备中 · 正在为「${featureName}」匹配专家`} />
              <div className="text-2xs" style={{ color: "#9ca3af" }}>
                本地 → 内网 → 云端三级匹配，通常需要几秒…
              </div>
            </div>
          ) : (
            <div className="flex h-full items-center justify-center p-3">
              <div
                className="rounded-lg border px-4 py-6 text-center text-2xs"
                style={{ borderColor: "#e0e0e0", backgroundColor: "#ffffff", color: "#9ca3af" }}
              >
                当前未选择专家团
                <div className="mt-1">
                  在上方专家团选择器中手动指定，或为业务预设专家团（AI 也会自动推荐）
                </div>
              </div>
            </div>
          )
        ) : (
          <div className="flex h-full items-stretch gap-3 p-3">
            {activeMembers.map((ref) => (
              <ExpertCaseCard
                key={`${ref.team.id}::${ref.member.name}`}
                refData={ref}
                caseId={caseId}
                files={files.filter((f) => f.member_key === ref.member.name)}
                draftFileIds={draftFileIds}
                qa={qa.filter((q) => q.member_key === ref.member.name)}
                busy={busyMembers[ref.member.name]}
                accepted={isMemberAccepted(ref.team.id, ref.member)}
                checkedOutputs={checkedOutputs}
                onToggleOutput={(o) => toggleOutput(ref, o)}
              />
            ))}
          </div>
        )}
      </div>

      {/* ===== 交付草稿（BUGFIX #78）：要「模板/清单」不给一大段文字，界面直填 ===== */}
      {drafts.length > 0 && (
        <div
          className="flex-shrink-0 overflow-y-auto border-t px-3 py-2"
          style={{ maxHeight: "55%", borderColor: "#e0e0e0", backgroundColor: "#fafaf9" }}
        >
          <div className="mb-1.5 flex items-center gap-2">
            <span className="text-2xs font-semibold" style={{ color: "#1f1f1f" }}>
              📝 交付草稿
            </span>
            <span className="text-[10px]" style={{ color: "#9ca3af" }}>
              直接填写 → 提交给专家审核 → 通过后自动并入交付物
            </span>
          </div>
          {/* 草稿页签：一份草稿一个页签，不再上下接龙（BUGFIX #87） */}
          <div className="mb-1.5 flex items-end gap-1 overflow-x-auto">
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
          {activeDraft && <DraftFormCard key={activeDraft.id} draft={activeDraft} />}
        </div>
      )}

      {/* ===== 底部完成条：交叉比对提醒 + 生成交付物并导出 ===== */}
      <div
        className="flex flex-shrink-0 items-center gap-3 border-t px-4 py-2"
        style={{ borderColor: "#e0e0e0", backgroundColor: "#ffffff" }}
      >
        <span className="min-w-0 flex-1 truncate text-2xs" style={{ color: "#616161" }}>
          {allAccepted
            ? "所有专家已验收通过，业务办理完成"
            : `还有 ${allMembers.length - acceptedCount} 位专家未验收（交付标准逐项确认后才算验收）`}
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
            backgroundColor: "#10a37f",
            color: "#ffffff",
            opacity: !allAccepted || exporting || files.length === 0 ? 0.5 : 1,
            cursor: exporting ? "wait" : allAccepted && files.length > 0 ? "pointer" : "not-allowed",
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
        <DeliverableCabinet files={passedFiles} onClose={() => setCabinetOpen(false)} />
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
  checkedOutputs,
  onToggleOutput,
}: {
  refData: MemberRef;
  caseId: string;
  files: OpsCaseFile[];
  /** 草稿提交生成的材料行 id（审核意见在草稿卡展示，BUGFIX #86） */
  draftFileIds: Set<string>;
  qa: OpsCaseQa[];
  busy: "uploading" | "reviewing" | "asking" | undefined;
  accepted: boolean;
  checkedOutputs: Set<string>;
  onToggleOutput: (output: string) => void;
}): JSX.Element {
  const { team, member } = refData;
  const fileInputRef = useRef<HTMLInputElement>(null);
  const [question, setQuestion] = useState("");
  const [dragOver, setDragOver] = useState(false);
  const outputs = member.outputs ?? [];
  const passedFiles = files.filter((f) => f.status === "passed").length;
  const rejectedFiles = files.filter((f) => f.status === "rejected").length;

  const handlePickFiles = async (list: FileList | null): Promise<void> => {
    if (!list || list.length === 0) return;
    await useOpsCaseStore.getState().attachFiles(team.id, member.name, [...list]);
    if (fileInputRef.current) fileInputRef.current.value = "";
  };

  // P0 消灭二次搬运（2026-08-10）：拖拽文件到专家卡直接入件，免去点按钮+选择器
  const handleDrop = async (e: React.DragEvent): Promise<void> => {
    e.preventDefault();
    setDragOver(false);
    const dropped = [...(e.dataTransfer?.files ?? [])];
    if (dropped.length > 0) {
      await useOpsCaseStore.getState().attachFiles(team.id, member.name, dropped);
    }
  };

  const handleAsk = async (): Promise<void> => {
    const q = question.trim();
    if (!q) return;
    setQuestion("");
    await useOpsCaseStore.getState().askExpert(team.id, member.name, q);
  };

  return (
    <div
      className="flex w-[360px] flex-shrink-0 flex-col overflow-hidden rounded-xl border"
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
            <span className="truncate text-ui font-semibold" style={{ color: "#1f1f1f" }}>
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
          <div className="truncate text-2xs" style={{ color: "#616161" }} title={member.role}>
            {member.role}
          </div>
        </div>
      </div>

      {/* 打招呼语（拟人化：真人在帮你审核的感觉） */}
      <div
        className="mx-3 mt-2 rounded-lg rounded-tl-none px-2.5 py-1.5 text-[10px] leading-relaxed"
        style={{ backgroundColor: "#f2f7f5", color: "#4b5563" }}
      >
        “你好，我负责{member.role.replace(/[。，；].*$/, "")}。把材料传给我，我来帮你审核把关。”
      </div>

      {/* 卡身：三段式（BUGFIX #81）—— 上传/交付标准固定常驻不被问答遮挡，
          问答记录独立滚动，提问框固定底部 */}
      <div className="mt-2 flex min-h-0 flex-1 flex-col border-t" style={{ borderColor: "#f0f0f0" }}>
        {/* ① 常驻区：上传材料 + 材料列表 + 交付标准（不随问答滚动） */}
        <div className="flex-shrink-0 space-y-2 border-b px-3 py-2" style={{ borderColor: "#f0f0f0" }}>
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
            disabled={!caseId || busy === "uploading"}
            className="w-full rounded-lg border border-dashed px-2 py-1.5 text-2xs font-semibold transition-colors hover:border-[#10a37f] hover:text-[#10a37f]"
            style={{
              borderColor: dragOver ? "#10a37f" : "#c9d4d0",
              color: dragOver ? "#10a37f" : "#0451a5",
              backgroundColor: dragOver ? "#eef9f4" : "#fafcfb",
              opacity: !caseId ? 0.5 : 1,
            }}
          >
            {dragOver ? "⬇ 松开即上传给 " + member.name : `⬆ 上传材料给 ${member.name}`}
          </button>
          <div className="text-[10px]" style={{ color: "#9ca3af" }}>
            支持拖拽文件到卡片 · 任意系统复制后直接 Ctrl+V 粘贴入件
          </div>

          {/* 上传/审核中的思考动效 */}
          {busy === "uploading" && <AiThinkingIndicator compact label="上传材料中" />}
          {busy === "reviewing" && <AiThinkingIndicator compact label={`${member.name}正在审核`} />}

          {/* 材料文件列表（多份时区内自滚，入口与交付标准仍常驻） */}
          {files.length > 0 && (
            <div className="max-h-44 space-y-1.5 overflow-y-auto">
              {files.map((f) => (
                <CaseFileRow
                  key={f.id}
                  file={f}
                  expertName={member.name}
                  draftLinked={draftFileIds.has(f.id)}
                />
              ))}
            </div>
          )}

          {/* 交付标准（逐项确认 = 验收条件） */}
          <div>
            <div className="mb-0.5 text-[10px] font-semibold" style={{ color: "#059669" }}>
              交付标准（逐项确认后才算验收）
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
                  return (
                    <label
                      key={o}
                      className="flex cursor-pointer items-start gap-1.5 text-[10px]"
                      style={{ color: isChecked ? "#059669" : "#333333" }}
                    >
                      <input
                        type="checkbox"
                        checked={isChecked}
                        onChange={() => onToggleOutput(o)}
                        className="mt-0.5"
                      />
                      <span
                        style={{ textDecoration: isChecked ? "line-through" : "none" }}
                      >
                        {o}
                      </span>
                    </label>
                  );
                })}
              </div>
            )}
          </div>
        </div>

        {/* ② 滚动区：问答记录独立滚动，不遮挡上方常驻区 */}
        <div className="min-h-0 flex-1 overflow-y-auto px-3 py-2">
          {qa.length > 0 && (
            <div className="space-y-1.5">
              {qa.map((item) => (
                <div key={item.id}>
                  <div
                    className="ml-6 rounded-lg rounded-tr-none border px-2 py-1 text-[10px]"
                    style={{ borderColor: "#eef2f0", backgroundColor: "#f5f5f4", color: "#6b7280" }}
                  >
                    问：{item.question}
                  </div>
                  <div className="mt-1 flex items-start gap-1.5">
                    <ExpertAvatar name={member.name} accepted={accepted} size={18} />
                    <div
                      className="min-w-0 flex-1 whitespace-pre-wrap rounded-lg rounded-tl-none border px-2 py-1 text-[10px]"
                      style={{ borderColor: "#e3efe9", backgroundColor: "#f2f7f5", color: "#202124" }}
                    >
                      {item.answer}
                    </div>
                  </div>
                </div>
              ))}
            </div>
          )}
        </div>

        {/* ③ 常驻区：提问框固定底部 */}
        <div className="flex-shrink-0 border-t px-3 py-2" style={{ borderColor: "#f0f0f0" }}>
          {busy === "asking" ? (
            <AiThinkingIndicator compact label={`${member.name}思考中`} />
          ) : (
            <div className="flex items-center gap-1.5">
              <input
                type="text"
                value={question}
                onChange={(e) => setQuestion(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === "Enter") void handleAsk();
                }}
                placeholder={`向${member.name}提问…（回车发送）`}
                className="flex-1 rounded border px-2 py-1 text-2xs outline-none focus:border-[#10a37f]"
                style={{
                  backgroundColor: "#ffffff",
                  borderColor: "#e0e0e0",
                  color: "#202124",
                }}
              />
              <button
                type="button"
                onClick={() => void handleAsk()}
                disabled={!question.trim()}
                className="rounded px-2 py-1 text-2xs font-semibold disabled:opacity-40"
                style={{ backgroundColor: "#10a37f", color: "#ffffff" }}
              >
                问专家
              </button>
            </div>
          )}
        </div>
      </div>
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
  const [preview, setPreview] = useState<{ name: string; text: string } | null>(null);
  const [busyId, setBusyId] = useState("");

  const handlePreview = async (f: OpsCaseFile): Promise<void> => {
    setBusyId(f.id);
    try {
      const r = await ipc.opsCaseFileContent(f.id);
      const bytes = Uint8Array.from(atob(r.content_base64), (c) => c.charCodeAt(0));
      setPreview({ name: r.file_name, text: new TextDecoder("utf-8").decode(bytes) });
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
            <div className="px-2 py-8 text-center text-2xs" style={{ color: "#9ca3af" }}>
              还没有验收通过的产物
              <div className="mt-1">上传材料或提交草稿，专家验收通过后自动入柜</div>
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
                验收专家：{f.member_key} · {f.reviewed_by === "ai" ? "AI 验收" : "人工验收"}
              </div>
              <div className="mt-1 flex gap-1.5">
                <button
                  type="button"
                  onClick={() => void handlePreview(f)}
                  disabled={busyId === f.id}
                  className="rounded border px-2 py-0.5 text-[10px] disabled:opacity-50"
                  style={{ borderColor: "#d4d4d4", color: "#0451a5", backgroundColor: "#ffffff" }}
                >
                  👁 预览
                </button>
                <button
                  type="button"
                  onClick={() => void handleSaveAs(f)}
                  disabled={busyId === f.id}
                  className="rounded border px-2 py-0.5 text-[10px] disabled:opacity-50"
                  style={{ borderColor: "#d4d4d4", color: "#059669", backgroundColor: "#ffffff" }}
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
              <span className="truncate text-2xs font-semibold" style={{ color: "#1f1f1f" }}>
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
            <span className={s.state === "doing" ? "animate-spin" : ""}>{icon[s.state]}</span>
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

function DraftFormCard({ draft }: { draft: OpsCaseDraft }): JSX.Element {
  const [values, setValues] = useState<Record<string, string>>(draft.values ?? {});
  const [saving, setSaving] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [snapshotOpen, setSnapshotOpen] = useState(false);
  // 全屏填写（BUGFIX #85）：大表单铺开填，不受卡片宽度限制
  const [expanded, setExpanded] = useState(false);
  // file 类型字段选中的文件（提交时自动作为材料上传给出题专家）
  const [draftFiles, setDraftFiles] = useState<Record<string, File>>({});
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
      await useOpsCaseStore.getState().attachFiles(draft.team_id, draft.member_key, picked);
    }
    await useOpsCaseStore.getState().saveDraft(draft.id, values);
    await useOpsCaseStore.getState().submitDraft(draft.id);
    setSubmitting(false);
  };

  // 必填进度（全屏页头进度条 + 页脚提示，BUGFIX #93 设计感）
  const requiredFields = draft.template.filter((f) => f.required);
  const filledRequired = requiredFields.filter((f) =>
    f.type === "file"
      ? draftFiles[f.name] || (values[f.name] ?? "").trim()
      : (values[f.name] ?? "").trim(),
  ).length;
  const progressPct =
    requiredFields.length === 0 ? 100 : Math.round((filledRequired / requiredFields.length) * 100);

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
    <div className={expanded ? "grid grid-cols-2 gap-x-5 gap-y-4" : "grid grid-cols-2 gap-x-3 gap-y-1.5"}>
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
          </label>
          <DraftFieldInput
            field={f}
            value={values[f.name] ?? ""}
            fileName={draftFiles[f.name]?.name}
            disabled={passed || saving || submitting}
            expanded={expanded}
            onChange={(v) => setValues((prev) => ({ ...prev, [f.name]: v }))}
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
      className={expanded ? "rounded-xl border p-4" : "mb-1.5 rounded border p-1.5"}
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
            className={expanded ? "flex items-start gap-1.5 text-[11px]" : "truncate text-[10px]"}
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
      <div className="fixed inset-0 z-40 flex flex-col" style={{ backgroundColor: "#f4f4f2" }}>
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
              <div className="truncate text-[13px] font-semibold" style={{ color: "#1f1f1f" }}>
                {draft.title}
              </div>
              <div className="mt-0.5 text-[10px]" style={{ color: "#9ca3af" }}>
                {draft.member_key ? `出题专家：${draft.member_key} · ` : ""}
                共 {draft.template.length} 项
                {requiredFields.length > 0 ? ` · 必填 ${requiredFields.length} 项` : ""}
              </div>
            </div>
            {requiredFields.length > 0 && (
              <div
                className="flex flex-shrink-0 items-center gap-1.5"
                title={`必填进度：${filledRequired}/${requiredFields.length}`}
              >
                <div className="h-1.5 w-24 overflow-hidden rounded-full" style={{ backgroundColor: "#e7e5e4" }}>
                  <div
                    className="h-full rounded-full transition-all"
                    style={{
                      width: `${progressPct}%`,
                      backgroundColor: progressPct === 100 ? "#10a37f" : "#0451a5",
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
              style={{ backgroundColor: statusBadge.bg, color: statusBadge.color }}
              title={linkedFile?.review_note || undefined}
            >
              {statusBadge.label}
            </span>
            <button
              type="button"
              onClick={() => setExpanded(false)}
              className="flex-shrink-0 rounded-md border px-2 py-1 text-[10px] transition-colors hover:bg-[#f5f5f4]"
              style={{ borderColor: "#d4d4d4", color: "#616161", backgroundColor: "#ffffff" }}
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
              <div className="rounded-xl border bg-white p-3" style={{ borderColor: "#e3efe9" }}>
                <AiThinkingIndicator compact label={`${draft.member_key || "专家"}正在审核草稿`} />
              </div>
            )}
            {notesBlock}
            <div
              className="rounded-xl border bg-white p-6"
              style={{ borderColor: "#e7e5e4", boxShadow: "0 1px 3px rgba(0,0,0,0.05)" }}
            >
              {fieldGrid}
            </div>
            {/* 打回重提对照（BUGFIX #79）：展开上次提交内容，知道自己改哪里 */}
            {rejected && draft.last_snapshot && (
              <div className="rounded-xl border bg-white p-4" style={{ borderColor: "#f0efed" }}>
                <button
                  type="button"
                  onClick={() => setSnapshotOpen((v) => !v)}
                  className="text-[11px] font-medium"
                  style={{ color: "#0451a5" }}
                >
                  {snapshotOpen ? "▾ 收起上次提交内容" : `▸ 对照上次提交内容（第 ${draft.submit_count ?? 1} 次提交）`}
                </button>
                {snapshotOpen && (
                  <pre
                    className="mt-2 max-h-56 overflow-auto rounded-md border p-3 text-[11px] whitespace-pre-wrap"
                    style={{ borderColor: "#f0efed", backgroundColor: "#fafaf9", color: "#4b5563" }}
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
          <div className="flex-shrink-0 border-t bg-white" style={{ borderColor: "#e5e5e2" }}>
            <div className="mx-auto flex max-w-4xl items-center gap-3 px-6 py-3">
              {missing.length > 0 ? (
                <span className="min-w-0 truncate text-[10px]" style={{ color: "#b25c1a" }}>
                  ⚠ 还有 {missing.length} 项必填未完成：{missing.map((m) => m.label).join("、")}
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
                  className="rounded-md border px-4 py-1.5 text-2xs transition-colors hover:bg-[#f5f5f4] disabled:opacity-50"
                  style={{ borderColor: "#d4d4d4", color: "#616161", backgroundColor: "#ffffff" }}
                >
                  {saving ? "保存中…" : "暂存"}
                </button>
                <button
                  type="button"
                  onClick={() => void handleSubmit()}
                  disabled={submitting || missing.length > 0}
                  className="rounded-md px-5 py-1.5 text-2xs font-semibold transition-colors hover:bg-[#0e9272] disabled:opacity-50"
                  style={{
                    backgroundColor: "#10a37f",
                    color: "#ffffff",
                    boxShadow: "0 1px 3px rgba(16,163,127,0.35)",
                  }}
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
          <AiThinkingIndicator compact label={`${draft.member_key || "专家"}正在审核草稿`} />
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
          onClick={() => setExpanded(true)}
          className="ml-auto flex-shrink-0 rounded border px-1.5 py-0.5 text-[10px]"
          style={{ borderColor: "#d4d4d4", color: "#0451a5", backgroundColor: "#ffffff" }}
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
            {snapshotOpen ? "▾ 收起上次提交内容" : `▸ 对照上次提交内容（第 ${draft.submit_count ?? 1} 次提交）`}
          </button>
          {snapshotOpen && (
            <pre
              className="mt-1 max-h-40 overflow-auto rounded border p-2 text-[10px] whitespace-pre-wrap"
              style={{ borderColor: "#f0efed", backgroundColor: "#fafaf9", color: "#4b5563" }}
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
            className="rounded border px-3 py-1 text-2xs disabled:opacity-50"
            style={{ borderColor: "#d4d4d4", color: "#616161", backgroundColor: "#ffffff" }}
          >
            {saving ? "保存中…" : "暂存"}
          </button>
          <button
            type="button"
            onClick={() => void handleSubmit()}
            disabled={submitting || missing.length > 0}
            className="rounded px-3 py-1 text-2xs font-semibold disabled:opacity-50"
            style={{ backgroundColor: "#10a37f", color: "#ffffff" }}
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
}: {
  file: OpsCaseFile;
  expertName: string;
  /** 草稿提交生成的材料：审核意见在草稿卡展示，此处不再重复气泡（BUGFIX #86） */
  draftLinked?: boolean;
}): JSX.Element {
  const [noteExpanded, setNoteExpanded] = useState(false);
  const [marksOpen, setMarksOpen] = useState(false);
  const meta = CASE_FILE_STATUS_META[file.status];
  const overrideFile = useOpsCaseStore((s) => s.overrideFile);
  const deleteFile = useOpsCaseStore((s) => s.deleteFile);
  const reviewFile = useOpsCaseStore((s) => s.reviewFile);

  return (
    <div
      className="rounded-lg border px-2 py-1"
      style={{ borderColor: "#e7e5e4", backgroundColor: "#fafaf9" }}
    >
      <div className="flex items-center gap-1.5">
        <span aria-hidden="true">📄</span>
        <span className="min-w-0 flex-1 truncate text-2xs" style={{ color: "#202124" }} title={file.file_name}>
          {file.file_name}
        </span>
        <span
          className="flex-shrink-0 rounded-full px-1.5 text-[10px] font-semibold"
          style={{ backgroundColor: `${meta.color}18`, color: meta.color }}
        >
          {meta.icon} {meta.label}
          {file.reviewed_by === "ai" ? " · AI" : file.reviewed_by === "human" ? " · 人工" : ""}
        </span>
        {(file.status === "pending" || file.status === "rejected") && (
          <button
            type="button"
            onClick={() => void reviewFile(file.id)}
            className="flex-shrink-0 text-[10px]"
            style={{ color: "#0451a5" }}
            title="让 AI 专家重新审核"
          >
            ↻ 重审
          </button>
        )}
        {/* 打回定位入口（BUGFIX #80）：在文档内高亮问题位置，知道改哪里 */}
        {file.status === "rejected" && (file.reject_marks?.length ?? 0) > 0 && (
          <button
            type="button"
            onClick={() => setMarksOpen(true)}
            className="flex-shrink-0 text-[10px] font-semibold"
            style={{ color: "#cd3131" }}
            title="在文档内高亮打回位置与修改建议"
          >
            📍 {file.reject_marks!.length} 处打回
          </button>
        )}
        {file.status !== "passed" && (
          <button
            type="button"
            onClick={() => void overrideFile(file.id, "passed", "人工验收通过")}
            className="flex-shrink-0 text-[10px]"
            style={{ color: "#059669" }}
            title="人工改判为通过"
          >
            ✓ 通过
          </button>
        )}
        {file.status !== "rejected" && (
          <button
            type="button"
            onClick={() => void overrideFile(file.id, "rejected", "人工打回")}
            className="flex-shrink-0 text-[10px]"
            style={{ color: "#b25c1a" }}
            title="人工改判为打回"
          >
            ✗ 打回
          </button>
        )}
        <button
          type="button"
          onClick={() => void deleteFile(file.id)}
          className="flex-shrink-0 text-[10px]"
          style={{ color: "#cd3131" }}
          title="删除材料"
        >
          ✕
        </button>
      </div>
      {/* 审核步骤链：接收→解析→提取→结论，过程可见（BUGFIX #79） */}
      <ReviewSteps file={file} />
      {/* 审核意见：专家口吻气泡（长文点击展开/收起）；草稿材料的意见在草稿卡展示 */}
      {file.review_note && file.status !== "reviewing" && !draftLinked && (
        <div className="mt-1 flex items-start gap-1.5">
          <ExpertAvatar name={expertName} accepted={false} size={18} />
          <div
            onClick={() => setNoteExpanded((v) => !v)}
            className="min-w-0 flex-1 whitespace-pre-wrap rounded-lg rounded-tl-none border px-2 py-1 text-[10px]"
            style={{
              borderColor: file.status === "rejected" ? "#f3d9d9" : "#e3efe9",
              backgroundColor: file.status === "rejected" ? "#fdf5f5" : "#f2f7f5",
              color: "#374151",
              cursor: "pointer",
              display: "-webkit-box",
              WebkitLineClamp: noteExpanded ? undefined : 3,
              WebkitBoxOrient: "vertical",
              overflow: "hidden",
            }}
            title="点击展开/收起审核意见"
          >
            {file.review_note}
          </div>
        </div>
      )}
      {/* 关键要素卡（厚资料变薄数据，2026-08-10）：低置信标红置顶，员工只核标红项 */}
      {(file.extracted_fields?.length ?? 0) > 0 && (
        <div
          className="mt-1 rounded-lg border px-2 py-1"
          style={{ borderColor: "#f0efed", backgroundColor: "#ffffff" }}
        >
          <div className="mb-0.5 text-[10px] font-semibold" style={{ color: "#6b7280" }}>
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
          <div className="text-[10px] font-semibold" style={{ color: "#6b7280" }}>
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
      {marksOpen && <RejectMarkPreview file={file} onClose={() => setMarksOpen(false)} />}
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
        const bytes = Uint8Array.from(atob(r.content_base64), (c) => c.charCodeAt(0));
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
          <span className="truncate text-2xs font-semibold" style={{ color: "#cd3131" }}>
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
          <div className="p-4 text-center text-2xs" style={{ color: "#cd3131" }}>
            文档内容读取失败，无法定位打回位置
          </div>
        )}
        {!failed && content === null && (
          <div className="p-4 text-center text-2xs" style={{ color: "#9ca3af" }}>
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
                      ? { backgroundColor: "#fdecec", border: "1px solid #e8b4b4", color: "#7f1d1d" }
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
        <div className="flex-shrink-0 border-t p-2" style={{ borderColor: "#e0e0e0", backgroundColor: "#fafafa" }}>
          {marks.map((m, i) => (
            <div key={i} className="mb-1 flex items-start gap-1.5 text-[10px]">
              <span className="flex-shrink-0 font-semibold" style={{ color: "#cd3131" }}>
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
