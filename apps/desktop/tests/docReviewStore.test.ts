import { beforeEach, describe, expect, it, vi } from "vitest";
import { useDocReviewStore } from "@/store/docReviewStore";
import { ipc } from "@/ipc/invoke";

vi.mock("@/ipc/invoke", () => ({
  ipc: {
    docReviewList: vi.fn(),
    docReviewRegister: vi.fn(),
    docReviewGet: vi.fn(),
    docReviewAnalyze: vi.fn(),
    docReviewStatus: vi.fn(),
    docReviewFindings: vi.fn(),
    docReviewDelete: vi.fn(),
  },
}));

beforeEach(() => {
  useDocReviewStore.setState({
    docs: [],
    selectedDocId: null,
    detail: null,
    findings: [],
    analyzing: false,
    loading: false,
    error: null,
  });
});

describe("docReviewStore", () => {
  it("loadDocs populates list", async () => {
    (ipc.docReviewList as ReturnType<typeof vi.fn>).mockResolvedValue([
      {
        doc_id: "d1",
        file_name: "a.txt",
        format: "txt",
        page_count: 1,
        status: "done",
        overall_risk_level: "high",
        created_at: "",
      },
    ]);
    await useDocReviewStore.getState().loadDocs();
    expect(useDocReviewStore.getState().docs).toHaveLength(1);
  });

  it("register failure surfaces error", async () => {
    (ipc.docReviewRegister as ReturnType<typeof vi.fn>).mockRejectedValue(
      new Error("bad format"),
    );
    const res = await useDocReviewStore.getState().register("C:/x.xls");
    expect(res.ok).toBe(false);
    expect(res.error).toBe("bad format");
  });

  it("open on a done doc loads persisted result without re-analyzing", async () => {
    // 已分析完成（done）的文档：切换过去只应读回持久化结果，
    // 绝不能重新触发分析（不调 docReviewAnalyze、不置 analyzing）。
    (ipc.docReviewGet as ReturnType<typeof vi.fn>).mockResolvedValue({
      doc_id: "d1",
      file_name: "a.txt",
      format: "txt",
      page_count: 1,
      status: "done",
      overall_risk_level: "high",
      created_at: "",
      file_path: "C:/a.txt",
      doc_category: null,
      risk_types: [],
      summary: null,
      pages: [],
      findings: [{ finding_id: "f1", risk_level: "high" }],
    });
    await useDocReviewStore.getState().open("d1");
    const s = useDocReviewStore.getState();
    expect(ipc.docReviewAnalyze).not.toHaveBeenCalled();
    expect(s.analyzing).toBe(false);
    expect(s.loading).toBe(false);
    expect(s.findings).toHaveLength(1);
  });
});
