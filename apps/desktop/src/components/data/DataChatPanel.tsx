/**
 * DataChatPanel —— 数据专家横向对话栏（BUGFIX #105：从 QueryEditor tab 拆出）。
 *
 * 与编辑器/数据网格/图表并行展示：自然语言提问 → NL2SQL 生成 SQL 回填编辑器。
 * 支持 zoomed 放大视图（全屏时字号加大），头部 ⛶ 按钮触发。
 */
import { useDataStore } from '@/store/dataStore';
import { PanelHeader } from './DataGrid';

export function DataChatPanel({
  zoomed = false,
  onZoom,
}: {
  zoomed?: boolean;
  onZoom?: () => void;
}): JSX.Element {
  const chat = useDataStore((s) => s.chat);
  const draft = useDataStore((s) => s.chatDraft);
  const setDraft = useDataStore((s) => s.setChatDraft);
  const send = useDataStore((s) => s.sendChat);
  const running = useDataStore((s) => s.running);

  const bubbleText = zoomed ? 'text-base' : 'text-ui';
  const roleText = 'text-2xs';

  return (
    <div className="flex h-full flex-col overflow-hidden" style={{ backgroundColor: '#ffffff' }}>
      <PanelHeader
        title="💬 AI 对话"
        right={
          onZoom ? (
            <button
              type="button"
              onClick={onZoom}
              className="rounded px-1.5 text-2xs transition-all hover:brightness-95"
              style={{ backgroundColor: '#ececec', color: '#333333' }}
              title={zoomed ? '退出放大' : '放大查看'}
            >
              ⛶
            </button>
          ) : undefined
        }
      />
      <div className={`flex-1 overflow-auto py-3 ${zoomed ? 'space-y-4 px-6' : 'space-y-3 px-3'}`}>
        {chat.length === 0 && (
          <div className={`px-1 ${zoomed ? 'text-ui' : 'text-2xs'}`} style={{ color: '#616161' }}>
            用自然语言提问，例如：「对比上月各分行坏账率」。
            生成的 SQL 会回填到左侧编辑器，复核后执行。
          </div>
        )}
        {chat.map((m, i) => (
          <div key={i} className={m.role === 'user' ? 'flex justify-end' : 'flex justify-start'}>
            <div
              className={`rounded-lg px-3 py-2 ${bubbleText} ${zoomed ? 'max-w-[70%]' : 'max-w-[85%]'}`}
              style={{
                backgroundColor: m.role === 'user' ? '#0e639c' : '#ececec',
                color: m.role === 'user' ? '#ffffff' : '#1f1f1f',
                lineHeight: 1.55,
                whiteSpace: 'pre-wrap',
              }}
            >
              <div className={`mb-0.5 ${roleText}`} style={{ color: m.role === 'user' ? '#cfe6ff' : '#059669' }}>
                {m.role === 'user' ? '👤 我' : '🤖 数据助手'}
              </div>
              {m.content}
            </div>
          </div>
        ))}
      </div>
      <div className="flex flex-shrink-0 items-end gap-2 border-t p-2" style={{ borderColor: '#d0d0d0' }}>
        <textarea
          value={draft}
          onChange={(e) => setDraft(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === 'Enter' && !e.shiftKey) {
              e.preventDefault();
              void send();
            }
          }}
          rows={zoomed ? 3 : 2}
          placeholder="用自然语言提问…（Enter 发送）"
          className={`flex-1 resize-none rounded px-3 py-2 outline-none ${zoomed ? 'text-base' : 'text-ui'}`}
          style={{ backgroundColor: '#ececec', color: '#1f1f1f' }}
        />
        <button
          type="button"
          onClick={() => void send()}
          disabled={running || !draft.trim()}
          className="rounded px-4 py-2 text-ui font-semibold transition-all hover:brightness-110"
          style={{
            backgroundColor: running || !draft.trim() ? '#ececec' : '#059669',
            color: running || !draft.trim() ? '#616161' : '#ffffff',
            cursor: running || !draft.trim() ? 'not-allowed' : 'pointer',
          }}
        >
          {running ? '生成中…' : '发送'}
        </button>
      </div>
    </div>
  );
}
