import type { DocBlock, DocFinding } from "@eaide/shared-protocol";

export interface Segment {
  text: string;
  findingId: string | null;
}

export function splitBlockSegments(
  block: DocBlock,
  findings: DocFinding[],
): Segment[] {
  const marks = findings
    .flatMap((f) => f.positions.map((p) => ({ ...p, findingId: f.finding_id })))
    .filter((p) => p.block_id === block.block_id)
    .sort((a, b) => a.start - b.start);
  if (marks.length === 0) return [{ text: block.text, findingId: null }];

  const segs: Segment[] = [];
  let cur = block.start;
  for (const m of marks) {
    const s = Math.max(m.start, cur);
    const e = Math.min(m.end, block.end);
    if (s > cur) {
      segs.push({
        text: block.text.slice(cur - block.start, s - block.start),
        findingId: null,
      });
    }
    if (e > s) {
      segs.push({
        text: block.text.slice(s - block.start, e - block.start),
        findingId: m.findingId,
      });
    }
    cur = Math.max(cur, e);
  }
  if (cur < block.end) {
    segs.push({ text: block.text.slice(cur - block.start), findingId: null });
  }
  return segs;
}
