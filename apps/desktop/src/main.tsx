/**
 * Tauri + React entry point.
 * Mounts the App into #root and registers global keyboard shortcuts.
 */
import React from 'react';
import ReactDOM from 'react-dom/client';
import { loader } from '@monaco-editor/react';
import * as monaco from 'monaco-editor';
import { App } from './App';
import { ShortcutsBridge } from './components/chrome/ShortcutsBridge';
import './styles/globals.css';

// 关键：把 @monaco-editor/react 的 loader 指向本地打包的 monaco-editor，
// 而不是默认的 cdn.jsdelivr.net（Tauri 内网环境无法访问 CDN + CSP 不放行）。
loader.config({ monaco });

// 防止 Tauri Windows WebView2 弹出原生右键菜单覆盖 Monaco 的自定义菜单。
// Monaco 内部已正确调用 preventDefault()，但 WebView2 在某些版本中会忽略。
// 这里对 .monaco-editor 区域强制阻止原生 contextmenu，确保 Monaco 的
// VSCode 风格右键菜单（复制/粘贴/剪切/撤销/全选/跳转定义…）正常显示。
document.addEventListener('contextmenu', (e) => {
  const target = e.target as HTMLElement;
  if (target.closest('.monaco-editor')) {
    e.preventDefault();
  }
});

ReactDOM.createRoot(document.getElementById('root') as HTMLElement).render(
  <React.StrictMode>
    <ShortcutsBridge />
    <App />
  </React.StrictMode>
);
