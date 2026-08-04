/**
 * CheatSheet — 按 F1 弹出的快捷键 cheat sheet。
 *
 * 简单模态：半透明背景 + 居中卡片 + 关闭。
 */
import { useUIStore } from '@/store/uiStore';

const SHORTCUTS: { keys: string; desc: string; group: string }[] = [
  { group: '通用', keys: 'Ctrl+Shift+P', desc: '打开命令面板' },
  { group: '通用', keys: 'Ctrl+P', desc: '快速打开' },
  { group: '通用', keys: 'F1', desc: '显示本快捷键表' },
  { group: '通用', keys: 'Esc', desc: '关闭弹窗 / 取消' },
  { group: '对话', keys: 'Enter', desc: '在输入框发送' },
  { group: '对话', keys: 'Shift+Enter', desc: '在输入框换行' },
  { group: '对话', keys: 'Ctrl+T', desc: '新建会话' },
  { group: '对话', keys: 'Ctrl+W', desc: '关闭当前 tab' },
  { group: '窗口', keys: 'Ctrl+B', desc: '切换 Activity Bar / 侧边栏' },
  { group: '窗口', keys: 'Ctrl+J', desc: '切换底部终端' },
  { group: '窗口', keys: 'Ctrl+`', desc: '新建终端' },
  { group: '开发', keys: 'F12', desc: '切换开发者工具（编辑器内为跳转定义）' },
  { group: '开发', keys: 'Ctrl+Shift+I', desc: '切换开发者工具' },
  { group: '设置', keys: 'Ctrl+,', desc: '打开 Settings' },
];

export function CheatSheet(): JSX.Element | null {
  const open = useUIStore((s) => s.cheatSheetOpen);
  const toggle = useUIStore((s) => s.toggleCheatSheet);
  if (!open) return null;

  // 按 group 分组
  const groups: Record<string, typeof SHORTCUTS> = {};
  for (const s of SHORTCUTS) {
    (groups[s.group] ||= []).push(s);
  }

  return (
    <div
      className="fixed inset-0 z-[200] flex items-center justify-center"
      style={{ backgroundColor: 'rgba(0,0,0,0.5)' }}
      onClick={() => toggle(false)}
    >
      <div
        className="w-[640px] max-h-[80vh] overflow-auto rounded shadow-2xl"
        style={{ backgroundColor: '#f3f3f3', border: '1px solid #d0d0d0' }}
        onClick={(e) => e.stopPropagation()}
      >
        <div
          className="flex items-center justify-between border-b px-4 py-2"
          style={{ borderColor: '#e0e0e0' }}
        >
          <span className="text-ui font-semibold uppercase tracking-wide text-fg">
            键盘快捷键
          </span>
          <button
            type="button"
            onClick={() => toggle(false)}
            className='rounded px-2 text-fg-muted hover:bg-vscode-border hover:text-fg'
          >
            ✕
          </button>
        </div>

        <div className="grid grid-cols-2 gap-4 p-4">
          {Object.entries(groups).map(([group, items]) => (
            <section key={group}>
              <h3
                className="mb-2 text-2xs font-semibold uppercase tracking-wider"
                style={{ color: '#616161' }}
              >
                {group}
              </h3>
              <ul className="space-y-1">
                {items.map((s) => (
                  <li
                    key={s.keys}
                    className="flex items-center justify-between text-ui"
                  >
                    <span className="text-fg">{s.desc}</span>
                    <kbd
                      className="rounded px-2 py-0.5 font-mono text-2xs"
                      style={{
                        backgroundColor: '#ffffff',
                        border: '1px solid #d0d0d0',
                        color: '#333333',
                      }}
                    >
                      {s.keys}
                    </kbd>
                  </li>
                ))}
              </ul>
            </section>
          ))}
        </div>

        <div
          className="border-t px-4 py-2 text-2xs"
          style={{ borderColor: '#e0e0e0', color: '#616161' }}
        >
          按 Esc 或点击背景关闭
        </div>
      </div>
    </div>
  );
}
