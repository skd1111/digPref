/**
 * OperationsWorkbench —— 运营工作台（运营模式独立页签的主内容，与开发模式并列）。
 *
 * 挂载方式：
 *   - 唯一入口：顶部 ModeSwitcher「运营模式」页签（mode === 'operator'，全屏接管）
 *   （ActivityBar「🏦 运营」兼容入口已移除：与模式页签重复，2026-08-07）
 *
 * 2026-08-10 交互改造：中间区不再是传统大 Chat，而是「专家验收工作流」；
 * 右侧第三象限（业务记录 / Skill 经验 / 外部接入）整体隐藏（用户要求，2026-08-10）：
 *   ┌──────────────────────┬───────────────────────────────────┐
 *   │ 左：业务列表           │ 中：ExpertWorkflowPanel（专家团页签   │
 *   │   16 模块导航 + 搜索    │   + 横向专家卡：上传材料/AI 审核      │
 *   │                       │   /迷你提问/打包导出）              │
 *   └──────────────────────┴───────────────────────────────────┘
 *
 * 选择功能点 → 自动注入会话上下文（chatStore.opsNavContext）+ 自动选专家团；
 * 全部专家验收通过 → 导出交付物 zip + 生成可审计业务记录（/ops/records）。
 */
import { useEffect, useMemo, useRef, useState } from "react";
import { ExpertWorkflowPanel } from "@/components/ops/ExpertWorkflowPanel";
import { useBiznavStore } from "@/store/biznavStore";
import { useOpsNavStore } from "@/store/opsNavStore";
import { useOpsCaseStore } from "@/store/opsCaseStore";
import { useSkillsStore } from "@/store/skillsStore";
import { useChatStore } from "@/store/chatStore";
import { useExpertTeamStore } from "@/store/expertTeamStore";
import { ipc } from "@/ipc/invoke";
import {
  OPS_MODULES,
  findOpsItem,
  type OpsModule,
} from "@/components/ops/opsModules";
import type { Feature, FeatureContextPayload } from "@/types/biznav";

