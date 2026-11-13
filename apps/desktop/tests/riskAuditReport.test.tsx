/**
 * riskAuditReport.test.tsx —— 运营模式「系统合规审核报告」弹窗回归。
 *
 * 覆盖用户反馈的两个问题：
 *   - 「依据（知识库）」条目不可点击 → 点依据打不开原文；
 *   - 「原文证据」无法回看被审核材料原文。
 * 现在：依据文件名可点击打开知识库原文件，原文证据旁提供材料原文入口。
 */
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { describe, expect, it, vi, beforeEach } from "vitest";

const previewLocalFile = vi.fn();

vi.mock("@/store/officePreviewStore", () => ({
  previewLocalFile: (...a: unknown[]) => previewLocalFile(...a),
}));

import { RiskAuditReportModal } from "@/components/ops/RiskAuditReportModal";
import type { OpsCaseFile } from "@/types/ops";

const KB_REF = {
  source: "《私募投资基金监督管理条例》",
  heading: "第二章 私募基金管理人",
  excerpt: "…基金管理人应当…受益所有人…",
  matched_terms: ["受益所有人"],
  file_path: "C:/kb/02-私募基金条例.md",
};

const FILE = {
  id: "CF-1",
  case_id: "bank__ops_open",
  team_id: "corp_account_team",
  member_key: "账户业务主管",
  file_name: "企业法人营业执照.png",
  file_path: "C:/cases/企业法人营业执照.png",
  status: "rejected",
  review_note: "补充受益所有人材料",
  reviewed_by: "ai",
  risk_report: {
    audited: true,
    degraded: false,
    error: "",
    basis: "vision",
    doc_category: "other",
    overall_risk_level: "medium",
    findings: [
      {
        finding_id: "F-1",
        risk_type: "compliance",
        risk_level: "medium",
        title: "受益所有人核验未完成",
        description: "材料仅载明自然人独资，未识别受益所有人",
        suggestion: "补充受益所有人身份、持股或控制关系",
        rule_ref: null,
        evidence_text: "仅载明自然人独资的 company 类型和法定代表人刘海波",
        positions: [],
        kb_refs: [KB_REF],
      },
    ],
    elapsed_ms: 1200,
    audited_at: 1,
  },
  created_at: 1,
  updated_at: 1,
} as unknown as OpsCaseFile;

describe("合规审核报告：点依据打开原文", () => {
  beforeEach(() => {
    previewLocalFile.mockClear();
    previewLocalFile.mockResolvedValue(undefined);
  });

  it("依据条目可点击 → 打开知识库原文件", async () => {
    render(<RiskAuditReportModal file={FILE} onClose={() => undefined} />);

    const src = await screen.findByText(/私募投资基金监督管理条例/);
    fireEvent.click(src);

    await waitFor(() =>
      expect(previewLocalFile).toHaveBeenCalledWith("C:/kb/02-私募基金条例.md"),
    );
  });

  it("原文证据旁可点 → 打开被审核材料原文", async () => {
    render(<RiskAuditReportModal file={FILE} onClose={() => undefined} />);

    fireEvent.click(await screen.findByText(/点此看材料原文/));

    await waitFor(() =>
      expect(previewLocalFile).toHaveBeenCalledWith("C:/cases/企业法人营业执照.png"),
    );
  });

  it("依据无 file_path → 纯文本，不触发预览", async () => {
    const noPath = {
      ...FILE,
      risk_report: {
        ...FILE.risk_report!,
        findings: [
          {
            ...FILE.risk_report!.findings[0],
            kb_refs: [{ ...KB_REF, file_path: "" }],
          },
        ],
      },
    } as unknown as OpsCaseFile;

    render(<RiskAuditReportModal file={noPath} onClose={() => undefined} />);
    await screen.findByText(/私募投资基金监督管理条例/);
    expect(previewLocalFile).not.toHaveBeenCalled();
  });
});
