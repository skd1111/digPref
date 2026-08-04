/**
 * MenuBar — 顶部菜单栏（VSCode 风格）。
 *
 * 菜单项接入真实操作：File / Edit / View / Run / Terminal / Help 各项
 * 都有对应的 command 触发逻辑（命令面板命令 + chatStore + uiStore）。
 *
 * 注意：活跃环境徽章 + 模式切换器已经搬到独立 TopBar（更醒目），
 *      MenuBar 这里只保留菜单项，不再嵌入那两个组件。
 */
import { useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { useChatStore } from '@/store/chatStore';
import { useUIStore } from '@/store/uiStore';
import {
  cloneFromGit,
  pickAndImportFolder,
  pickAndOpenFile,
} from '@/components/codenav/fileOps';

const MENUS: { label: string; items: string[] }[] = [
  {
    label: 'File',
    items: [
      'New Chat',
      '—',
      'Open File…',
      'Open Folder…',
      'Clone from Git…',
      '—',
      'Open Conversation…',
      'Save Transcript',
      'Export…',
      '—',
      'Settings…',
      'Exit',
    ],
  },
  { label: 'Edit', items: ['Undo', 'Redo', '—', 'Cut', 'Copy', 'Paste', 'Find'] },
  { label: 'View', items: ['Command Palette', 'Quick Open', '—', 'Toggle Activity Bar', 'Toggle Side Bar', 'Toggle Status Bar', '—', 'Zoom In', 'Zoom Out', 'Reset Zoom'] },
  { label: 'Run', items: ['Start Agent', 'Cancel Run', 'Restart Agent Service'] },
  { label: 'Terminal', items: ['New Terminal', 'Clear Output', 'Kill Process'] },
  { label: 'Help', items: ['Documentation', 'Keyboard Shortcuts', 'About EAIDE', 'View Logs…'] },
];

export function MenuBar(): JSX.Element {
  const [openIdx, setOpenIdx] = useState<number | null>(null);
  const navigate = useNavigate();
  const newTab = useChatStore((s) => s.newTab);
  const toggleCommandPalette = useUIStore((s) => s.toggleCommandPalette);
  const toggleQuickOpen = useUIStore((s) => s.toggleQuickOpen);
  const toggleCheatSheet = useUIStore((s) => s.toggleCheatSheet);

  // 把菜单项名字映射到具体动作
  const runAction = (label: string): void => {
    setOpenIdx(null);
    switch (label) {
      case 'New Chat':
        newTab();
        break;
      case 'Command Palette':
        toggleCommandPalette();
        break;
      case 'Quick Open':
        toggleQuickOpen();
        break;
      case 'Keyboard Shortcuts':
        toggleCheatSheet();
        break;
      case 'Settings…':
        navigate('/settings');
        break;
      case 'Open File…':
        // eslint-disable-next-line no-console
        console.info('[MenuBar] Open File triggered');
        void pickAndOpenFile();
        break;
      case 'Open Folder…':
        void pickAndImportFolder();
        break;
      case 'Clone from Git…':
        void cloneFromGit();
        break;
      case 'Exit':
        // 由 Tauri 窗口关闭走 close-requested 流程（关窗口会触发 agent kill）
        // React 端通过 window.close() 让浏览器走原生路径
        window.close();
        break;
      default:
        alert(`"${label}" — 占位功能，待实现`);
    }
  };

  return (
    <div
      className="flex h-[26px] select-none items-stretch border-b text-ui"
      style={{
        backgroundColor: '#ececec',
        color: '#333333',
        borderColor: '#d0d0d0',
      }}
      onMouseLeave={() => setOpenIdx(null)}
    >
      {/* 左：菜单项 */}
      {MENUS.map((menu, i) => (
        <div key={menu.label} className="relative">
          <button
            type="button"
            className="h-full px-3 transition-colors hover:bg-vscode-border"
            style={openIdx === i ? { backgroundColor: '#d0d0d0' } : undefined}
            onMouseEnter={() => setOpenIdx(i)}
            onClick={() => setOpenIdx((v) => (v === i ? null : i))}
          >
            {menu.label}
          </button>

          {openIdx === i && (
            <div
              className="absolute left-0 top-full z-50 min-w-[220px] border text-ui shadow-xl"
              style={{
                backgroundColor: '#f3f3f3',
                borderColor: '#d0d0d0',
              }}
              onClick={() => setOpenIdx(null)}
            >
              {menu.items.map((item, j) =>
                item === '—' ? (
                  <div key={j} className="my-0.5 h-px" style={{ backgroundColor: '#d0d0d0' }} />
                ) : (
                  <div
                    key={j}
                    onClick={() => runAction(item)}
                    className="cursor-pointer px-3 py-1 transition-colors hover:bg-vscode-border"
                  >
                    {item}
                  </div>
                ),
              )}
            </div>
          )}
        </div>
      ))}
    </div>
  );
}
