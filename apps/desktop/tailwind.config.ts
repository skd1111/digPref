import type { Config } from 'tailwindcss';

const config: Config = {
  content: ['./index.html', './src/**/*.{ts,tsx}'],
  darkMode: 'class',
  theme: {
    extend: {
      colors: {
        // —— VSCode 风格浅色调色板（白底黑字）——
        // 命名遵循 --vscode-* 约定，方便后续直接对齐官方主题 token。
        // 后期新增「彩色文字」类功能时，请优先在此扩展语义 token
        // （如 status / syntax），不要散落硬编码 hex。
        vscode: {
          // 编辑器主背景（"内容"区域）
          'editor-bg': '#ffffff',
          'editor-fg': '#1f1f1f',
          // 侧边栏
          'sideBar-bg': '#f3f3f3',
          'sideBar-fg': '#333333',
          // 活动栏（最左图标列）
          'activityBar-bg': '#f3f3f3',
          'activityBar-fg': '#1f1f1f',
          'activityBar-inactiveFg': '#616161',
          'activityBar-activeBorder': '#007acc',
          // 状态栏
          'statusBar-bg': '#007acc',
          'statusBar-fg': '#ffffff',
          'statusBar-noFolder-bg': '#68217a',
          // 标题栏
          'titleBar-activeBg': '#ececec',
          'titleBar-activeFg': '#333333',
          'titleBar-inactiveBg': '#ececec',
          // 菜单栏
          'menu-bg': '#f3f3f3',
          'menu-fg': '#333333',
          'menu-hover': '#d0d0d0',
          // tab
          'tab-activeBg': '#ffffff',
          'tab-inactiveBg': '#ececec',
          'tab-inactiveFg': '#6e6e6e',
          'tab-border': '#f3f3f3',
          'tab-activeBorder-top': '#007acc',
          // 终端
          'terminal-bg': '#ffffff',
          'terminal-ansiBlack': '#000000',
          'terminal-ansiRed': '#cd3131',
          'terminal-ansiGreen': '#0dbc79',
          'terminal-ansiYellow': '#e5e510',
          'terminal-ansiBlue': '#2472c8',
          'terminal-ansiMagenta': '#bc3fbc',
          'terminal-ansiCyan': '#11a8cd',
          'terminal-ansiWhite': '#e5e5e5',
          // 通用前景
          'fg-muted': '#616161',
          'border': '#d4d4d4',
          'border-strong': '#b8b8b8',
          // 行号
          'lineNumber-fg': '#8a8a8a',
          'lineNumber-activeFg': '#1f1f1f',
        },
        // —— 自定义扩展 ——
        bg: {
          base: '#ffffff',     // 别名到 vscode.editor-bg
          panel: '#f3f3f3',    // 别名到 vscode.sideBar-bg
          subtle: '#ececec',
          code: '#f6f8fa',
        },
        border: {
          DEFAULT: '#d4d4d4',
          strong: '#b8b8b8',
        },
        fg: {
          DEFAULT: '#1f1f1f',
          muted: '#616161',
          dim: '#6e6e6e',
        },
        accent: {
          DEFAULT: '#007acc',
          approval: '#0dbc79',
          warn: '#e5e510',
          danger: '#cd3131',
        },
      },
      fontFamily: {
        mono: ['"Cascadia Code"', '"JetBrains Mono"', 'Consolas', 'ui-monospace', 'monospace'],
        sans: ['"Segoe UI Variable"', '"Segoe UI"', 'system-ui', 'sans-serif'],
      },
      fontSize: {
        // VSCode 12px 是 UI 元素标准尺寸
        '2xs': ['11px', '14px'],
        'ui': ['12px', '18px'],
        'ui-lg': ['13px', '20px'],
      },
    },
  },
  plugins: [],
};

export default config;
