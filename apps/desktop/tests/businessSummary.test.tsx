/**
 * businessSummary.test.tsx —— 业务汇总（业务办理台账）前端测试（2026-11-09）。
 *
 * 覆盖：查询页渲染（状态计数条 + 结果表 + 多维筛选参数）、运营工作台挂起冻结。
 */
import { beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { BusinessSummaryView } from "@/views/BusinessSummaryView";
import { ExpertWorkflowPanel } from "@/components/ops/ExpertWorkflowPanel";
import { useExpertTeamStore } from "@/store/expertTeamStore";
import { useOpsCaseStore } from "@/store/opsCaseStore";
import { useOpsTaskStore } from "@/store/opsTaskStore";
import { useUIStore } from "@/store/uiStore";

const taskListMock = vi.fn();
const taskGetMock = vi.fn();
const taskStatusMock = vi.fn();
const taskStartMock = vi.fn();
const caseGetMock = vi.fn();

vi.mock("@/ipc/invoke", () => ({
  ipc: {
    opsTaskList: (...args: unknown[]) => taskListMock(...args),
    opsTaskGet: (...args: unknown[]) => taskGetMock(...args),
    opsTaskStatus: (...args: unknown[]) => taskStatusMock(...args),
    opsTaskStart: (...args: unknown[]) => taskStartMock(...args),
    opsCaseGet: (...args: unknown[]) => caseGetMock(...args),
    opsCaseFileAdd: vi.fn(),
    opsCaseFileReview: vi.fn(),
    opsCaseFileOverride: vi.fn(),
    opsCaseFileDelete: vi.fn(),
    opsCaseAsk: vi.fn(),
    opsCaseDraftSave: vi.fn(),
    opsCaseDraftDirect: vi.fn(),
    opsCaseDraftSubmit: vi.fn(),
    opsCaseDraftOcrFill: vi.fn(),
    opsCaseExport: vi.fn(),
    opsCaseCrosscheck: vi.fn().mockResolvedValue({
      case_id: "bank__ops_open",
      inconsistencies: [],
      low_confidence: [],
      consistent: true,
    }),
    opsCreateRecord: vi.fn(),
    opsListRecords: vi.fn(),
    opsCaseClear: vi.fn(),
  },
}));

const TEAM = {
  schema_version: "1.0",
  id: "due_diligence_team",
  name: "尽职调查专家团",
  description: "",
  applicable_scenarios: [],
  trigger_keywords: [],
  enabled: true,
  report_template: "",
  members: [
    {
      name: "客户身份识别专家",
      role: "识别客户身份",
      responsibilities: [],
      focus_points: [],
      outputs: ["客户身份基本信息表"],
      prompt: "",
    },
  ],
};

const FEATURE = {
  id: "ops_open",
  name: "对公开户辅助",
  description: "",
  category: "对公账户管理",
  skill_id: null,
  expert_team_ids: [TEAM.id],
  project_name: "bank",
  project_root: "",
  related_files: [],
  related_apis: [],
  related_tables: [],
  business_rules: [],
  risk_level: "low",
  source: "manual",
  ai_confidence: null,
  version: 1,
  created_at: 0,
  updated_at: 0,
};

function task(overrides: Record<string, unknown> = {}): Record<string, unknown> {
  return {
    id: "BT-1",
    case_id: "bank__ops_open",
    project_name: "bank",
    feature_id: "corp_open",
    feature_label: "对公开户辅助",
    category: "corp_account",
    category_label: "对公账户管理",
    title: "对公开户辅助",
    status: "in_progress",
    status_label: "正在办理",
    status_note: "",
    pending_items: [],
    operator: "客户经理张三",
    record_id: "",
    created_at: 1700000000,
    updated_at: 1700000000,
    suspended_at: 0,
    closed_at: 0,
    ...overrides,
  };
}

beforeEach(() => {
  vi.clearAllMocks();
  taskListMock.mockResolvedValue({
    tasks: [task()],
    total: 1,
    counts: { in_progress: 1, suspended: 0, manual_review: 0, closed: 0 },
  });
  taskGetMock.mockResolvedValue({
    task: task(),
    pending_items: [],
    file_stats: { pending: 1 },
    file_total: 1,
    record: null,
  });
  taskStatusMock.mockResolvedValue(task({ status: "suspended", status_label: "挂起" }));
  taskStartMock.mockResolvedValue(task());
  caseGetMock.mockResolvedValue({
    case_id: "bank__ops_open",
    files: [],
    qa: [],
    drafts: [],
    task: task(),
  });
  useUIStore.setState({ mode: "operator" });
  useExpertTeamStore.setState({
    teams: [TEAM as never],
    selectedTeamIds: [TEAM.id],
    selectionMode: "auto",
    selectionSource: "preset",
  });
  useOpsCaseStore.setState({
    case_id: "bank__ops_open",
    files: [],
    qa: [],
    drafts: [],
    task: null,
    loading: false,
    error: null,
    busyMembers: {},
    exporting: false,
    riskAuditFileId: "",
  });
  useOpsTaskStore.setState({
    tasks: [],
    counts: { in_progress: 0, suspended: 0, manual_review: 0, closed: 0 },
    query: {},
    detail: null,
    loading: false,
    error: null,
  });
});

// ---------------------------------------------------------------------------
// 查询页
// ---------------------------------------------------------------------------

describe("业务汇总查询页", () => {
  /** 状态计数条上的按钮（按 title 精确取，避免与结果行的状态徽标撞名） */
  function statusChip(label: string): HTMLElement {
    return screen.getByTitle(`按「${label}」筛选（可多选）`);
  }

  it("渲染台账列表与状态计数条", async () => {
    render(<BusinessSummaryView />);
    await waitFor(() => expect(screen.getByText("业务汇总")).toBeTruthy());
    // 状态计数条（四态）
    expect(statusChip("正在办理")).toBeTruthy();
    expect(statusChip("挂起")).toBeTruthy();
    expect(statusChip("人工确认")).toBeTruthy();
    expect(statusChip("结束")).toBeTruthy();
    // 结果行
    await waitFor(() => expect(screen.getAllByText("对公开户辅助").length).toBeGreaterThan(0));
    expect(screen.getAllByText("对公账户管理").length).toBeGreaterThan(0);
  });

  it("点状态计数条即按该状态筛选（多选）", async () => {
    render(<BusinessSummaryView />);
    await waitFor(() => expect(statusChip("正在办理")).toBeTruthy());
    fireEvent.click(statusChip("正在办理"));
    await waitFor(() =>
      expect(taskListMock).toHaveBeenCalledWith(expect.objectContaining({ status: "in_progress" })),
    );
  });

  it("筛选条件（业务大类 / 关键词 / 日期）随选择传给后端", async () => {
    render(<BusinessSummaryView />);
    await waitFor(() => expect(statusChip("正在办理")).toBeTruthy());

    // 业务大类
    const selects = screen.getAllByRole("combobox");
    fireEvent.change(selects[0], { target: { value: "corp_account" } });
    await waitFor(() =>
      expect(taskListMock).toHaveBeenCalledWith(expect.objectContaining({ category: "corp_account" })),
    );

    // 关键词 + 日期
    const keyword = screen.getByPlaceholderText("业务名称 / 小类");
    fireEvent.change(keyword, { target: { value: "开户" } });
    fireEvent.click(screen.getByText("查询"));
    await waitFor(() =>
      expect(taskListMock).toHaveBeenCalledWith(expect.objectContaining({ keyword: "开户" })),
    );
  });

  it("点结果行展开明细（待处理项 / 材料进度）", async () => {
    taskGetMock.mockResolvedValue({
      task: task({ status: "manual_review", status_label: "人工确认" }),
      pending_items: [
        {
          file_id: "CF-1",
          file_name: "营业执照.txt",
          member_key: "客户身份识别专家",
          kind: "rejected",
          note: "执照已过有效期",
        },
      ],
      file_stats: { rejected: 1 },
      file_total: 1,
      record: null,
    });
    render(<BusinessSummaryView />);
    await waitFor(() => expect(screen.getAllByText("对公开户辅助").length).toBeGreaterThan(0));
    fireEvent.click(screen.getAllByText("对公开户辅助")[0]);
    await waitFor(() => expect(screen.getByText(/执照已过有效期/)).toBeTruthy());
    expect(screen.getByText(/材料进度：共 1 份/)).toBeTruthy();
  });
});

// ---------------------------------------------------------------------------
// 挂起冻结（运营工作台）
// ---------------------------------------------------------------------------

describe("业务挂起冻结", () => {
  function renderPanel(): void {
    render(
      <ExpertWorkflowPanel
        selection={{ staticHit: null, featureHit: FEATURE as never }}
        projectName="bank"
        onSaveTeamPreset={() => undefined}
      />,
    );
  }

  it("还没上传材料（未建档）也看得到「挂起」，但置灰并提示何时可用", async () => {
    caseGetMock.mockResolvedValue({
      case_id: "bank__ops_open",
      files: [],
      qa: [],
      drafts: [],
      task: null,
    });
    useOpsCaseStore.setState({ task: null });
    renderPanel();
    const btn = (await screen.findByText("⏸ 挂起")).closest("button")!;
    // 等 Case 加载完成（避免 loading 期间的临时状态干扰断言）
    await waitFor(() => expect(useOpsCaseStore.getState().loading).toBe(false));
    // 没台账：按钮可见但禁用，悬浮说明「上传第一份材料后可用」
    expect(btn.disabled).toBe(true);
    expect(btn.title).toMatch(/上传第一份材料后可用/);
  });

  it("台账存在时显示「挂起」按钮；点后当前业务只读冻结并给出恢复入口", async () => {
    useOpsCaseStore.setState({ task: task() as never });
    renderPanel();
    await waitFor(() => expect(screen.getByText("⏸ 挂起")).toBeTruthy());
    // 等 Case 加载完成（否则 loadCase 返回值会把挂起状态覆盖回去）
    await waitFor(() => expect(useOpsCaseStore.getState().loading).toBe(false));

    fireEvent.click(screen.getByText("⏸ 挂起"));
    await waitFor(() =>
      expect(taskStatusMock).toHaveBeenCalledWith("BT-1", { action: "suspend" }),
    );
    // 冻结后：横幅 + 恢复按钮 + 上传按钮禁用
    await waitFor(() => expect(screen.getByText(/本业务已挂起/)).toBeTruthy());
    expect(screen.getAllByText("▶ 恢复办理").length).toBeGreaterThan(0);
    const uploadBtn = screen.getAllByText("⬆ 上传材料")[0].closest("button")!;
    expect(uploadBtn.disabled).toBe(true);
  });

  it("恢复办理后解冻（上传按钮恢复可用）", async () => {
    taskStatusMock.mockResolvedValue(task());
    caseGetMock.mockResolvedValue({
      case_id: "bank__ops_open",
      files: [],
      qa: [],
      drafts: [],
      task: task({ status: "suspended", status_label: "挂起" }),
    });
    useOpsCaseStore.setState({
      task: task({ status: "suspended", status_label: "挂起" }) as never,
    });
    renderPanel();
    await waitFor(() => expect(screen.getByText(/本业务已挂起/)).toBeTruthy());
    fireEvent.click(screen.getAllByText("▶ 恢复办理")[0]);
    await waitFor(() =>
      expect(taskStatusMock).toHaveBeenCalledWith("BT-1", { action: "resume" }),
    );
    await waitFor(() => expect(screen.queryByText(/本业务已挂起/)).toBeNull());
  });
});
