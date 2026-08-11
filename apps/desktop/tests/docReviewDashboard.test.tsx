import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { DocTextViewer } from "@/components/doc-review/DocTextViewer";
import { DocReviewDashboard } from "@/views/DocReviewDashboard";
import { useDocReviewStore } from "@/store/docReviewStore";

const mockRegister = vi.fn();
const mockList = vi.fn();

vi.mock("@/ipc/invoke", () => ({
  ipc: {
    docReviewRegister: (...args: unknown[]) => mockRegister(...args),
    docReviewList: (...args: unknown[]) => mockList(...args),
  },
}));
vi.mock("@tauri-apps/api/webview", () => ({
  getCurrentWebview: () => {
    throw new Error("non-tauri");
  },
}));
vi.mock("@/components/doc-review/DocReviewList", () => ({ DocReviewList: () => <div /> }));
vi.mock("@/components/doc-review/FindingsPanel", () => ({ FindingsPanel: () => <div /> }));

describe("DocTextViewer", () => {
  it("renders pages and highlighted segments", () => {
    const finding = {
      finding_id: "f1",
      risk_type: "legal",
      risk_level: "high",
      title: "t",
      description: "",
      suggestion: "",
      rule_ref: null,
      evidence_text: "",
      positions: [{ page_no: 1, block_id: "p1b1", start: 0, end: 5 }],
    };
    useDocReviewStore.setState({
      detail: {
        doc_id: "d1",
        file_name: "a.txt",
        file_path: "C:/a.txt",
        format: "txt",
        page_count: 1,
        status: "done",
        overall_risk_level: "high",
        doc_category: "contract",
        risk_types: ["legal"],
        summary: null,
        created_at: "",
        pages: [
          {
            page_no: 1,
            blocks: [
              { block_id: "p1b1", text: "违约金过高", start: 0, end: 5 },
            ],
          },
        ],
        findings: [finding],
      },
      findings: [finding],
    } as never);
    render(<DocTextViewer />);
    expect(screen.getByText("第 1 页")).toBeTruthy();
    expect(screen.getByText("违约金过高")).toBeTruthy();
  });
});

describe("导入中进度动效", () => {
  it("register 期间 importingFile 置位，结束后清空", async () => {
    let resolveReg: (v: unknown) => void = () => {};
    mockRegister.mockReturnValueOnce(
      new Promise((r) => {
        resolveReg = r;
      }),
    );
    mockList.mockResolvedValue([]);
    useDocReviewStore.setState({ importingFile: null } as never);

    const pending = useDocReviewStore.getState().register("C:/合同.doc");
    expect(useDocReviewStore.getState().importingFile).toBe("合同.doc");

    resolveReg({ doc_id: "d1" });
    await pending;
    expect(useDocReviewStore.getState().importingFile).toBeNull();
  });

  it("register 失败时也清空 importingFile", async () => {
    mockRegister.mockRejectedValueOnce(new Error("转换超时"));
    const res = await useDocReviewStore.getState().register("C:/坏.doc");
    expect(res.ok).toBe(false);
    expect(useDocReviewStore.getState().importingFile).toBeNull();
  });

  it("导入中展示文件名与 spinner 遮罩", () => {
    useDocReviewStore.setState({ importingFile: "测试合同.doc" } as never);
    render(<DocReviewDashboard />);
    expect(screen.getByText(/正在导入「测试合同.doc」/)).toBeTruthy();
  });
});
