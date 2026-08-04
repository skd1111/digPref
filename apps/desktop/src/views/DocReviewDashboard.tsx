import { DocReviewList } from "@/components/doc-review/DocReviewList";
import { DocTextViewer } from "@/components/doc-review/DocTextViewer";
import { FindingsPanel } from "@/components/doc-review/FindingsPanel";

export function DocReviewDashboard(): JSX.Element {
  return (
    <div
      className="doc-review-dashboard flex h-full"
      style={{ backgroundColor: "#ffffff" }}
    >
      <div
        className="flex-shrink-0 border-r"
        style={{ width: 280, borderColor: "#d4d4d4" }}
      >
        <DocReviewList />
      </div>
      <div className="flex-1 overflow-hidden">
        <DocTextViewer />
      </div>
      <div
        className="flex-shrink-0 border-l"
        style={{ width: 320, borderColor: "#d4d4d4" }}
      >
        <FindingsPanel />
      </div>
    </div>
  );
}
