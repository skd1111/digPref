import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { DocTextViewer } from "@/components/doc-review/DocTextViewer";
import { useDocReviewStore } from "@/store/docReviewStore";

vi.mock("@/ipc/invoke", () => ({ ipc: {} }));

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
