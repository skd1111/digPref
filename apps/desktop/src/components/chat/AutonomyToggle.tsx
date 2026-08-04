/**
 * AutonomyToggle —— Phase 18 会话级自主性开关（双框架架构）。
 *
 * 放在 ChatInput 发送按钮旁（与 InferenceModeToggle 并排）：
 *   👤 交互 —— 每步等人审批（默认，安全）
 *   🤖 自动 —— 按智能体推荐选项自主继续执行
 *
 * 首次开启需经 AutoModeConfirmDialog 风险确认，确认后写 AUTO_MODE_ENABLED
 * 审计；状态只存 chatStore（不持久化），重启/新会话回落 interactive。
 */
import { useState } from 'react';
import { useChatStore } from '@/store/chatStore';
import { useUIStore } from '@/store/uiStore';
import { ipc } from '@/ipc/invoke';
import { AutoModeConfirmDialog } from '@/components/chat/AutoModeConfirmDialog';

export function AutonomyToggle(): JSX.Element {
  const autonomy = useChatStore((s) => s.autonomy);
  const setAutonomy = useChatStore((s) => s.setAutonomy);
  const activeTabId = useChatStore((s) => s.activeTabId);
  const workMode = useUIStore((s) => s.mode);
  const [confirming, setConfirming] = useState(false);

  const isAuto = autonomy === 'auto';

  const handleClick = (): void => {
    if (isAuto) {
      // 关闭：直接回落交互模式（无需确认）
      setAutonomy('interactive');
      return;
    }
    // 开启：每次都要过风险确认弹窗（不跨会话记忆授权）
    setConfirming(true);
  };

  const handleConfirm = (): void => {
    setConfirming(false);
    setAutonomy('auto');
    // 授权审计 best-effort：失败不阻塞开关（后端决策审计仍独立留痕）
    ipc.confirmAutonomy(activeTabId, workMode).catch((e) => {
      console.warn('AUTO_MODE_ENABLED audit failed:', e);
    });
  };

  return (
    <>
      <button
        type="button"
        onClick={handleClick}
        title={
          isAuto
            ? '自动模式：按推荐选项自主继续执行（点击关闭）'
            : '交互模式：每步等待审批（点击开启自动模式）'
        }
        className="flex items-center gap-1 rounded px-2 py-1 text-xs font-medium transition-colors"
        style={{
          backgroundColor: isAuto ? '#3a2a1a' : '#1f2937',
          color: isAuto ? '#e8ab5e' : '#9ca3af',
          border: `1px solid ${isAuto ? '#5a3a2a' : '#374151'}`,
        }}
      >
        <span>{isAuto ? '🤖' : '👤'}</span>
        <span className="hidden sm:inline">{isAuto ? '自动' : '交互'}</span>
      </button>
      {confirming && (
        <AutoModeConfirmDialog
          onConfirm={handleConfirm}
          onCancel={() => setConfirming(false)}
        />
      )}
    </>
  );
}
