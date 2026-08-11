/**
 * ContextChip —— Phase 2G 业务功能点上下文 chip。
 *
 * 挂载位置：CenterChatFlow ChatInput 上方（spec §4.4）。
 * 形态：VSCode macOS 风格顶部 chip。
 *   - 背景 #0e639c20，border #0e639c（项目已有 accent 色）
 *   - 左侧 🧩 + "当前上下文：XXX"
 *   - 右侧 [×] 关闭
 *   - selectedFeatureContext === null → 不渲染
 */
import { useChatStore } from '@/store/chatStore';
import { useBiznavStore } from '@/store/biznavStore';

export function ContextChip(): JSX.Element | null {
  const ctx = useChatStore((s) => s.selectedFeatureContext);
  const opsCtx = useChatStore((s) => s.opsNavContext);
  const setFeatureContext = useChatStore((s) => s.setFeatureContext);
  const setOpsNavContext = useChatStore((s) => s.setOpsNavContext);
  const closeDrawer = useBiznavStore((s) => s.closeDrawer);

  const active = opsCtx ?? ctx;
  if (!active) return null;

  const handleClose = (): void => {
    if (opsCtx) {
      setOpsNavContext(null);
    } else {
      setFeatureContext(null);
      closeDrawer();
    }
  };

  return (
    <div
      className="mb-2 flex items-center gap-2 rounded px-2 py-1 text-2xs"
      style={{
        backgroundColor: '#0e639c20',
        border: '1px solid #0e639c',
        color: '#0b6bcb',
      }}
      title={active.feature_description.slice(0, 80)}
    >
      <span>🧩</span>
      <span className="flex-1 truncate">
        当前上下文：<strong style={{ color: '#1f1f1f' }}>{active.feature_name}</strong>
      </span>
      <button
        type="button"
        onClick={handleClose}
        className="flex-shrink-0 rounded px-1 transition-colors hover:bg-vscode-border"
        style={{ color: '#616161' }}
        title="关闭上下文"
        aria-label="关闭上下文"
      >
        ✕
      </button>
    </div>
  );
}
