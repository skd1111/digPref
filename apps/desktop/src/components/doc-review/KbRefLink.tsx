/**
 * KbRefLink —— 知识库依据条目（点击打开命中的原文件）。
 *
 * 统一「点依据 → 看原文」的入口，文档审核（FindingsPanel）与运营模式合规审核
 * 报告（RiskAuditReportModal）共用：
 *   - 带 file_path：来源渲染为可点击按钮，点击走 previewLocalFile
 *     （md/txt/html 内置预览，pdf 内嵌，doc 等交系统默认程序）；
 *   - 无 file_path（知识文件已被清理/不可解析）：降级为纯文本，不触发预览。
 */
import { previewLocalFile } from "@/store/officePreviewStore";
import { BookIcon } from "@/components/ui/Icons";

/** 点击依据 → 预览命中的知识原文件（失败时提示路径，便于排查）。 */
export async function openKbRefFile(path: string): Promise<void> {
  try {
    await previewLocalFile(path);
  } catch (e) {
    window.alert(`打开依据原文件失败：${path}\n${String(e)}`);
  }
}

/** 依据来源：有原文件时可点击打开，无原文件时纯文本。 */
export function KbRefLink({
  source,
  filePath,
}: {
  source: string;
  filePath?: string;
}): JSX.Element {
  if (!filePath) {
    return (
      <span className="flex items-center gap-1">
        <BookIcon size={12} />
        {source}
      </span>
    );
  }
  return (
    <button
      type="button"
      className="cursor-pointer rounded underline decoration-dotted underline-offset-2 hover:text-[#0b6bcb]"
      style={{ color: "#1f1f1f" }}
      title="点击预览依据原文件"
      onClick={(e) => {
        e.stopPropagation();
        void openKbRefFile(filePath);
      }}
    >
      <span className="flex items-center gap-1">
        <BookIcon size={12} />
        {source}
      </span>
    </button>
  );
}
