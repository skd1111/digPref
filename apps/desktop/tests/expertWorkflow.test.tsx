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
    opsCaseDraftOcrFill: vi.fn(),
    opsCaseFileContent: vi.fn(),
    opsCaseFileSaveAs: vi.fn(),
    opsCaseClear: vi.fn(),
    // 业务台账（业务汇总）：草稿 OCR 上传 / 草稿内文件随提交上传时补建档（BUGFIX #212）
    opsTaskStart: vi.fn(),
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
    review_note: "材料齐全，与系统登记一致",
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
  fireEvent.click(screen.getByText(/继续填写/).closest("button")!);
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
    // 直开标记跨测试不能沿用（否则下一个用例一进来交付物区就自动展开）
    lastDirectDraftId: "",
    lastNewDraftId: "",
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
    // 断言限定在专家卡内：专家名也可能出现在其他区域（如交付物页签区的出处专家）
    expect(
      document.getElementById(`expert-card-${TEAM.id}-客户身份识别专家`)
        ?.textContent,
    ).toContain("客户身份识别专家");
    expect(screen.getByText("营业执照.txt")).toBeTruthy();
    // 材料行默认只给结论 + 结果 + 两个入口（2026-11 交互优化）：
    // 审核意见直接可见，关键要素/证据链点「查看审核明细」才展开
    expect(screen.getByText(/材料齐全/)).toBeTruthy();
    expect(screen.queryByText("关键要素")).toBeNull();
    fireEvent.click(screen.getByText(/查看审核明细/));
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

  it("审核中的材料给出「重审」出口（BUGFIX #202：不让用户对着转圈干等）", async () => {
    const reviewing = {
      ...passedFile(),
      id: "CF-REVIEWING",
      status: "reviewing" as const,
      reviewed_by: "" as const,
      review_note: "",
    };
    (ipc.opsCaseGet as ReturnType<typeof vi.fn>).mockResolvedValue({
      case_id: "bank__ops_open",
      files: [reviewing],
      qa: [],
      drafts: [],
    });
    (ipc.opsCaseFileReview as ReturnType<typeof vi.fn>).mockResolvedValue({
      ...reviewing,
      status: "passed",
      review_note: "审核通过",
      reviewed_by: "ai",
    });
    useOpsCaseStore.setState({ files: [reviewing as never] });

    render(
      <ExpertWorkflowPanel
        selection={selection}
        projectName="bank"
        onSaveTeamPreset={() => undefined}
      />,
    );

    // 展开材料详情 → 「↻ 重审」可见（审核中也能点）
    fireEvent.click(screen.getByText("营业执照.txt"));
    const reReviewBtn = screen.getByText("↻ 重审").closest("button")!;
    expect(reReviewBtn).toBeTruthy();
    fireEvent.click(reReviewBtn);
    await waitFor(() => expect(ipc.opsCaseFileReview).toHaveBeenCalledTimes(1));
    expect((ipc.opsCaseFileReview as ReturnType<typeof vi.fn>).mock.calls[0][0]).toBe(
      "CF-REVIEWING",
    );
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

    // 填写企业名称 → 可提交（专家卡内提问输入框带 placeholder，按无 placeholder 区分）
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
// 上传文件 OCR 自动填入（2026-11-05）：所有专家录入表单通用
// ---------------------------------------------------------------------------

describe("上传文件 OCR 自动填入", () => {
  it("选文件后自动填入字段并标 OCR 徽标，可一键撤销", async () => {
    (ipc.opsCaseDraftSave as ReturnType<typeof vi.fn>).mockImplementation(
      async (_id: string, body: { values: Record<string, string> }) => ({
        ...DRAFT,
        values: body.values,
      }),
    );
    (ipc.opsCaseDraftOcrFill as ReturnType<typeof vi.fn>).mockResolvedValue({
      values: { corp_name: "某某贸易有限公司" },
      filled: ["corp_name"],
      engine: "document",
      source: "llm",
      text_chars: 26,
      text_preview: "企业名称：某某贸易有限公司",
    });
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

    // OCR 专用文件选择框（带 accept 白名单，区别于专家卡材料上传框）
    const fileInput = document.querySelector(
      'input[type="file"][accept*=".pdf"]',
    ) as HTMLInputElement;
    fireEvent.change(fileInput, {
      target: {
        files: [new File(["企业名称：某某贸易有限公司"], "执照.txt", { type: "text/plain" })],
      },
    });

    await waitFor(() => expect(ipc.opsCaseDraftOcrFill).toHaveBeenCalledTimes(1));
    await waitFor(() => expect(screen.getByText("OCR 填入")).toBeTruthy());
    const textInputs = screen
      .getAllByRole("textbox")
      .filter((el) => !(el as HTMLInputElement).placeholder);
    expect((textInputs[0] as HTMLInputElement).value).toBe("某某贸易有限公司");
    // 自动填入后立即暂存，刷新/切页不丢
    expect(ipc.opsCaseDraftSave).toHaveBeenCalledWith("DR-1", {
      values: { corp_name: "某某贸易有限公司" },
    });

    // 撤销本次识别：值回退、徽标移除
    fireEvent.click(screen.getByText(/撤销本次识别/).closest("button")!);
    expect((textInputs[0] as HTMLInputElement).value).toBe("");
    expect(screen.queryByText("OCR 填入")).toBeNull();
  });

  it("草稿 OCR 上传也算业务发起：自动补建档（否则挂起/业务汇总不可用）", async () => {
    (ipc.opsTaskStart as ReturnType<typeof vi.fn>).mockResolvedValue({
      id: "BT-OCR",
      case_id: "bank__ops_open",
      status: "in_progress",
      status_label: "正在办理",
      pending_items: [],
    });
    (ipc.opsCaseDraftOcrFill as ReturnType<typeof vi.fn>).mockResolvedValue({
      values: { corp_name: "某某贸易有限公司" },
      filled: ["corp_name"],
      engine: "document",
      source: "llm",
      text_chars: 26,
      text_preview: "企业名称：某某贸易有限公司",
    });
    (ipc.opsCaseGet as ReturnType<typeof vi.fn>).mockResolvedValue({
      case_id: "bank__ops_open",
      files: [],
      qa: [],
      drafts: [DRAFT],
      task: null,
    });
    useOpsCaseStore.setState({ drafts: [DRAFT as never], task: null });

    render(
      <ExpertWorkflowPanel
        selection={selection}
        projectName="bank"
        onSaveTeamPreset={() => undefined}
      />,
    );
    expandDraftPanel();
    // 等 Case 加载完成（否则 loadCase 返回值会把刚建的台账覆盖回 null）
    await waitFor(() => expect(useOpsCaseStore.getState().loading).toBe(false));

    const fileInput = document.querySelector(
      'input[type="file"][accept*=".pdf"]',
    ) as HTMLInputElement;
    fireEvent.change(fileInput, {
      target: {
        files: [new File(["企业名称：某某贸易有限公司"], "执照.txt", { type: "text/plain" })],
      },
    });

    await waitFor(() => expect(ipc.opsCaseDraftOcrFill).toHaveBeenCalledTimes(1));
    // 首次用 OCR 填单即视为业务发起 → 建档
    await waitFor(() => expect(ipc.opsTaskStart).toHaveBeenCalledTimes(1));
    expect(useOpsCaseStore.getState().task?.id).toBe("BT-OCR");
  });
});

// ---------------------------------------------------------------------------
// 系统合规审核报告（2026-11-05）：专家审核前先过文档审核模块，
// 有风险点自动弹报告（附原文证据），无风险不弹窗
// ---------------------------------------------------------------------------

function riskReportFixture() {
  return {
    audited: true,
    degraded: false,
    error: "",
    basis: "document",
    doc_category: "contract",
    risk_types: ["legal"],
    overall_risk_level: "high",
    findings: [
      {
        finding_id: "F-1",
        risk_type: "legal",
        risk_level: "high",
        title: "单方最终解释权",
        description: "条款剥夺相对方解释权，存在无效风险",
        suggestion: "删除该条或改为双方协商解释",
        rule_ref: null,
        evidence_text: "甲方享有最终解释权",
        positions: [{ page_no: 1, block_id: "p1b1", start: 0, end: 9 }],
        kb_refs: [],
      },
    ],
    elapsed_ms: 1200,
    audited_at: 1,
  };
}

describe("系统合规审核报告", () => {
  function pendingFile() {
    return { ...passedFile(), id: "CF-PENDING", status: "pending" as const };
  }

  it("审核发现风险点 → 自动弹报告（含原文证据），关闭后可从材料行回看", async () => {
    const risky = {
      ...passedFile(),
      id: "CF-PENDING",
      status: "rejected" as const,
      risk_report: riskReportFixture(),
    };
    const pending = pendingFile();
    (ipc.opsCaseGet as ReturnType<typeof vi.fn>).mockResolvedValue({
      case_id: "bank__ops_open",
      files: [pending],
      qa: [],
      drafts: [],
    });
    (ipc.opsCaseFileReview as ReturnType<typeof vi.fn>).mockResolvedValue(risky);
    useOpsCaseStore.setState({ files: [pending as never], drafts: [], riskAuditFileId: "" });

    render(
      <ExpertWorkflowPanel
        selection={selection}
        projectName="bank"
        onSaveTeamPreset={() => undefined}
      />,
    );
    await waitFor(() => expect(screen.getByText("营业执照.txt")).toBeTruthy());
    // 材料详情默认折叠，展开后才有点「重审」入口
    fireEvent.click(screen.getByText("营业执照.txt"));
    await waitFor(() => expect(screen.getByText("↻ 重审")).toBeTruthy());
    fireEvent.click(screen.getByText("↻ 重审"));

    await waitFor(() =>
      expect(screen.getByText(/系统合规审核发现 1 项风险/)).toBeTruthy(),
    );
    // 风险点 + 原文证据必须同时出现（需求：报告附原文证据）
    expect(screen.getByText(/单方最终解释权/)).toBeTruthy();
    expect(screen.getByText("甲方享有最终解释权")).toBeTruthy();
    expect(screen.getByText(/处理建议：删除该条或改为双方协商解释/)).toBeTruthy();

    // 关闭后不再自动弹；材料行保留「合规风险 1」入口可回看
    fireEvent.click(screen.getByText("知道了"));
    expect(screen.queryByText(/系统合规审核发现/)).toBeNull();
    expect(useOpsCaseStore.getState().riskAuditFileId).toBe("");
    fireEvent.click(screen.getByText(/合规风险 1/).closest("button")!);
    expect(screen.getByText(/系统合规审核发现 1 项风险/)).toBeTruthy();
  });

  it("审核无风险点 → 不弹窗", async () => {
    const clean = {
      ...passedFile(),
      id: "CF-PENDING",
      status: "passed" as const,
      risk_report: { ...riskReportFixture(), findings: [], overall_risk_level: "" },
    };
    const pending = pendingFile();
    (ipc.opsCaseGet as ReturnType<typeof vi.fn>).mockResolvedValue({
      case_id: "bank__ops_open",
      files: [pending],
      qa: [],
      drafts: [],
    });
    (ipc.opsCaseFileReview as ReturnType<typeof vi.fn>).mockResolvedValue(clean);
    useOpsCaseStore.setState({ files: [pending as never], drafts: [], riskAuditFileId: "" });

    render(
      <ExpertWorkflowPanel
        selection={selection}
        projectName="bank"
        onSaveTeamPreset={() => undefined}
      />,
    );
    await waitFor(() => expect(screen.getByText("营业执照.txt")).toBeTruthy());
    // 材料详情默认折叠，展开后才有点「重审」入口
    fireEvent.click(screen.getByText("营业执照.txt"));
    await waitFor(() => expect(screen.getByText("↻ 重审")).toBeTruthy());
    fireEvent.click(screen.getByText("↻ 重审"));
    await waitFor(() => expect(ipc.opsCaseFileReview).toHaveBeenCalledTimes(1));
    expect(screen.queryByText(/系统合规审核发现/)).toBeNull();
    expect(screen.queryByText(/合规风险/)).toBeNull();
  });

  it("材料行默认只给结论 + 两个入口：合规风险是独立按钮，审核明细点开才展开", async () => {
    const risky = {
      ...passedFile(),
      id: "CF-RISK",
      status: "rejected" as const,
      review_note: "统一社会信用代码未提供",
      risk_report: riskReportFixture(),
    };
    (ipc.opsCaseGet as ReturnType<typeof vi.fn>).mockResolvedValue({
      case_id: "bank__ops_open",
      files: [risky],
      qa: [],
      drafts: [],
    });
    useOpsCaseStore.setState({ files: [risky as never], drafts: [], riskAuditFileId: "" });

    render(
      <ExpertWorkflowPanel
        selection={selection}
        projectName="bank"
        onSaveTeamPreset={() => undefined}
      />,
    );
    await waitFor(() => expect(screen.getByText("营业执照.txt")).toBeTruthy());
    // 结论直显：驳回原因不用展开就能看到
    expect(screen.getByText(/统一社会信用代码未提供/)).toBeTruthy();
    // 审核明细默认收起：关键要素/证据链不铺屏
    expect(screen.queryByText("关键要素")).toBeNull();
    expect(screen.queryByText("审核依据（原文摘录）")).toBeNull();
    // 合规风险是独立按钮（走 .btn 体系），一点就开报告
    const riskBtn = screen.getByText(/合规风险 1/).closest("button")!;
    expect(riskBtn.className).toContain("btn");
    fireEvent.click(riskBtn);
    await waitFor(() => expect(screen.getByText(/系统合规审核发现 1 项风险/)).toBeTruthy());
    fireEvent.click(screen.getByText("知道了"));
    // 明细入口存在，点开才见关键要素
    fireEvent.click(screen.getByText(/查看审核明细/));
    expect(screen.getByText("关键要素")).toBeTruthy();
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
    expect(screen.getByText(/继续填写/)).toBeTruthy();
    expect(screen.queryByText("提交给专家审核")).toBeNull();
    // 点开提醒栏 → 表单出现，可再收起
    expandDraftPanel();
    expect(screen.getByText("提交给专家审核")).toBeTruthy();
    fireEvent.click(screen.getByText(/收起/).closest("button")!);
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
    expect(screen.queryByText(/继续填写/)).toBeNull();
    // 通过专家卡提问框发起提问（专家卡输入框带 placeholder，按此区分）
    // 提问框默认收起（2026-11 交互优化），点「追问」展开后才输入
    fireEvent.click(screen.getByTitle(/向客户身份识别专家追问/));
    const askInput = screen.getByPlaceholderText(/提问…（回车发送）/);
    fireEvent.change(askInput, { target: { value: "给我一份资料清单" } });
    fireEvent.keyDown(askInput, { key: "Enter" });
    // 新草稿到达 → 自动展开，无需点提醒栏（此时也不存在提醒栏）
    await waitFor(() => {
      expect(screen.queryByText(/继续填写/)).toBeNull();
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

    // 入口按钮在审核明细里（材料行默认只给结论 + 两个入口）
    fireEvent.click(screen.getByText(/查看审核明细/));
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
    // 危险操作收在「⋯」溢出菜单（2026-11 交互优化）
    fireEvent.click(screen.getByTitle("更多操作"));
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
// 直开草稿专家卡下方展开（2026-11-09）：点交付物不再铺满全屏，
// 交付物页签区在专家卡下面铺平（替代原右侧抽屉）
// ---------------------------------------------------------------------------

describe("直开草稿专家卡下方展开（2026-11-09）", () => {
  const draftA = { ...DRAFT, id: "DR-A", title: "资料预审意见书" };
  const draftB = { ...DRAFT, id: "DR-B", title: "资料预审信息及补件申请表" };

  it("点交付物直开草稿只展开专家卡下方页签区，不自动全屏", async () => {
    (ipc.opsCaseGet as ReturnType<typeof vi.fn>).mockResolvedValue({
      case_id: "bank__ops_open",
      files: [],
      qa: [],
      drafts: [draftA, draftB],
    });
    useOpsCaseStore.setState({
      drafts: [draftA as never, draftB as never],
      lastDirectDraftId: "DR-A",
      lastNewDraftId: "DR-A",
    });
    render(
      <ExpertWorkflowPanel
        selection={selection}
        projectName="bank"
        onSaveTeamPreset={() => undefined}
      />,
    );
    // 交付物区自动打开（页签区头部提示可见），且没有全屏页头
    await waitFor(() =>
      expect(screen.getByText(/填写 → 提交专家审核/)).toBeTruthy(),
    );
    expect(screen.queryByText(/✕ 退出全屏/)).toBeNull();
    // 切换草稿页签来回：仍是下方页签区，不弹全屏
    fireEvent.click(screen.getAllByText("资料预审信息及补件申请表")[0]);
    fireEvent.click(screen.getAllByText("资料预审意见书")[0]);
    expect(screen.queryByText(/✕ 退出全屏/)).toBeNull();
    expect(screen.getByText(/📝 交付草稿/)).toBeTruthy();
  });

  it("离开再回到业务办理（组件重挂）不再自动弹交付物区", async () => {
    (ipc.opsCaseGet as ReturnType<typeof vi.fn>).mockResolvedValue({
      case_id: "bank__ops_open",
      files: [],
      qa: [],
      drafts: [draftA, draftB],
    });
    useOpsCaseStore.setState({
      drafts: [draftA as never, draftB as never],
      lastDirectDraftId: "DR-A",
      lastNewDraftId: "DR-A",
    });
    const first = render(
      <ExpertWorkflowPanel
        selection={selection}
        projectName="bank"
        onSaveTeamPreset={() => undefined}
      />,
    );
    // 首次直开：交付物区自动展开
    await waitFor(() =>
      expect(screen.getByText(/填写 → 提交专家审核/)).toBeTruthy(),
    );
    // 切到其他界面再回来：面板卸载后重新挂载，标记已在首次消费清标
    first.unmount();
    render(
      <ExpertWorkflowPanel
        selection={selection}
        projectName="bank"
        onSaveTeamPreset={() => undefined}
      />,
    );
    await waitFor(() => expect(screen.getByText(/继续填写/)).toBeTruthy());
    expect(screen.queryByText(/填写 → 提交专家审核/)).toBeNull();
    expect(screen.queryByText(/✕ 退出全屏/)).toBeNull();
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
    // 等专家卡稳定出现（compact 卡也保留交付标准）再点击，避免事件落在重建前的分离节点上
    await screen.findByText("交付标准");
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
    // 草稿入 store；直开标记被面板消费后清标（防止重挂时重复弹交付物区）
    await waitFor(() => {
      const st = useOpsCaseStore.getState();
      expect(st.drafts.some((d: { id: string }) => d.id === "DR-DIRECT")).toBe(true);
      expect(st.lastDirectDraftId).toBe("");
    });
    // 交付物区自动展开（不再自动全屏，2026-11-09）
    await waitFor(() =>
      expect(screen.getByText(/填写 → 提交专家审核/)).toBeTruthy(),
    );
    expect(screen.queryByText(/✕ 退出全屏/)).toBeNull();
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
    // 同上：等专家卡稳定后再点击，避免事件落在重建前的分离节点上
    await screen.findByText("交付标准");
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
