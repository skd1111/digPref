/**
 * operationsWorkbench.test.tsx —— 运营工作台选团链路回归（BUGFIX #76）。
 *
 * 背景：WorkspaceLayout 启动时 loadTeams 若因 Agent 未就绪失败（静默吞错），
 * teams 永远为空 → recommend 返回的团匹配不到 → 点「尽调报告初稿」中间区毫无变化。
 * 另：recommend 走 LLM 需数秒，期间必须展示「专家团准备中」动效，避免误判卡死。
 */
import { beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import { OperationsWorkbench } from "@/views/OperationsWorkbench";
import { useExpertTeamStore } from "@/store/expertTeamStore";
import { useOpsNavStore } from "@/store/opsNavStore";
import { useBiznavStore } from "@/store/biznavStore";
import { useSkillsStore } from "@/store/skillsStore";
import { useChatStore } from "@/store/chatStore";
import { useUIStore } from "@/store/uiStore";
import { ipc } from "@/ipc/invoke";

vi.mock("@/ipc/invoke", () => ({
  ipc: {
    biznavListFeatures: vi.fn(),
    biznavStatus: vi.fn(),
    skillsList: vi.fn(),
    expertTeamsList: vi.fn(),
    expertTeamsRecommend: vi.fn(),
    opsCaseGet: vi.fn(),
    opsCaseCrosscheck: vi.fn(),
    opsCreateRecord: vi.fn(),
  },
}));

vi.mock("@tauri-apps/plugin-dialog", () => ({
  save: vi.fn().mockResolvedValue(null),
}));

const TEAM = {
  schema_version: "1.0",
  id: "due_diligence_team",
  name: "尽职调查专家团",
  description: "",
  applicable_scenarios: [],
  trigger_keywords: [],
  enabled: true,
  members: [
    {
      name: "报告撰写专家",
      role: "撰写尽调报告",
      responsibilities: [],
      focus_points: [],
      outputs: ["尽调报告初稿"],
      prompt: "",
    },
  ],
};

/** 可手动兑现的 Promise，用于模拟 recommend 的数秒延迟 */
function deferred<T>() {
  let resolve!: (v: T) => void;
  const promise = new Promise<T>((r) => {
    resolve = r;
  });
  return { promise, resolve };
}

beforeEach(() => {
  vi.clearAllMocks();
  (ipc.biznavListFeatures as ReturnType<typeof vi.fn>).mockResolvedValue({
    project_name: "demo",
    features: [],
    total: 0,
  });
  (ipc.skillsList as ReturnType<typeof vi.fn>).mockResolvedValue({ skills: [] });
  (ipc.opsCaseGet as ReturnType<typeof vi.fn>).mockResolvedValue({
    case_id: "demo__due_report",
    files: [],
    qa: [],
  });
  (ipc.opsCaseCrosscheck as ReturnType<typeof vi.fn>).mockResolvedValue({
    inconsistencies: [],
  });
  useUIStore.setState({ mode: "operator" });
  useBiznavStore.setState({ features: [], projectName: "demo" });
  useSkillsStore.setState({ skills: [] });
  useOpsNavStore.setState({ selectedItemId: null, skillBindings: {} });
  useChatStore.setState({ opsNavContext: null });
  useExpertTeamStore.setState({
    teams: [],
    selectedTeamIds: [],
    selectionMode: "auto",
    selectionSource: "",
    selectedForItemId: null,
    recommending: false,
  });
});

describe("OperationsWorkbench 选团链路（BUGFIX #76）", () => {
  it("teams 为空时进入工作台会兜底重拉专家团列表", async () => {
    (ipc.expertTeamsList as ReturnType<typeof vi.fn>).mockResolvedValue({
      teams: [TEAM],
    });
    const rec = deferred<{ team_ids: string[]; source: string }>();
    (ipc.expertTeamsRecommend as ReturnType<typeof vi.fn>).mockReturnValue(
      rec.promise,
    );

    useOpsNavStore.setState({ selectedItemId: "due_report" });
    render(<OperationsWorkbench />);

    // 兜底重拉被触发，且结果写入 store
    await waitFor(() =>
      expect(ipc.expertTeamsList).toHaveBeenCalled(),
    );
    await waitFor(() =>
      expect(useExpertTeamStore.getState().teams).toHaveLength(1),
    );
  });

  it("推荐进行中显示「专家团准备中」，返回后渲染专家团页签", async () => {
    (ipc.expertTeamsList as ReturnType<typeof vi.fn>).mockResolvedValue({
      teams: [TEAM],
    });
    const rec = deferred<{ team_ids: string[]; source: string }>();
    (ipc.expertTeamsRecommend as ReturnType<typeof vi.fn>).mockReturnValue(
      rec.promise,
    );

    useOpsNavStore.setState({ selectedItemId: "due_report" });
    render(<OperationsWorkbench />);

    // 推荐在途（LLM 数秒）→ 加载动效，不再「点了没反应」
    await waitFor(() => {
      expect(useExpertTeamStore.getState().recommending).toBe(true);
    });
    // 中间区动效 + 选择器状态文案两处都有提示
    expect(screen.getAllByText(/专家团准备中/).length).toBeGreaterThanOrEqual(1);

    // 推荐返回 → 动效消失，专家团页签 + 专家卡渲染出来
    rec.resolve({ team_ids: [TEAM.id], source: "llm" });
    await waitFor(() => {
      expect(screen.getByText(/尽职调查专家团/)).toBeTruthy();
    });
    expect(screen.getByText("报告撰写专家")).toBeTruthy();
    expect(useExpertTeamStore.getState().recommending).toBe(false);
  });

  it("同一业务在途不重复发起推荐（防 skills 加载触发 effect 重跑造成连发）", async () => {
    (ipc.expertTeamsList as ReturnType<typeof vi.fn>).mockResolvedValue({
      teams: [TEAM],
    });
    const rec = deferred<{ team_ids: string[]; source: string }>();
    (ipc.expertTeamsRecommend as ReturnType<typeof vi.fn>).mockReturnValue(
      rec.promise,
    );

    useOpsNavStore.setState({ selectedItemId: "due_report" });
    render(<OperationsWorkbench />);
    await waitFor(() => {
      expect(ipc.expertTeamsRecommend).toHaveBeenCalledTimes(1);
    });

    // 模拟 skills 加载完成（effect 依赖变化重跑）→ 不应再次发起
    useSkillsStore.setState({ skills: [] });
    await new Promise((r) => setTimeout(r, 20));
    expect(ipc.expertTeamsRecommend).toHaveBeenCalledTimes(1);

    rec.resolve({ team_ids: [TEAM.id], source: "llm" });
  });

  it("同业务切模式再切回：直接复用已选专家团，不重跑推荐（防重复加载）", async () => {
    (ipc.expertTeamsList as ReturnType<typeof vi.fn>).mockResolvedValue({
      teams: [TEAM],
    });
    (ipc.expertTeamsRecommend as ReturnType<typeof vi.fn>).mockResolvedValue({
      team_ids: [TEAM.id],
      source: "llm",
    });

    useOpsNavStore.setState({ selectedItemId: "due_report" });
    const first = render(<OperationsWorkbench />);
    await waitFor(() => {
      expect(useExpertTeamStore.getState().selectedTeamIds).toEqual([TEAM.id]);
    });
    expect(ipc.expertTeamsRecommend).toHaveBeenCalledTimes(1);

    // 切到其他模式 → 组件卸载；再切回运营 → 重新挂载
    first.unmount();
    render(<OperationsWorkbench />);
    await new Promise((r) => setTimeout(r, 20));

    // 不重跑推荐，专家团直接沿用；中间区不闪「专家团准备中」
    expect(ipc.expertTeamsRecommend).toHaveBeenCalledTimes(1);
    expect(useExpertTeamStore.getState().recommending).toBe(false);
    // 页签 + 选择器 option 两处都含团名，取 getAll 判存在
    expect(screen.getAllByText(/尽职调查专家团/).length).toBeGreaterThanOrEqual(1);
    expect(screen.queryAllByText(/专家团准备中/)).toHaveLength(0);
  });
});
