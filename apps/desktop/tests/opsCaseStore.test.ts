/**
 * opsCaseStore.test.ts —— 专家验收工作流 Case 状态测试（2026-08-10）。
 *
 * 覆盖：loadCase / attachFiles（自动触发 AI 审核）/ reviewFile /
 * overrideFile / deleteFile / askExpert / exportCase。
 */
import { beforeEach, describe, expect, it, vi } from "vitest";
import { useOpsCaseStore } from "@/store/opsCaseStore";
import { ipc } from "@/ipc/invoke";

vi.mock("@/ipc/invoke", () => ({
  ipc: {
    opsCaseGet: vi.fn(),
    opsCaseFileAdd: vi.fn(),
    opsCaseFileReview: vi.fn(),
    opsCaseFileOverride: vi.fn(),
    opsCaseFileDelete: vi.fn(),
    opsCaseAsk: vi.fn(),
    opsCaseExport: vi.fn(),
  },
}));

const CASE_ID = "bank__ops_open";

function resetStore(): void {
  useOpsCaseStore.setState({
    case_id: "",
    files: [],
    qa: [],
    loading: false,
    error: null,
    busyMembers: {},
    exporting: false,
  });
}

beforeEach(() => {
  resetStore();
  vi.clearAllMocks();
});

describe("opsCaseStore", () => {
  it("loadCase 拉取 Case 数据", async () => {
    (ipc.opsCaseGet as ReturnType<typeof vi.fn>).mockResolvedValue({
      case_id: CASE_ID,
      files: [{ id: "CF-1", case_id: CASE_ID, member_key: "身份专家", status: "passed" }],
      qa: [{ id: "QA-1", case_id: CASE_ID, member_key: "身份专家", question: "q", answer: "a" }],
    });
    await useOpsCaseStore.getState().loadCase("bank", "ops_open");
    const s = useOpsCaseStore.getState();
    expect(s.case_id).toBe(CASE_ID);
    expect(s.files).toHaveLength(1);
    expect(s.qa).toHaveLength(1);
  });

  it("loadCase 无 featureId 时重置", async () => {
    useOpsCaseStore.setState({ case_id: CASE_ID, files: [{ id: "x" } as never] });
    await useOpsCaseStore.getState().loadCase("bank", "");
    expect(useOpsCaseStore.getState().case_id).toBe("");
    expect(useOpsCaseStore.getState().files).toEqual([]);
  });

  it("loadCase 瞬态失败（Agent 未就绪）静默退避重试直到成功（BUGFIX #77）", async () => {
    vi.useFakeTimers();
    try {
      const get = ipc.opsCaseGet as ReturnType<typeof vi.fn>;
      get
        .mockRejectedValueOnce(
          new Error(
            "ops command failed: error sending request for url (http://127.0.0.1:8765/ops/case)",
          ),
        )
        .mockResolvedValueOnce({ case_id: CASE_ID, files: [], qa: [] });
      const p = useOpsCaseStore.getState().loadCase("bank", "ops_open");
      // 第一次失败后退避 1s 重试；重试期间不向用户报错
      await vi.advanceTimersByTimeAsync(1500);
      await p;
      expect(get).toHaveBeenCalledTimes(2);
      expect(useOpsCaseStore.getState().error).toBeNull();
      expect(useOpsCaseStore.getState().case_id).toBe(CASE_ID);
    } finally {
      vi.useRealTimers();
    }
  });

  it("loadCase 非瞬态错误立即展示且不重试", async () => {
    const get = ipc.opsCaseGet as ReturnType<typeof vi.fn>;
    get.mockRejectedValue(new Error("case not found"));
    await useOpsCaseStore.getState().loadCase("bank", "ops_open");
    expect(get).toHaveBeenCalledTimes(1);
    expect(useOpsCaseStore.getState().error).toContain("case not found");
  });

  it("attachFiles 上传后自动触发 AI 审核", async () => {
    (ipc.opsCaseFileReview as ReturnType<typeof vi.fn>).mockResolvedValue({});
    useOpsCaseStore.setState({ case_id: CASE_ID });
    (ipc.opsCaseFileAdd as ReturnType<typeof vi.fn>).mockResolvedValue({
      id: "CF-9",
      case_id: CASE_ID,
      team_id: "t",
      member_key: "身份专家",
      file_name: "营业执照.txt",
      file_path: "/tmp/x",
      status: "pending",
      review_note: "",
      reviewed_by: "",
      created_at: 1,
      updated_at: 1,
    });
    const file = new File(["统一社会信用代码"], "营业执照.txt", { type: "text/plain" });
    await useOpsCaseStore.getState().attachFiles("t", "身份专家", [file]);

    expect(ipc.opsCaseFileAdd).toHaveBeenCalledTimes(1);
    const body = (ipc.opsCaseFileAdd as ReturnType<typeof vi.fn>).mock.calls[0][0];
    expect(body.file_name).toBe("营业执照.txt");
    expect(body.content_base64.length).toBeGreaterThan(0);
    // 上传后自动调审核
    expect(ipc.opsCaseFileReview).toHaveBeenCalledWith("CF-9");
    // busy 解除
    expect(useOpsCaseStore.getState().busyMembers["身份专家"]).toBeUndefined();
  });

  it("reviewFile 更新文件状态并清除 busy", async () => {
    useOpsCaseStore.setState({
      case_id: CASE_ID,
      files: [
        {
          id: "CF-1",
          case_id: CASE_ID,
          team_id: "t",
          member_key: "身份专家",
          file_name: "a.txt",
          file_path: "/tmp/a",
          status: "pending",
          review_note: "",
          reviewed_by: "",
          created_at: 1,
          updated_at: 1,
        },
      ],
    });
    (ipc.opsCaseFileReview as ReturnType<typeof vi.fn>).mockResolvedValue({
      id: "CF-1",
      team_id: "t",
      status: "passed",
      review_note: "通过",
      reviewed_by: "ai",
    });
    await useOpsCaseStore.getState().reviewFile("CF-1");
    const f = useOpsCaseStore.getState().files[0];
    expect(f.status).toBe("passed");
    expect(f.reviewed_by).toBe("ai");
    // 出审核结果 → 对应专家团页签未读 +1
    expect(useOpsCaseStore.getState().unreadByTeam["t"]).toBe(1);
  });

  it("markTeamRead 清除页签未读；loadCase 重置未读", async () => {
    useOpsCaseStore.setState({ unreadByTeam: { t: 2 } });
    useOpsCaseStore.getState().markTeamRead("t");
    expect(useOpsCaseStore.getState().unreadByTeam["t"]).toBeUndefined();

    useOpsCaseStore.setState({ unreadByTeam: { t: 3 } });
    (ipc.opsCaseGet as ReturnType<typeof vi.fn>).mockResolvedValue({
      case_id: CASE_ID,
      files: [],
      qa: [],
    });
    await useOpsCaseStore.getState().loadCase("bank", "ops_open");
    expect(useOpsCaseStore.getState().unreadByTeam).toEqual({});
  });

  it("overrideFile 人工改判", async () => {
    useOpsCaseStore.setState({
      case_id: CASE_ID,
      files: [
        {
          id: "CF-2",
          case_id: CASE_ID,
          team_id: "t",
          member_key: "身份专家",
          file_name: "b.txt",
          file_path: "/tmp/b",
          status: "rejected",
          review_note: "",
          reviewed_by: "ai",
          created_at: 1,
          updated_at: 1,
        },
      ],
    });
    (ipc.opsCaseFileOverride as ReturnType<typeof vi.fn>).mockResolvedValue({
      id: "CF-2",
      status: "passed",
      reviewed_by: "human",
    });
    await useOpsCaseStore.getState().overrideFile("CF-2", "passed", "人工确认");
    expect(useOpsCaseStore.getState().files[0].status).toBe("passed");
  });

  it("deleteFile 从列表移除", async () => {
    useOpsCaseStore.setState({
      case_id: CASE_ID,
      files: [
        {
          id: "CF-3",
          case_id: CASE_ID,
          team_id: "t",
          member_key: "身份专家",
          file_name: "c.txt",
          file_path: "/tmp/c",
          status: "pending",
          review_note: "",
          reviewed_by: "",
          created_at: 1,
          updated_at: 1,
        },
      ],
    });
    (ipc.opsCaseFileDelete as ReturnType<typeof vi.fn>).mockResolvedValue({ ok: true });
    await useOpsCaseStore.getState().deleteFile("CF-3");
    expect(useOpsCaseStore.getState().files).toEqual([]);
  });

  it("askExpert 追加问答并管理 busy", async () => {
    useOpsCaseStore.setState({ case_id: CASE_ID });
    (ipc.opsCaseAsk as ReturnType<typeof vi.fn>).mockResolvedValue({
      qa: {
        id: "QA-2",
        case_id: CASE_ID,
        member_key: "身份专家",
        question: "执照要注意什么？",
        answer: "有效期",
        created_at: 1,
      },
    });
    await useOpsCaseStore.getState().askExpert("t", "身份专家", "执照要注意什么？");
    const s = useOpsCaseStore.getState();
    expect(s.qa).toHaveLength(1);
    expect(s.qa[0].answer).toBe("有效期");
    expect(s.busyMembers["身份专家"]).toBeUndefined();
  });

  it("askExpert 空问题不请求", async () => {
    useOpsCaseStore.setState({ case_id: CASE_ID });
    await useOpsCaseStore.getState().askExpert("t", "身份专家", "   ");
    expect(ipc.opsCaseAsk).not.toHaveBeenCalled();
  });

  it("exportCase 成功/失败分别返回 true/false", async () => {
    useOpsCaseStore.setState({ case_id: CASE_ID });
    (ipc.opsCaseExport as ReturnType<typeof vi.fn>).mockResolvedValue({
      ok: true,
      path: "C:/out/a.zip",
      file_count: 1,
    });
    const ok = await useOpsCaseStore
      .getState()
      .exportCase("C:/out/a.zip", { featureName: "开户", checklist: ["x"] });
    expect(ok).toBe(true);
    expect(useOpsCaseStore.getState().exporting).toBe(false);

    (ipc.opsCaseExport as ReturnType<typeof vi.fn>).mockRejectedValue(new Error("boom"));
    const fail = await useOpsCaseStore.getState().exportCase("C:/out/b.zip", {});
    expect(fail).toBe(false);
    expect(useOpsCaseStore.getState().error).toContain("boom");
  });
});
