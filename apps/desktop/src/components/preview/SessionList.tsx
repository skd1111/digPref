/**
 * SessionList —— 多会话切换器（顶部下拉 + 状态徽标）。
 */
import type { PreviewSession } from "@/store/previewStore";

export function SessionList({
  sessions,
  activeId,
  onSelect,
  onNew,
}: {
  sessions: PreviewSession[];
  activeId: string | null;
  onSelect: (id: string) => void;
  onNew: () => void;
}) {
  return (
    <div className="flex items-center gap-2">
      <select
        aria-label="预览会话"
        value={activeId ?? ""}
        onChange={(e) => e.target.value && onSelect(e.target.value)}
        className="max-w-[220px] rounded border border-neutral-700 bg-neutral-800 px-2 py-1 text-xs text-neutral-200 outline-none focus:border-sky-500"
      >
        {sessions.length === 0 && <option value="">暂无预览会话</option>}
        {sessions.map((s) => (
          <option key={s.id} value={s.id}>
            {s.framework} · {s.port} · {s.status}
          </option>
        ))}
      </select>
      <button
        type="button"
        onClick={onNew}
        title="新建预览"
        className="rounded border border-neutral-700 bg-neutral-800 px-2 py-1 text-xs text-neutral-200 hover:border-sky-500 hover:text-sky-300"
      >
        ＋
      </button>
    </div>
  );
}
