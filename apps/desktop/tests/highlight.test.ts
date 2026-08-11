import { describe, expect, it } from "vitest";
import { splitBlockSegments } from "@/components/doc-review/highlight";
import type { DocBlock, DocFinding } from "@eaide/shared-protocol";

const block: DocBlock = {
  block_id: "p1b1",
  text: "甲方乙方违约金过高",
  start: 0,
  end: 9,
};

function finding(id: string, start: number, end: number): DocFinding {
  return {
    finding_id: id,
    risk_type: "legal",
    risk_level: "high",
    title: "t",
    description: "",
    suggestion: "",
    rule_ref: null,
    evidence_text: "",
    positions: [{ page_no: 1, block_id: "p1b1", start, end }],
    kb_refs: [],
  };
}

describe("splitBlockSegments", () => {
  it("splits a block into highlighted segments", () => {
    const segs = splitBlockSegments(block, [finding("f1", 4, 9)]);
    expect(segs).toEqual([
      { text: "甲方乙方", findingId: null },
      { text: "违约金过高", findingId: "f1" },
    ]);
  });

  it("returns single segment when no positions", () => {
    expect(splitBlockSegments(block, [])).toEqual([
      { text: "甲方乙方违约金过高", findingId: null },
    ]);
  });
});