export function OperationsWorkbench(): JSX.Element {
  // ---- hooks（全部无条件，遵守 BUGFIX #15 教训）----
  const features = useBiznavStore((s) => s.features);
  const projectName = useBiznavStore((s) => s.projectName);
  const loadFeatures = useBiznavStore((s) => s.loadFeatures);
  const skills = useSkillsStore((s) => s.skills);
  const selectedItemId = useOpsNavStore((s) => s.selectedItemId);
  
  const [expandedModules, setExpandedModules] = useState<Set<string>>(
    () => new Set([OPS_MODULES[0]?.id].filter(Boolean) as string[]),
  );
  const [searchQuery, setSearchQuery] = useState("");
  // AI 推荐在途的业务 id（按业务去重：skills 加载完成会触发 effect 重跑，日志曾出现 5 连发）
  const recommendInFlightRef = useRef<string | null>(null);
  
  // 挂载：加载工程功能点 + Skill（Skill 仅供历史兼容的专家团推荐链使用，不再直接呈现）
  useEffect(() => {
    void loadFeatures();
    void useSkillsStore.getState().loadSkills();
    // 兜底重拉专家团：WorkspaceLayout 启动时 loadTeams 若因 Agent 未就绪失败（静默吞错），
    // teams 会永远为空 → 推荐返回的团匹配不到 → 点业务项中间区毫无变化（BUGFIX #76）
    if (useExpertTeamStore.getState().teams.length === 0) {
      void useExpertTeamStore.getState().loadTeams();
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // 当前选中的业务信息（静态导航项 或 工程提炼功能点）
  const selection = useMemo(() => {
    if (!selectedItemId) return null;
    const staticHit = findOpsItem(selectedItemId);
    const featureHit = features.find((f) => f.id === selectedItemId) ?? null;
    return { staticHit, featureHit };
  }, [selectedItemId, features]);

  // 选中业务 → 写入 chatStore.opsNavContext（自动注入绑定 Skill）
  useEffect(() => {
    const ctx = buildOpsContext(
      selection,
      useOpsNavStore.getState().skillBindings,
    );
    useChatStore.getState().setOpsNavContext(ctx);
    return () => {
      // 离开运营工作台（切模式）时仅清理注入上下文，避免污染其他会话；
      // 专家团选择态保留（selectedForItemId 标记对应业务，切回同业务直接复用，
      // 不再重跑推荐 —— BUGFIX #124：修复「切模式再切回又得重新加载专家团」）
      useChatStore.getState().setOpsNavContext(null);
    };
  }, [selection]);

  // 选中业务 → 自动选择专家团（中期改造：运营链路全看专家团，Skill 退出）：
  //   1. 功能点预设 expert_team_ids → 直接用（零延迟）
  //   2. 历史数据兼容：Skill 预设 required_expert_team_ids
  //   3. 无预设 → 后端拿功能点名 + 全部专家团描述让 LLM 判断（本地→内网→云端三级降级，失败静默）
  //   manual 模式下不自动改写，直到用户切回「自动」
  useEffect(() => {
    if (!selection || !selectedItemId) return;
    // 业务切换（selectedForItemId 跨卸载保留在 store，组件 ref 做不到）：
    // 上一业务的选择（含手动）作废，新业务重新自动选团；同业务切模式再切回不重置
    if (useExpertTeamStore.getState().selectedForItemId !== selectedItemId) {
      useExpertTeamStore.getState().clearSelection();
    }
    // 清理后重新取快照（st 可能已过期：manual 被重置为 auto）
    const st = useExpertTeamStore.getState();
    if (st.selectionMode === "manual") return;
    // 同一业务已有选择结果（切模式再切回 / skills 重载触发 effect 重跑）→ 直接复用，
    // 不再重走预设判断与 LLM 推荐，避免重复加载刚打开的专家团
    if (st.selectedForItemId === selectedItemId && st.selectionSource !== "") return;
    const featurePreset = selection.featureHit?.expert_team_ids ?? [];
    if (featurePreset.length > 0) {
      st.applyAutoSelection(featurePreset, "preset", selectedItemId);
      return;
    }
    const skillId =
      selection.featureHit?.skill_id ??
      useOpsNavStore.getState().skillBindings[selectedItemId] ??
      null;
    const skill = skillId
      ? skills.find((s) => s.id === skillId)
      : undefined;
    const legacyPreset = skill?.required_expert_team_ids ?? [];
    if (legacyPreset.length > 0) {
      st.applyAutoSelection(legacyPreset, "preset", selectedItemId);
      return;
    }
    // AI 推荐走 LLM（本地→内网→云端三级降级）需数秒：
    // 1) 标记进行中 → 中间区展示加载动效，避免用户误判「点了没反应」
    // 2) 同一业务在途不重复发起
    if (recommendInFlightRef.current === selectedItemId) return;
    recommendInFlightRef.current = selectedItemId;
    const flightKey = selectedItemId;
    // 旧选择作废（切业务/首次进入）：清空团列表并记录本次推荐对应的业务 id，
    // 在途竞态时旧结果返回靠 selectedForItemId 校验，不会错配到新业务
    st.applyAutoSelection([], "none", selectedItemId);
    st.setRecommending(true);
    void ipc
      .expertTeamsRecommend({
        feature_name:
          selection.featureHit?.name ??
          selection.staticHit?.item.label ??
          "",
        feature_description: selection.featureHit?.description ?? "",
        materials: skill?.materials ?? [],
        deliverables: skill?.deliverables ?? [],
        preset_team_ids: [],
      })
      .then((r) => {
        // 推荐返回时业务可能已切换：仅 auto 模式且仍是本业务的在途推荐才应用
        const cur = useExpertTeamStore.getState();
        if (cur.selectionMode === "auto" && cur.selectedForItemId === flightKey) {
          cur.applyAutoSelection(
            r.team_ids,
            r.source as "llm" | "keyword" | "none",
            flightKey,
          );
        } else {
          cur.setRecommending(false);
        }
      })
      .catch(() => {
        useExpertTeamStore.getState().setRecommending(false);
      })
      .finally(() => {
        if (recommendInFlightRef.current === flightKey) {
          recommendInFlightRef.current = null;
        }
      });
  }, [selection, selectedItemId, skills]);

  const handleSelect = (itemId: string): void => {
    useOpsNavStore.getState().selectItem(itemId);
  };

  // 中期改造：保存功能点的专家团预设（替代 Skill 绑定，运营链路全看专家团）
  const handleSaveTeamPreset = async (ids: string[]): Promise<void> => {
    const f = selection?.featureHit;
    if (!f) return;
    try {
      await useBiznavStore.getState().upsertFeature(f.id, projectName, {
        expected_version: f.version,
        expert_team_ids: ids,
      });
    } catch (e) {
      window.alert(`保存专家团预设失败：${e instanceof Error ? e.message : String(e)}`);
    }
  };

  const filteredModules = useMemo(() => {
    const q = searchQuery.trim().toLowerCase();
    if (!q) return OPS_MODULES;
    return OPS_MODULES.map((m) => ({
      ...m,
      items: m.items.filter(
        (i) =>
          i.label.toLowerCase().includes(q) ||
          m.label.toLowerCase().includes(q) ||
          i.id.toLowerCase().includes(q),
      ),
    })).filter((m) => m.items.length > 0);
  }, [searchQuery]);

  return (
    <div className="flex h-full min-h-0" style={{ backgroundColor: "#f3f3f3" }}>
      {/* ===== 左：业务列表（16 模块导航 + 搜索） ===== */}
      <aside
        className="flex w-[280px] flex-shrink-0 flex-col border-r"
        style={{ borderColor: "#d4d4d4", backgroundColor: "#f8f8f8" }}
      >
        <div
          className="flex items-center justify-between border-b px-3 py-2"
          style={{ borderColor: "#d4d4d4" }}
        >
          <span className="text-ui font-semibold" style={{ color: "#333333" }}>
            🏦 业务列表
          </span>
          <button
            type="button"
            onClick={() => useOpsNavStore.getState().openCreateDialog()}
            className="rounded px-1.5 py-0.5 text-2xs"
            style={{ color: "#0451a5" }}
            title="创建功能点"
          >
            ＋ 新建
          </button>
        </div>
        <div className="border-b p-2" style={{ borderColor: "#d4d4d4" }}>
          <input
            type="text"
            value={searchQuery}
            onChange={(e) => setSearchQuery(e.target.value)}
            placeholder="🔍 搜索功能点…"
            className="w-full rounded border px-2 py-1 text-2xs outline-none focus:border-[#007acc]"
            style={{
              backgroundColor: "#ffffff",
              borderColor: "#d4d4d4",
              color: "#1f1f1f",
            }}
          />
        </div>
        <div className="flex-1 overflow-auto p-1">
          {filteredModules.length === 0 && (
            <div
              className="px-2 py-6 text-center text-2xs"
              style={{ color: "#616161" }}
            >
              没有匹配的功能点
            </div>
          )}
          {filteredModules.map((m) => (
            <OpsModuleGroup
              key={m.id}
              module={m}
              features={features}
              expanded={expandedModules.has(m.id)}
              selectedItemId={selectedItemId}
              onToggle={() =>
                setExpandedModules((prev) => {
                  const next = new Set(prev);
                  if (next.has(m.id)) next.delete(m.id);
                  else next.add(m.id);
                  return next;
                })
              }
              onSelect={handleSelect}
            />
          ))}
        </div>
        <div
          className="flex-shrink-0 border-t px-3 py-1.5 text-2xs"
          style={{ borderColor: "#d4d4d4", color: "#616161" }}
        >
          运营工作台 · 专家团验收交付 · 统计报表由数据专家模式承接
        </div>
      </aside>

      {/* ===== 中：专家验收工作流（取代传统大 Chat；右侧第三象限已隐藏，2026-08-10） ===== */}
      <main
        className="flex min-w-0 flex-1 flex-col"
        style={{ backgroundColor: "#ffffff" }}
      >
        <ExpertWorkflowPanel
          selection={selection}
          projectName={projectName}
          onSaveTeamPreset={(ids: string[]) => void handleSaveTeamPreset(ids)}
        />
      </main>

      {/* 新建功能点对话框 */}
      <CreateFeatureDialog
        modules={OPS_MODULES}
        projectName={projectName}
        onCreated={(featureId) => handleSelect(featureId)}
      />
    </div>
  );
}

// ---------------------------------------------------------------------------
// 左侧模块组
// ---------------------------------------------------------------------------

function OpsModuleGroup({
  module,
  features,
  expanded,
  selectedItemId,
  onToggle,
  onSelect,
}: {
  module: OpsModule;
  features: Feature[];
  expanded: boolean;
  selectedItemId: string | null;
  onToggle: () => void;
  onSelect: (itemId: string) => void;
}): JSX.Element {
  // 工程提炼的功能点：category 命中模块名（含「对公账户管理」这类长名子串匹配）
  const engFeatures = features.filter(
    (f) =>
      f.category === module.label ||
      (module.label.length >= 4 && f.category.includes(module.label)) ||
      module.label.includes(f.category),
  );
  const skillBindings = useOpsNavStore((s) => s.skillBindings);
  // 当前选中业务的办理状态角标（BUGFIX #79：一眼看到哪个业务能导出/待处理）
  const caseFiles = useOpsCaseStore((s) => s.files);
  const caseBadgeFor = (
    itemId: string,
  ): { text: string; color: string } | null => {
    if (itemId !== selectedItemId || caseFiles.length === 0) return null;
    const rejected = caseFiles.filter((f) => f.status === "rejected").length;
    const passed = caseFiles.filter((f) => f.status === "passed").length;
    if (rejected > 0) return { text: `${rejected} 打回`, color: "#b25c1a" };
    if (passed === caseFiles.length) return { text: "可导出", color: "#10a37f" };
    return { text: "办理中", color: "#0451a5" };
  };

  return (
    <div className="mb-1">
      <button
        type="button"
        onClick={onToggle}
        className="flex w-full items-center gap-1.5 rounded px-2 py-1 text-ui font-semibold transition-colors hover:bg-vscode-border"
        style={{ color: "#333333" }}
      >
        <span
          className="text-[10px] transition-transform duration-150"
          style={{ transform: expanded ? "rotate(90deg)" : "none" }}
        >
          ▶
        </span>
        <span>{module.icon}</span>
        <span className="flex-1 text-left">{module.label}</span>
        <span
          className="rounded px-1.5 text-2xs"
          style={{ backgroundColor: "#ffffff", color: "#6e6e6e" }}
        >
          {module.items.length + engFeatures.length}
        </span>
      </button>
      {expanded && (
        <div
          className="ml-3 mt-0.5 space-y-0.5 border-l-2 pl-1.5"
          style={{ borderColor: "#d4d4d4" }}
        >
          {module.items.map((item) => {
            const selected = item.id === selectedItemId;
            const skillId = skillBindings[item.id];
            const caseBadge = caseBadgeFor(item.id);
            return (
              <button
                key={item.id}
                type="button"
                onClick={() => onSelect(item.id)}
                className="flex w-full items-center gap-1 rounded px-1.5 py-1 text-2xs transition-colors"
                style={{
                  backgroundColor: selected ? "#0e639c" : "transparent",
                  color: selected ? "#ffffff" : "#1f1f1f",
                  borderLeft: selected
                    ? "3px solid #007acc"
                    : "3px solid transparent",
                }}
                title={`${module.label} · ${item.label}`}
              >
                <span className="flex-1 truncate text-left">{item.label}</span>
                {caseBadge && (
                  <span
                    className="rounded px-1 text-[9px] font-semibold"
                    style={{ backgroundColor: `${caseBadge.color}18`, color: caseBadge.color }}
                    title="当前业务的材料办理状态"
                  >
                    {caseBadge.text}
                  </span>
                )}
                {item.delegated ? (
                  <span
                    className="text-[9px]"
                    style={{ color: selected ? "#cfe4f5" : "#795e26" }}
                  >
                    数据专家承接
                  </span>
                ) : item.external ? (
                  <span
                    className="text-[9px]"
                    style={{ color: selected ? "#cfe4f5" : "#b25c1a" }}
                  >
                    外部接入
                  </span>
                ) : skillId ? (
                  <span
                    className="rounded px-1 text-[9px]"
                    style={{
                      backgroundColor: selected ? "#ffffff33" : "#0e639c20",
                      color: selected ? "#ffffff" : "#0451a5",
                    }}
                  >
                    Skill
                  </span>
                ) : null}
              </button>
            );
          })}
          {engFeatures.map((f) => {
            const selected = f.id === selectedItemId;
            return (
              <button
                key={f.id}
                type="button"
                onClick={() => onSelect(f.id)}
                className="flex w-full items-center gap-1 rounded px-1.5 py-1 text-2xs transition-colors"
                style={{
                  backgroundColor: selected ? "#0e639c" : "transparent",
                  color: selected ? "#ffffff" : "#1f1f1f",
                  borderLeft: selected
                    ? "3px solid #007acc"
                    : "3px solid transparent",
                }}
                title={`工程提炼：${f.name}`}
              >
                <span
                  className="text-[10px]"
                  style={{ color: selected ? "#cfe4f5" : "#4ec9b0" }}
                >
                  ⎇
                </span>
                <span className="flex-1 truncate text-left">{f.name}</span>
                {f.skill_id ? (
                  <span
                    className="rounded px-1 text-[9px]"
                    style={{
                      backgroundColor: selected ? "#ffffff33" : "#0e639c20",
                      color: selected ? "#ffffff" : "#0451a5",
                    }}
                  >
                    Skill
                  </span>
                ) : null}
              </button>
            );
          })}
        </div>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// 新建功能点对话框
// ---------------------------------------------------------------------------

function CreateFeatureDialog({
  modules,
  projectName,
  onCreated,
}: {
  modules: OpsModule[];
  projectName: string;
  onCreated: (featureId: string) => void;
}): JSX.Element | null {
  const open = useOpsNavStore((s) => s.createDialogOpen);
  const close = useOpsNavStore((s) => s.closeCreateDialog);
  const [name, setName] = useState("");
  const [moduleId, setModuleId] = useState(modules[0]?.id ?? "");
  const [description, setDescription] = useState("");
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    if (open) {
      setName("");
      setModuleId(modules[0]?.id ?? "");
      setDescription("");
      setSaving(false);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open]);

  if (!open) return null;

  const handleCreate = async (): Promise<void> => {
    if (!name.trim()) {
      window.alert("请填写功能点名称");
      return;
    }
    const module = modules.find((m) => m.id === moduleId);
    setSaving(true);
    const id = `ops_${moduleId}_${Date.now().toString(36)}`;
    const st = useBiznavStore.getState();
    try {
      if (st.backendReady) {
        await st.upsertFeature(id, projectName, {
          expected_version: 1,
          name: name.trim(),
          description: description.trim(),
          category: module?.label ?? "未分类",
          skill_id: null,
        });
        await st.loadFeatures();
      } else {
        // 后端不可用 → 本地新增（演示兜底）
        st.addLocalFeature({
          id,
          name: name.trim(),
          description: description.trim(),
          category: module?.label ?? "未分类",
          skill_id: null,
          project_name: projectName,
          project_root: st.projectRoot,
          related_files: [],
          related_apis: [],
          related_tables: [],
          business_rules: [],
          risk_level: "low",
          source: "manual",
          ai_confidence: null,
          version: 1,
          created_at: Date.now(),
          updated_at: Date.now(),
        });
      }
      // 中期改造：新建不再绑 Skill；专家团预设在「业务工作台」/功能点编辑器配置
      close();
      onCreated(id);
    } catch (e) {
      window.alert(`创建失败：${e instanceof Error ? e.message : String(e)}`);
    } finally {
      setSaving(false);
    }
  };

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center"
      style={{ backgroundColor: "rgba(0,0,0,0.5)" }}
      onClick={(e) => {
        if (e.target === e.currentTarget) close();
      }}
    >
      <div
        className="w-[460px] rounded shadow-2xl"
        style={{ backgroundColor: "#ffffff", border: "1px solid #d4d4d4" }}
        onClick={(e) => e.stopPropagation()}
      >
        <div
          className="flex items-center justify-between border-b px-4 py-2"
          style={{ borderColor: "#d4d4d4", backgroundColor: "#f3f3f3" }}
        >
          <span className="text-ui font-semibold" style={{ color: "#1f1f1f" }}>
            ＋ 创建功能点
          </span>
          <button
            type="button"
            onClick={close}
            className="rounded px-2 py-0.5 text-2xs hover:bg-vscode-border"
            style={{ color: "#616161" }}
          >
            ✕
          </button>
        </div>
        <div className="space-y-2 p-4">
          <div>
            <label
              className="mb-0.5 block text-2xs"
              style={{ color: "#616161" }}
            >
              功能点名称
            </label>
            <input
              value={name}
              onChange={(e) => setName(e.target.value)}
              placeholder="如：尽职调查"
              className="w-full rounded border px-2 py-1 text-2xs outline-none"
              style={{
                backgroundColor: "#f3f3f3",
                borderColor: "#d4d4d4",
                color: "#1f1f1f",
              }}
            />
          </div>
          <div>
            <label
              className="mb-0.5 block text-2xs"
              style={{ color: "#616161" }}
            >
              所属一级模块
            </label>
            <select
              value={moduleId}
              onChange={(e) => setModuleId(e.target.value)}
              className="w-full rounded border px-2 py-1 text-2xs outline-none"
              style={{
                backgroundColor: "#f3f3f3",
                borderColor: "#d4d4d4",
                color: "#1f1f1f",
              }}
            >
              {modules.map((m) => (
                <option key={m.id} value={m.id}>
                  {m.icon} {m.label}
                </option>
              ))}
            </select>
          </div>
          <div>
            <label
              className="mb-0.5 block text-2xs"
              style={{ color: "#616161" }}
            >
              描述
            </label>
            <textarea
              value={description}
              onChange={(e) => setDescription(e.target.value)}
              rows={2}
              placeholder="这个业务做什么、解决什么问题…"
              className="w-full rounded border px-2 py-1 text-2xs outline-none"
              style={{
                backgroundColor: "#f3f3f3",
                borderColor: "#d4d4d4",
                color: "#1f1f1f",
              }}
            />
          </div>
        </div>
        <div
          className="flex justify-end gap-2 border-t px-4 py-2"
          style={{ borderColor: "#d4d4d4", backgroundColor: "#f3f3f3" }}
        >
          <button
            type="button"
            onClick={close}
            className="rounded px-3 py-1 text-2xs"
            style={{ backgroundColor: "#e0e0e0", color: "#1f1f1f" }}
          >
            取消
          </button>
          <button
            type="button"
            onClick={() => void handleCreate()}
            disabled={saving}
            className="rounded px-3 py-1 text-2xs font-semibold"
            style={{
              backgroundColor: "#0e639c",
              color: "#ffffff",
              opacity: saving ? 0.6 : 1,
            }}
          >
            {saving ? "创建中…" : "创建"}
          </button>
        </div>
      </div>
    </div>
  );
}

/** 构建注入会话的业务上下文（含 Skill id，提示词会自动加载 Skill 经验） */
function buildOpsContext(
  selection: {
    staticHit: ReturnType<typeof findOpsItem>;
    featureHit: Feature | null;
  } | null,
  skillBindings: Record<string, string>,
): FeatureContextPayload | null {
  if (!selection) return null;
  if (selection.featureHit) {
    const f = selection.featureHit;
    return {
      feature_id: f.id,
      feature_name: f.name,
      feature_description: f.description,
      skill_id: f.skill_id ?? skillBindings[f.id] ?? null,
      related_files: [],
      related_apis: [],
      related_tables: [],
      business_rules: [],
      source: "manual",
    };
  }
  const item = selection.staticHit?.item;
  const module = selection.staticHit?.module;
  if (!item) return null;
  return {
    feature_id: item.id,
    feature_name: item.label,
    feature_description: `${module?.label ?? ""} · ${item.label}`,
    skill_id: skillBindings[item.id] ?? null,
    related_files: [],
    related_apis: [],
    related_tables: [],
    business_rules: [],
    source: "manual",
  };
}
