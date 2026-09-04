/**
 * AboutSettingPanel — 关于页 + 日志诊断。
 *
 * BUGFIX #193（2026-09-04）：
 * - 日志位置按平台动态展示（此前硬编码 Windows 路径，macOS/Linux 用户被误导）。
 * - 新增「一键导出全部日志」按钮：调用 Rust `export_all_logs` 把两路日志
 *   （Rust eaide.log/crash.log + Python agent.log/cot.log/orchestrator-*.jsonl）
 *   打包成 zip，方便用户发给支持人员排查。
 */
import { useEffect, useState } from 'react';
import { save } from '@tauri-apps/plugin-dialog';
import { platform } from '@tauri-apps/plugin-os';
import { ipc } from '@/ipc/invoke';

interface LogPathInfo {
  label: string;
  rustLog: string;
  pythonLog: string;
  cotLog: string;
  crashLog: string;
}

function getLogPaths(): LogPathInfo {
  let p: string;
  try {
    p = platform();
  } catch {
    p = 'windows'; // 非 Tauri 环境（vitest）兜底
  }
  if (p === 'macos') {
    const base = '~/Library/Application Support/eaide';
    return {
      label: 'macOS',
      rustLog: `${base}/logs/eaide.log`,
      pythonLog: `${base}/logs/agent.log`,
      cotLog: `${base}/logs/cot.log`,
      crashLog: `${base}/logs/crash.log`,
    };
  }
  if (p === 'linux') {
    const base = '~/.local/share/eaide';
    return {
      label: 'Linux',
      rustLog: `${base}/logs/eaide.log`,
      pythonLog: `${base}/logs/agent.log`,
      cotLog: `${base}/logs/cot.log`,
      crashLog: `${base}/logs/crash.log`,
    };
  }
  // Windows 默认：安装目录（exe 父目录）
  return {
    label: 'Windows',
    rustLog: '<安装目录>\\logs\\eaide.log',
    pythonLog: '<安装目录>\\logs\\agent.log',
    cotLog: '<安装目录>\\logs\\cot.log',
    crashLog: '<安装目录>\\logs\\crash.log',
  };
}

export function AboutSettingPanel(): JSX.Element {
  const paths = getLogPaths();
  const [exporting, setExporting] = useState(false);
  const [lastResult, setLastResult] = useState<string | null>(null);

  // 组件挂载时清掉上次结果（避免切换面板后残留）
  useEffect(() => {
    setLastResult(null);
  }, []);

  const onExportLogs = async (): Promise<void> => {
    if (exporting) return;
    const ts = new Date()
      .toISOString()
      .replace(/[:.]/g, '-')
      .replace('T', '_')
      .slice(0, 19);
    let picked: string | null = null;
    try {
      picked = await save({
        defaultPath: `eaide-logs-${ts}.zip`,
        filters: [{ name: 'Zip 压缩包', extensions: ['zip'] }],
        title: '导出 EAIDE 全部日志',
      });
    } catch {
      // 非 Tauri 环境（vitest）无对话框，静默降级
      return;
    }
    if (!picked) return; // 用户取消

    setExporting(true);
    setLastResult(null);
    try {
      const res = await ipc.exportAllLogs(picked);
      const sizeKb = (res.total_bytes / 1024).toFixed(1);
      const missingNote =
        res.missing.length > 0 ? `\n缺失来源 ${res.missing.length} 个（详见 zip 内 MANIFEST.txt）` : '';
      const msg = `导出成功：${res.path}\n包含 ${res.file_count} 个文件，共 ${sizeKb} KB${missingNote}`;
      setLastResult(msg);
      alert(msg);
    } catch (err) {
      const msg = `导出失败：${err instanceof Error ? err.message : String(err)}`;
      setLastResult(msg);
      alert(msg);
    } finally {
      setExporting(false);
    }
  };

  return (
    <div className="mx-auto max-w-3xl space-y-6">
      <header>
        <h1 className="text-ui-lg font-semibold">About</h1>
        <p className="mt-1 text-2xs text-fg-muted">EAIDE — Enterprise Local AI IDE</p>
      </header>

      <section
        className="rounded p-4 text-ui"
        style={{ backgroundColor: '#f3f3f3', border: '1px solid #d4d4d4' }}
      >
        <table className="w-full">
          <tbody>
            <Row k="应用版本" v="0.1.0" />
            <Row k="Tauri" v="2.x" />
            <Row k="前端" v="React 18 + TypeScript + Tailwind" />
            <Row k="后端 Agent" v="Python 3.10+ / FastAPI / LangGraph" />
            <Row k="MCP 服务" v="stdio" />
            <Row k="凭证" v="OS Keychain (Keyring crate)" />
            <Row k="审计" v="SQLite (Rust + Python 共享)" />
            <Row k="当前平台" v={paths.label} />
          </tbody>
        </table>
      </section>

      <section>
        <div className="mb-2 flex items-center justify-between">
          <h2
            className="text-2xs font-semibold uppercase tracking-wider"
            style={{ color: '#616161' }}
          >
            日志位置（{paths.label}）
          </h2>
          <button
            type="button"
            onClick={() => void onExportLogs()}
            disabled={exporting}
            className="rounded px-3 py-1 text-2xs font-medium text-white transition-colors disabled:opacity-50"
            style={{ backgroundColor: exporting ? '#9aa0a6' : '#0e639c' }}
            title="把 Rust + Python 两侧全部日志打包成 zip，方便发给支持人员"
          >
            {exporting ? '导出中…' : '📦 一键导出全部日志'}
          </button>
        </div>
        <div
          className="rounded p-3 font-mono text-2xs"
          style={{ backgroundColor: '#f3f3f3', border: '1px solid #d4d4d4' }}
        >
          <ul className="space-y-0.5 text-fg-muted">
            <li>· <span className="text-fg">eaide.log</span> — Rust 主进程常规运行：{paths.rustLog}</li>
            <li>· <span className="text-fg">crash.log</span> — Rust panic / 致命错误（含 backtrace）：{paths.crashLog}</li>
            <li>· <span className="text-fg">agent.log</span> — Python Agent 主日志（FastAPI/LangGraph/LLM 路由）：{paths.pythonLog}</li>
            <li>· <span className="text-fg">cot.log</span> — 意图识别 / 思维链全链路：{paths.cotLog}</li>
            <li>· <span className="text-fg">orchestrator-YYYYMMDD.jsonl</span> — 结构化事件日志（同目录）</li>
          </ul>
          {lastResult && (
            <div
              className="mt-2 rounded p-2 text-2xs"
              style={{
                backgroundColor: lastResult.startsWith('导出成功') ? '#e6f4ea' : '#fce8e6',
                color: lastResult.startsWith('导出成功') ? '#137333' : '#c5221f',
                border: `1px solid ${lastResult.startsWith('导出成功') ? '#ceead6' : '#f5c6cb'}`,
              }}
            >
              {lastResult}
            </div>
          )}
        </div>
        <p className="mt-2 text-2xs text-fg-muted">
          提示：导出 zip 内含 MANIFEST.txt，列出每个文件的来源路径与打包状态（OK / MISSING / 错误原因），
          便于支持人员快速定位缺失环节。配置文件（environments.json / llm-config.json）<strong>不</strong>会被导出，避免泄露密钥。
        </p>
      </section>
    </div>
  );
}

function Row({ k, v }: { k: string; v: string }): JSX.Element {
  return (
    <tr>
      <td className="py-1 text-fg-muted">{k}</td>
      <td className="py-1">{v}</td>
    </tr>
  );
}
