/**
 * FindingsPanel —— 风险点「知识库依据」可点击预览原文件回归。
 *
 * 覆盖：
 *   - kb_ref 带 file_path → 来源渲染为可点击按钮，点击调 previewLocalFile（且 stopPropagation 不打开详情弹窗）
 *   - kb_ref 无 file_path → 纯文本，不触发预览
 */
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { describe, expect, it, vi, beforeEach } from "vitest";

const previewLocalFile = vi.fn();

vi.mock("@/store/officePreviewStore", () => ({
  previewLocalFile: (...a: unknown[]) => previewLocalFile(...a),
}));
vi.mock("@tauri-apps/plugin-dialog", () => ({ save: vi.fn() }));
vi.mock("@/ipc/invoke", () => ({ ipc: { docReviewExportWord: vi.fn() } }));

import { FindingsPanel } from "@/components/doc-review/FindingsPanel";
import { useDocReviewStore } from "@/store/docReviewStore";

const KB_REF = {
  source: "《合同行政监督管理办法》",
  heading: "条款红线清单",
  excerpt: "…最终解释权…",
  matched_terms: ["解释权"],
  file_path: "C:/kb/01-合规风险.md",
};

const FINDING = {
  finding_id: "f1",
  risk_type: "compliance",
  risk_level: "high",
  title: "单方最终解释权条款",
  description: "合同约定本公司享有最终解释权",
  suggestion: "删除该条款",
  rule_ref: null,
  evidence_text: "本公司对本协议享有最终解释权",
  positions: [],
  kb_refs: [KB_REF],
};

describe("FindingsPanel 知识库依据可点击预览", () => {
  beforeEach(() => {
    previewLocalFile.mockClear();
    previewLocalFile.mockResolvedValue(undefined);
    useDocReviewStore.setState({
      findings: [FINDING],
      detail: null,
      selectedDocId: "d1",
    } as never);
  });

  it("带 file_path → 点击来源预览原文件", async () => {
    render(<FindingsPanel />);
    const src = await screen.findByText(/合同行政监督管理办法/);
    fireEvent.click(src);
    await waitFor(() =>
      expect(previewLocalFile).toHaveBeenCalledWith("C:/kb/01-合规风险.md"),
    );
    // stopPropagation：不应打开详情弹窗（弹窗含「定位原文高亮」按钮）
    expect(screen.queryByText(/定位原文高亮/)).toBeNull();
  });

  it("无 file_path → 纯文本，不触发预览", async () => {
    useDocReviewStore.setState({
      findings: [{ ...FINDING, kb_refs: [{ ...KB_REF, file_path: "" }] }],
      detail: null,
      selectedDocId: "d1",
    } as never);
    render(<FindingsPanel />);
    await screen.findByText(/合同行政监督管理办法/);
    expect(previewLocalFile).not.toHaveBeenCalled();
  });
});
