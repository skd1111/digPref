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
});
