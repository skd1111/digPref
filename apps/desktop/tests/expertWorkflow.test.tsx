/**
 * expertWorkflow.test.tsx —— 专家验收工作流面板测试（2026-08-10）。
 *
 * 验证核心业务规则：所有专家交付标准确认 + 有材料 → 才能「生成交付物并导出」；
 * 导出成功后自动生成可审计业务记录。
 */
import { beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { ExpertWorkflowPanel } from "@/components/ops/ExpertWorkflowPanel";
import { useExpertTeamStore } from "@/store/expertTeamStore";
import { useOpsCaseStore } from "@/store/opsCaseStore";
import { useUIStore } from "@/store/uiStore";
import { ipc } from "@/ipc/invoke";

vi.mock("@/ipc/invoke", () => ({
  ipc: {
    opsCaseGet: vi.fn(),
    opsCaseFileAdd: vi.fn(),
    opsCaseFileReview: vi.fn(),
    opsCaseFileOverride: vi.fn(),
    opsCaseFileDelete: vi.fn(),
    opsCaseAsk: vi.fn(),
    opsCaseDraftDirect: vi.fn(),
    opsCaseExport: vi.fn(),
    opsCaseCrosscheck: vi.fn().mockResolvedValue({
      case_id: "bank__ops_open",
      inconsistencies: [],
      low_confidence: [],
      consistent: true,
    }),
    opsCreateRecord: vi.fn(),
    opsCaseDraftSave: vi.fn(),
    opsCaseDraftSubmit: vi.fn(),
    opsCaseFileContent: vi.fn(),
    opsCaseFileSaveAs: vi.fn(),
    opsCaseClear: vi.fn(),
  },
}));

const saveMock = vi.fn();
vi.mock("@tauri-apps/plugin-dialog", () => ({
  save: (...args: unknown[]) => saveMock(...args),
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
  name: "对公开户",
  description: "",
  category: "业务办理",
  skill_id: null,
  expert_team_ids: ["due_diligence_team"],
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

function passedFile() {
  return {
    id: "CF-1",
    case_id: "bank__ops_open",
    team_id: TEAM.id,
    member_key: "客户身份识别专家",
    file_name: "营业执照.txt",
    file_path: "/tmp/营业执照.txt",
    status: "passed" as const,
    review_note: "通过",
    reviewed_by: "ai" as const,
    extracted_fields: [
      { field: "统一社会信用代码", value: "91310000XXXX", confidence: 0.95 },
      { field: "有效期", value: "模糊不清", confidence: 0.3 },
    ],
    evidence: ["91310000XXXX"],
    created_at: 1,
    updated_at: 1,
  };
}

const selection = { staticHit: null, featureHit: FEATURE as never };

/** 草稿区默认收起（BUGFIX #134）：历史草稿需点提醒栏展开后才可见表单 */
function expandDraftPanel(): void {
  fireEvent.click(screen.getByText(/展开填写/).closest("button")!);
}

beforeEach(() => {
  vi.clearAllMocks();
  (ipc.opsCaseGet as ReturnType<typeof vi.fn>).mockResolvedValue({
    case_id: "bank__ops_open",
    files: [],
    qa: [],
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
    files: [passedFile()],
    qa: [],
    drafts: [],
    loading: false,
    error: null,
    busyMembers: {},
    exporting: false,
  });
});

describe("ExpertWorkflowPanel", () => {
  it("渲染专家卡与材料状态", () => {
    render(
      <ExpertWorkflowPanel
        selection={selection}
        projectName="bank"
        onSaveTeamPreset={() => undefined}
      />,
    );
    expect(screen.getByText("客户身份识别专家")).toBeTruthy();
    expect(screen.getByText("营业执照.txt")).toBeTruthy();
    // 关键要素卡：低置信项标红置顶 + 证据链原文摘录
    expect(screen.getByText("关键要素")).toBeTruthy();
    expect(screen.getByText(/⚠ 有效期：模糊不清/)).toBeTruthy();
    expect(screen.getByText("审核依据（原文摘录）")).toBeTruthy();
    expect(screen.getByText("已验收", { exact: false })).toBeFalsy; // 未勾选交付标准前不算验收
    const exportBtn = screen.getByText("📦 生成交付物并导出").closest("button")!;
    expect(exportBtn.disabled).toBe(true);
  });

  it("勾选全部交付标准后导出按钮启用，导出成功生成业务记录", async () => {
    (ipc.opsCaseExport as ReturnType<typeof vi.fn>).mockResolvedValue({
      ok: true,
      path: "C:/out/对公开户-交付物.zip",
      file_count: 1,
    });
    (ipc.opsCreateRecord as ReturnType<typeof vi.fn>).mockResolvedValue({
      id: "OPR-1",
    });
    saveMock.mockResolvedValue("C:/out/对公开户-交付物.zip");
    const alertSpy = vi.spyOn(window, "alert").mockImplementation(() => undefined);

    render(
      <ExpertWorkflowPanel
        selection={selection}
        projectName="bank"
        onSaveTeamPreset={() => undefined}
      />,
    );

    // 勾选交付标准 → 全部验收
    const checkbox = screen.getByRole("checkbox");
    fireEvent.click(checkbox);
    expect(screen.getByText("✓ 全部验收通过")).toBeTruthy();

    const exportBtn = screen.getByText("📦 生成交付物并导出").closest("button")!;
    expect(exportBtn.disabled).toBe(false);
    fireEvent.click(exportBtn);

    await waitFor(() => expect(ipc.opsCaseExport).toHaveBeenCalledTimes(1));
    const exportBody = (ipc.opsCaseExport as ReturnType<typeof vi.fn>).mock.calls[0][0];
    expect(exportBody.target_path).toBe("C:/out/对公开户-交付物.zip");
    expect(exportBody.checklist).toEqual(["客户身份识别专家 → 客户身份基本信息表"]);

    // 自动生成可审计业务记录
    await waitFor(() => expect(ipc.opsCreateRecord).toHaveBeenCalledTimes(1));
    const record = (ipc.opsCreateRecord as ReturnType<typeof vi.fn>).mock.calls[0][0];
    expect(record.result).toBe("done");
    expect(alertSpy).toHaveBeenCalled();
    alertSpy.mockRestore();
  });

  it("取消保存对话框则不导出", async () => {
    saveMock.mockResolvedValue(null);
    render(
      <ExpertWorkflowPanel
        selection={selection}
        projectName="bank"
        onSaveTeamPreset={() => undefined}
      />,
    );
    fireEvent.click(screen.getByRole("checkbox"));
    const exportBtn = screen.getByText("📦 生成交付物并导出").closest("button")!;
    fireEvent.click(exportBtn);
    await waitFor(() => expect(saveMock).toHaveBeenCalled());
    expect(ipc.opsCaseExport).not.toHaveBeenCalled();
  });
});

// ---------------------------------------------------------------------------
// 交付草稿（BUGFIX #78）：要「模板/清单」不给一大段文字，界面直填
// ---------------------------------------------------------------------------

const DRAFT = {
  id: "DR-1",
  case_id: "bank__ops_open",
  team_id: TEAM.id,
  member_key: "客户身份识别专家",
  title: "对公开户资料清单",
  template: [
    { name: "corp_name", label: "企业名称", type: "text", required: true },
    { name: "remark", label: "备注", type: "textarea", required: false },
  ],
  values: {},
  status: "draft" as const,
  file_id: "",
  created_at: 1,
  updated_at: 1,
};

describe("交付草稿表单（BUGFIX #78）", () => {
  it("必填未完成时提交按钮禁用，填写后可提交并自动入审核", async () => {
    (ipc.opsCaseDraftSave as ReturnType<typeof vi.fn>).mockImplementation(
      async (_id: string, body: { values: Record<string, string> }) => ({
        ...DRAFT,
        values: body.values,
      }),
    );
    (ipc.opsCaseDraftSubmit as ReturnType<typeof vi.fn>).mockResolvedValue({
      draft: { ...DRAFT, status: "passed", file_id: "CF-DRAFT" },
      file: {
        id: "CF-DRAFT",
        case_id: "bank__ops_open",
        team_id: TEAM.id,
        member_key: "客户身份识别专家",
        file_name: "对公开户资料清单.md",
        file_path: "/tmp/x.md",
        status: "passed",
        review_note: "资料齐全",
        reviewed_by: "ai",
        created_at: 1,
        updated_at: 1,
      },
    });
    // 面板挂载会 loadCase：opsCaseGet 响应必须带上草稿，否则被空列表覆盖
    (ipc.opsCaseGet as ReturnType<typeof vi.fn>).mockResolvedValue({
      case_id: "bank__ops_open",
      files: [],
      qa: [],
      drafts: [DRAFT],
    });
    useOpsCaseStore.setState({ drafts: [DRAFT as never] });

    render(
      <ExpertWorkflowPanel
        selection={selection}
        projectName="bank"
        onSaveTeamPreset={() => undefined}
      />,
    );

    expect(screen.getByText(/交付草稿/)).toBeTruthy();
    // 历史草稿默认收起（BUGFIX #134），点提醒栏展开后才见表单
    expandDraftPanel();
    // 页签 + 草稿卡标题各一处
    expect(screen.getAllByText("对公开户资料清单").length).toBeGreaterThanOrEqual(1);

    // 必填未完成 → 提交禁用
    const submitBtn = screen.getByText("提交给专家审核").closest("button")!;
    expect(submitBtn.disabled).toBe(true);

    // 填写企业名称 → 可提交（专家卡内也有提问输入框，按无 placeholder 区分）
    const draftInputs = screen
      .getAllByRole("textbox")
      .filter((el) => !(el as HTMLInputElement).placeholder);
    fireEvent.change(draftInputs[0], { target: { value: "某某贸易有限公司" } });
    expect(submitBtn.disabled).toBe(false);
    fireEvent.click(submitBtn);

    await waitFor(() => expect(ipc.opsCaseDraftSubmit).toHaveBeenCalledTimes(1));
    // 提交前先暂存填写值
    expect(ipc.opsCaseDraftSave).toHaveBeenCalled();
    // 通过后展示已入交付物徽标
    await waitFor(() =>
      expect(screen.getByText(/已验收 · 已入交付物/)).toBeTruthy(),
    );
  });

  it("自动预填字段展示「自动预填」徽标供核对（BUGFIX #79）", () => {
    const prefilled = { ...DRAFT, values: { corp_name: "某某贸易有限公司" } };
    (ipc.opsCaseGet as ReturnType<typeof vi.fn>).mockResolvedValue({
      case_id: "bank__ops_open",
      files: [],
      qa: [],
      drafts: [prefilled],
    });
    useOpsCaseStore.setState({ drafts: [prefilled as never] });
    render(
      <ExpertWorkflowPanel
        selection={selection}
        projectName="bank"
        onSaveTeamPreset={() => undefined}
      />,
    );
    expandDraftPanel();
    expect(screen.getByText("自动预填")).toBeTruthy();
  });
});

// ---------------------------------------------------------------------------
// 草稿区默认收起（BUGFIX #134）：启动恢复的历史草稿不自动铺屏，
// 只留提醒栏；本会话新到达的草稿才自动展开
// ---------------------------------------------------------------------------

describe("草稿区默认收起（BUGFIX #134）", () => {
  it("启动恢复的历史草稿默认收起为提醒栏，表单不自动出现", () => {
    (ipc.opsCaseGet as ReturnType<typeof vi.fn>).mockResolvedValue({
      case_id: "bank__ops_open",
      files: [],
      qa: [],
      drafts: [DRAFT],
    });
    useOpsCaseStore.setState({ drafts: [DRAFT as never] });
    render(
      <ExpertWorkflowPanel
        selection={selection}
        projectName="bank"
        onSaveTeamPreset={() => undefined}
      />,
    );
    // 提醒栏可见，表单控件与提交按钮不出现
    expect(screen.getByText(/展开填写/)).toBeTruthy();
    expect(screen.queryByText("提交给专家审核")).toBeNull();
    // 点开提醒栏 → 表单出现，可再收起
    expandDraftPanel();
    expect(screen.getByText("提交给专家审核")).toBeTruthy();
    fireEvent.click(screen.getByText(/▾ 收起/).closest("button")!);
    expect(screen.queryByText("提交给专家审核")).toBeNull();
  });

  it("专家回答附带的新草稿到达后草稿区自动展开", async () => {
    (ipc.opsCaseAsk as ReturnType<typeof vi.fn>).mockResolvedValue({
      qa: { id: "QA-1", question: "要清单", answer: "好的", created_at: 1 },
      draft: DRAFT,
    });
    render(
      <ExpertWorkflowPanel
        selection={selection}
        projectName="bank"
        onSaveTeamPreset={() => undefined}
      />,
    );
    // 无草稿时连提醒栏都没有
    expect(screen.queryByText(/展开填写/)).toBeNull();
    // 通过专家卡提问框发起提问（专家卡输入框带 placeholder，按此区分）
    const askInput = screen.getByPlaceholderText(/提问…（回车发送）/);
    fireEvent.change(askInput, { target: { value: "给我一份资料清单" } });
    fireEvent.keyDown(askInput, { key: "Enter" });
    // 新草稿到达 → 自动展开，无需点提醒栏（此时也不存在提醒栏）
    await waitFor(() => {
      expect(screen.queryByText(/展开填写/)).toBeNull();
      expect(screen.getByText("提交给专家审核")).toBeTruthy();
    });
  });
});

// ---------------------------------------------------------------------------
// 打回定位（BUGFIX #80）：文档内高亮问题位置 + 修改建议
// ---------------------------------------------------------------------------

describe("打回定位（BUGFIX #80）", () => {
  it("打回材料显示定位入口，点开弹窗高亮问题行与建议", async () => {
    const rejectedFile = {
      ...passedFile(),
      id: "CF-REJ",
      status: "rejected" as const,
      review_note: "执照已过期",
      reject_marks: [{ quote: "LICENSE EXPIRED", advice: "请更换有效期内的执照" }],
    };
    // 面板挂载会 loadCase：响应必须带上该文件，否则异步返回后覆盖 store 导致弹窗消失
    (ipc.opsCaseGet as ReturnType<typeof vi.fn>).mockResolvedValue({
      case_id: "bank__ops_open",
      files: [rejectedFile],
      qa: [],
      drafts: [],
    });
    (ipc.opsCaseFileContent as ReturnType<typeof vi.fn>).mockResolvedValue({
      file_name: "执照.txt",
      content_base64: btoa("COPY OF LICENSE\nLICENSE EXPIRED"),
    });
    useOpsCaseStore.setState({
      files: [rejectedFile as never],
      drafts: [],
    });

    render(
      <ExpertWorkflowPanel
        selection={selection}
        projectName="bank"
        onSaveTeamPreset={() => undefined}
      />,
    );

    // 入口按钮
    const entry = screen.getByText(/1 处打回/);
    fireEvent.click(entry);

    // 弹窗标题与逐条清单（内容异步加载，统一 waitFor）
    await waitFor(() => expect(screen.getByText(/打回位置/)).toBeTruthy());
    await waitFor(() =>
      expect(screen.getAllByText(/请更换有效期内的执照/).length).toBeGreaterThanOrEqual(1),
    );
  });
});

// ---------------------------------------------------------------------------
// 草稿增强（BUGFIX #85）：file 字段用上传控件 + 重新开始办理
// ---------------------------------------------------------------------------

describe("草稿增强（BUGFIX #85）", () => {
  it("file 类型字段渲染为上传控件而非文本框", async () => {
    const withFile = {
      ...DRAFT,
      template: [
        { name: "id_card", label: "法人身份证", type: "file", required: true },
        { name: "corp_name", label: "企业名称", type: "text", required: false },
      ],
    };
    (ipc.opsCaseGet as ReturnType<typeof vi.fn>).mockResolvedValue({
      case_id: "bank__ops_open",
      files: [],
      qa: [],
      drafts: [withFile],
    });
    useOpsCaseStore.setState({ drafts: [withFile as never] });
    render(
      <ExpertWorkflowPanel
        selection={selection}
        projectName="bank"
        onSaveTeamPreset={() => undefined}
      />,
    );
    expandDraftPanel();
    // 上传控件文案存在；file 字段不是文本框（逐字段显现动效，等第二字段出现）
    expect(screen.getByText(/点击选择文件/)).toBeTruthy();
    await waitFor(() => {
      const boxes = screen
        .getAllByRole("textbox")
        .filter((el) => !(el as HTMLInputElement).placeholder);
      expect(boxes).toHaveLength(1);
    });
  });

  it("重新开始办理：确认后调用清空并重置", async () => {
    const confirmSpy = vi.spyOn(window, "confirm").mockReturnValue(true);
    (ipc.opsCaseClear as ReturnType<typeof vi.fn>).mockResolvedValue({
      ok: true,
      case_id: "bank__ops_open",
      files: 1,
      qa: 1,
      drafts: 1,
    });
    (ipc.opsCaseGet as ReturnType<typeof vi.fn>).mockResolvedValue({
      case_id: "bank__ops_open",
      files: [],
      qa: [],
      drafts: [],
    });
    render(
      <ExpertWorkflowPanel
        selection={selection}
        projectName="bank"
        onSaveTeamPreset={() => undefined}
      />,
    );
    fireEvent.click(screen.getByText(/重新开始办理/));
    await waitFor(() => expect(ipc.opsCaseClear).toHaveBeenCalledTimes(1));
    expect(confirmSpy).toHaveBeenCalled();
    confirmSpy.mockRestore();
  });
});

// ---------------------------------------------------------------------------
// 草稿页签 + 审核交互（BUGFIX #86/#87）
// ---------------------------------------------------------------------------

describe("草稿页签与审核交互（BUGFIX #86/#87）", () => {
  const draftA = { ...DRAFT, id: "DR-A", title: "资料清单A" };
  const draftB = {
    ...DRAFT,
    id: "DR-B",
    title: "任务书B",
    status: "submitted" as const,
    file_id: "CF-B",
    values: { corp_name: "甲公司", license_no: "91310000XXXX" },
  };
  const fileB = {
    ...passedFile(),
    id: "CF-B",
    status: "rejected" as const,
    review_note: "营业执照已过有效期\n信用代码位数不清晰",
  };

  function renderTwoDrafts() {
    (ipc.opsCaseGet as ReturnType<typeof vi.fn>).mockResolvedValue({
      case_id: "bank__ops_open",
      files: [fileB],
      qa: [],
      drafts: [draftA, draftB],
    });
    useOpsCaseStore.setState({
      files: [fileB as never],
      drafts: [draftA as never, draftB as never],
    });
    const view = render(
      <ExpertWorkflowPanel
        selection={selection}
        projectName="bank"
        onSaveTeamPreset={() => undefined}
      />,
    );
    expandDraftPanel();
    return view;
  }

  it("多草稿用页签切换不接龙，默认停在最新草稿", () => {
    renderTwoDrafts();
    expect(screen.getAllByText("资料清单A").length).toBeGreaterThanOrEqual(1);
    expect(screen.getAllByText("任务书B").length).toBeGreaterThanOrEqual(1);
    // 默认激活最后一个草稿（B）→ 其审核意见区可见
    expect(screen.getByText(/专家审核意见（2 条）/)).toBeTruthy();
    // 切换到 A → B 的意见区消失（不接龙，同一时刻只展示一份）
    fireEvent.click(screen.getAllByText("资料清单A")[0]);
    expect(screen.queryByText(/专家审核意见/)).toBeNull();
  });

  it("草稿审核意见逐条展示且材料行不重复气泡", () => {
    renderTwoDrafts();
    expect(screen.getByText(/营业执照已过有效期/)).toBeTruthy();
    expect(screen.getByText(/信用代码位数不清晰/)).toBeTruthy();
    // 意见只在草稿卡出现一次（材料行的气泡已收起）
    expect(screen.getAllByText(/营业执照已过有效期/)).toHaveLength(1);
  });

  it("草稿关联材料审核中时草稿卡展示 thinking", async () => {
    const reviewingFile = { ...fileB, status: "reviewing" as const, review_note: "" };
    (ipc.opsCaseGet as ReturnType<typeof vi.fn>).mockResolvedValue({
      case_id: "bank__ops_open",
      files: [reviewingFile],
      qa: [],
      drafts: [draftB],
    });
    useOpsCaseStore.setState({
      files: [reviewingFile as never],
      drafts: [draftB as never],
    });
    render(
      <ExpertWorkflowPanel
        selection={selection}
        projectName="bank"
        onSaveTeamPreset={() => undefined}
      />,
    );
    expandDraftPanel();
    await waitFor(() => expect(screen.getByText(/正在审核草稿/)).toBeTruthy());
  });
});

// ---------------------------------------------------------------------------
// 全屏表单设计重构（BUGFIX #93）：页头状态色条 + 必填进度 + 常驻操作页脚
// ---------------------------------------------------------------------------

describe("全屏草稿表单（BUGFIX #93）", () => {
  const renderFullscreen = async () => {
    (ipc.opsCaseGet as ReturnType<typeof vi.fn>).mockResolvedValue({
      case_id: "bank__ops_open",
      files: [],
      qa: [],
      drafts: [DRAFT],
    });
    useOpsCaseStore.setState({ drafts: [DRAFT as never] });
    render(
      <ExpertWorkflowPanel
        selection={selection}
        projectName="bank"
        onSaveTeamPreset={() => undefined}
      />,
    );
    expandDraftPanel();
    fireEvent.click(screen.getByText(/⛶ 全屏/));
    await waitFor(() => expect(screen.getByText(/✕ 退出全屏/)).toBeTruthy());
  };

  it("全屏页头含副题与必填进度，页脚常驻未完成提示", async () => {
    await renderFullscreen();
    // 副题：出题专家 + 项数 + 必填数
    expect(screen.getByText(/共 2 项 · 必填 1 项/)).toBeTruthy();
    // 进度分数（未填；团页签也可能有同形计数，取至少一处）
    expect(screen.getAllByText("0/1").length).toBeGreaterThanOrEqual(1);
    // 页脚未完成提示
    expect(screen.getByText(/还有 1 项必填未完成/)).toBeTruthy();
  });

  it("填完必填后进度满格，页脚切换为可提交提示", async () => {
    await renderFullscreen();
    // 全屏内企业名称输入框（无 placeholder 的 textbox）
    await waitFor(() => {
      const boxes = screen
        .getAllByRole("textbox")
        .filter((el) => !(el as HTMLInputElement).placeholder);
      expect(boxes.length).toBeGreaterThanOrEqual(1);
    });
    const input = screen
      .getAllByRole("textbox")
      .filter((el) => !(el as HTMLInputElement).placeholder)[0];
    fireEvent.change(input, { target: { value: "某某贸易有限公司" } });
    expect(screen.getAllByText("1/1").length).toBeGreaterThanOrEqual(1);
    expect(screen.getByText(/必填项已完成，可提交专家审核/)).toBeTruthy();
  });
});

// ---------------------------------------------------------------------------
// 点交付物直开表单（2026-08-14）：有模板零 LLM 直建草稿；无模板自动问专家
// ---------------------------------------------------------------------------

describe("点交付物直开表单（2026-08-14）", () => {
  const DIRECT_DRAFT = {
    id: "DR-DIRECT",
    case_id: "bank__ops_open",
    team_id: TEAM.id,
    member_key: "客户身份识别专家",
    title: "客户身份基本信息表",
    template: [
      { name: "corp_name", label: "企业名称", type: "text", required: true },
      { name: "legal_person", label: "法定代表人", type: "text", required: false },
    ],
    values: {},
    status: "draft" as const,
    file_id: "",
    created_at: 1,
    updated_at: 1,
  };

  function seedTeamWithForm(outputs: string[]): void {
    const teamWithForm = {
      ...TEAM,
      members: [
        {
          ...TEAM.members[0],
          outputs,
          output_forms:
            outputs.includes("客户身份基本信息表")
              ? {
                  客户身份基本信息表: [
                    { name: "corp_name", label: "企业名称", type: "text", required: true },
                  ],
                }
              : {},
        },
      ],
    };
    useExpertTeamStore.setState({ teams: [teamWithForm as never] });
  }

  it("有模板的交付物：点击零 LLM 直建草稿，不再走问专家", async () => {
    seedTeamWithForm(["客户身份基本信息表"]);
    (ipc.opsCaseDraftDirect as ReturnType<typeof vi.fn>).mockResolvedValue({
      draft: DIRECT_DRAFT,
      reused: false,
    });
    render(
      <ExpertWorkflowPanel
        selection={selection}
        projectName="bank"
        onSaveTeamPreset={() => undefined}
      />,
    );
    const btn = await screen.findByRole("button", { name: /客户身份基本信息表/ });
    fireEvent.click(btn);
    await waitFor(() =>
      expect(ipc.opsCaseDraftDirect).toHaveBeenCalledWith({
        case_id: "bank__ops_open",
        team_id: TEAM.id,
        member_key: "客户身份识别专家",
        output_name: "客户身份基本信息表",
      }),
    );
    // 未走问专家链路
    expect(ipc.opsCaseAsk).not.toHaveBeenCalled();
    // 草稿入 store 且标记为直开目标（面板据此自动全屏）
    await waitFor(() => {
      const st = useOpsCaseStore.getState();
      expect(st.drafts.some((d: { id: string }) => d.id === "DR-DIRECT")).toBe(true);
      expect(st.lastDirectDraftId).toBe("DR-DIRECT");
    });
  });

  it("无模板的交付物：点击自动拼好提问发给该专家（免手动输入）", async () => {
    seedTeamWithForm(["身份风险疑点清单"]);
    (ipc.opsCaseAsk as ReturnType<typeof vi.fn>).mockResolvedValue({
      qa: { id: "QA-1", question: "x", answer: "y", created_at: 1 },
      draft: null,
    });
    render(
      <ExpertWorkflowPanel
        selection={selection}
        projectName="bank"
        onSaveTeamPreset={() => undefined}
      />,
    );
    const btn = await screen.findByRole("button", { name: /身份风险疑点清单/ });
    fireEvent.click(btn);
    await waitFor(() =>
      expect(ipc.opsCaseAsk).toHaveBeenCalledWith(
        expect.objectContaining({
          member_key: "客户身份识别专家",
          question: expect.stringContaining("请帮我完成交付物「身份风险疑点清单」"),
        }),
      ),
    );
    expect(ipc.opsCaseDraftDirect).not.toHaveBeenCalled();
  });
});
