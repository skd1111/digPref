/**
 * ChatInput —— textarea + 发送按钮。
 *
 * 提交时：
 *   1. 将用户消息写入 chatStore
 *   2. 调用 Rust `agent_chat` 命令，启动 SSE 流
 *   3. 事件通过 useAgentStream hook 自动路由到 store 和终端
 *
 * Phase 12 V1 新增：
 *   - 显示 Monaco 选区 chip（用户在编辑器右键「📋 附加选区到对话」时设置）
 *   - 发送时：选区作为独立 system 消息写入 chatStore，让 agent 知道用户关注点
 *
 * 借鉴 VSCode 内联聊天交互：
 *   - Enter 发送，Shift+Enter 换行（与 VSCode Copilot Chat 一致）
 *   - 发送中禁用输入，显示 loading 状态
 *   - 错误时显示内联提示
 */
import { useState, useCallback, type KeyboardEvent } from 'react';
import { invoke } from '@/ipc/invoke';
import { useChatStore } from '@/store/chatStore';
import { useUIStore } from '@/store/uiStore';
import { useCodeNavStore } from '@/store/codeNavStore';
import { InferenceModeToggle } from '@/components/chat/InferenceModeToggle';  // Phase 4 V0
import { AutonomyToggle } from '@/components/chat/AutonomyToggle';  // Phase 18

export function ChatInput(): JSX.Element {
  const [text, setText] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const appendChat = useChatStore((s) => s.append);
  const setBusyStore = useChatStore((s) => s.setBusy);
  const autonomy = useChatStore((s) => s.autonomy);  // Phase 18
  const workMode = useUIStore((s) => s.mode);  // Phase 18：前端模式透传
  const chatSelection = useCodeNavStore((s) => s.chatSelection);
  const clearChatSelection = useCodeNavStore((s) => s.clearChatSelection);

  const submit = useCallback(async (): Promise<void> => {
    const trimmed = text.trim();
    if (!trimmed || busy) return;

    // Phase 12 V1：如果附着了选区，先把它作为一条 system 消息写入 chatStore，
    // 让 agent 在 SSE 流中知道「用户关注以下代码」。
    if (chatSelection) {
      appendChat({
        id: `sel-${Date.now()}`,
        role: 'system',
        kind: 'execution',
        category: 'log',
        content: `[用户关注以下代码 · ${chatSelection.label} · 来自 ${shortFile(chatSelection.file)}]\n\`\`\`\n${chatSelection.text}\n\`\`\``,
        status: 'ok',
      });
    }

    // 添加用户消息到聊天
    appendChat({
      id: `user-${Date.now()}`,
      role: 'user',
      content: trimmed,
    });

    setBusy(true);
    setBusyStore(true);
    setError(null);
    try {
      // 发送时把「附加选区」也告诉 agent —— 后端 system prompt 会改写
      // Phase 18：workMode/autonomy 随请求透传（ModeRouter 先验 + HITL 决策矩阵）
      await invoke('agent_chat', {
        prompt: trimmed,
        workMode,
        autonomy,
        selection: chatSelection
          ? {
              file: chatSelection.file,
              start_line: chatSelection.startLine,
              end_line: chatSelection.endLine,
              text: chatSelection.text,
            }
          : null,
      });
    } catch (e) {
      setError(String(e));
      setBusyStore(false);
    } finally {
      setText('');
      setBusy(false);
      // 发送后清掉选区（一次性附加）
      clearChatSelection();
    }
  }, [text, busy, chatSelection, appendChat, setBusyStore, clearChatSelection, workMode, autonomy]);

  const handleKeyDown = useCallback(
    (e: KeyboardEvent<HTMLTextAreaElement>) => {
      // Enter 发送，Shift+Enter 换行（与 VSCode Copilot Chat 一致）
      if (e.key === 'Enter' && !e.shiftKey) {
        e.preventDefault();
        void submit();
      }
    },
    [submit],
  );

  return (
    <div className="flex flex-col gap-1">
      {error && (
        <div className="rounded border border-accent-danger bg-bg-code px-3 py-1 text-xs text-accent-danger">
          {error}
        </div>
      )}

      {/* Phase 12 V1：附加选区 chip —— 让用户看到「我附加了哪段代码」
          自动同步（auto=true，编辑器选区变化触发）：灰色边框，提示「会自动更新」
          手动附加（auto=false，右键菜单触发）：绿色边框，提示「手动选择保留」 */}
      {chatSelection && (
        <div
          className="flex items-center gap-2 rounded px-2 py-1 text-2xs"
          style={{
            backgroundColor: '#f3f3f3',
            border: `1px solid ${chatSelection.auto ? '#616161' : '#059669'}`,
            color: '#1f1f1f',
          }}
        >
          <span style={{ color: chatSelection.auto ? '#616161' : '#059669' }}>
            {chatSelection.auto ? '📋' : '📌'}
          </span>
          <span className="font-mono" style={{ color: chatSelection.auto ? '#4f46e5' : '#059669' }}>
            {chatSelection.auto ? '已自动附加选中' : '已手动附加选中'}
          </span>
          <span className="font-mono" style={{ color: '#0b6bcb' }}>
            {chatSelection.label}
          </span>
          <span className="truncate" style={{ color: '#616161' }}>
            · {shortFile(chatSelection.file)}
          </span>
          <button
            type="button"
            onClick={() => clearChatSelection()}
            title="移除选区"
            className="ml-auto rounded px-1.5 hover:bg-[#ececec]"
            style={{ color: '#616161' }}
          >
            ✕
          </button>
        </div>
      )}

      <div className="flex gap-2">
        <textarea
          value={text}
          onChange={(e) => setText(e.target.value)}
          onKeyDown={handleKeyDown}
          disabled={busy}
          placeholder={
            busy
              ? 'Agent 正在处理…'
              : chatSelection
                ? `告诉我你想做什么（已附加 ${chatSelection.label}）…`
                : '告诉我你想做什么… (Enter 发送, Shift+Enter 换行)'
          }
          className="flex-1 resize-none rounded border border-border bg-bg-subtle p-2 text-sm focus:border-accent focus:outline-none disabled:opacity-50"
          rows={2}
        />
        <InferenceModeToggle />
        <AutonomyToggle />
        <button
          onClick={() => void submit()}
          disabled={busy || !text.trim()}
          className="rounded bg-accent px-4 text-sm font-semibold text-white hover:opacity-90 disabled:cursor-not-allowed disabled:opacity-50"
        >
          {busy ? '…' : '发送'}
        </button>
      </div>
    </div>
  );
}

/** 把绝对路径截短为「相对项目根」或文件名，方便 chip 显示 */
function shortFile(path: string): string {
  if (!path) return '?';
  // Windows: 取最后两段（D:/code/myproject/src/foo.ts → myproject/src/foo.ts）
  const parts = path.split(/[/\\]/).filter(Boolean);
  if (parts.length <= 2) return path;
  return '…/' + parts.slice(-2).join('/');
}