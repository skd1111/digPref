/**
 * ToolchainSettingsPanel — 工具链与编译配置（2026-08-28 两页签合并）。
 *
 * 一个页面维护两套相关但消费方不同的配置：
 *   - 「工具路径」（Agent 侧 toolchain.json）：Coding 框架分层验证器（编译/测试）
 *     使用的可执行文件路径；未配置时按「配置路径 → PATH → 常见安装目录」探测，
 *     均失败降级为纯语法检查。
 *   - 「文件树编译」（Rust 侧 compile.json）：文件树右键「编译此文件/编译选中」
 *     使用的编译器目录 + 产物输出目录。存 Rust 侧保证 Agent 离线也能编译。
 *
 * 历史：原「编译配置」独立页签与本面板字段重叠（python / javac 配两遍），
 * 故合并到此处；两套持久化后端各自保留（离线编译依赖 Rust 侧 compile.json）。
 */
import { useEffect, useState } from 'react';
import { open } from '@tauri-apps/plugin-dialog';
import { ipc } from '@/ipc/invoke';

// ---- 区块一：工具链可执行文件路径（Agent /toolchain）----

const TOOL_FIELDS: { key: string; label: string; placeholder: string }[] = [
  { key: 'python', label: 'Python', placeholder: '如 D:\\Python312\\python.exe' },
  { key: 'node', label: 'Node.js', placeholder: '如 C:\\Program Files\\nodejs\\node.exe' },
  { key: 'pnpm', label: 'pnpm', placeholder: '如 %LOCALAPPDATA%\\pnpm\\pnpm.exe' },
  { key: 'java', label: 'Java (java)', placeholder: '如 C:\\Program Files\\Java\\jdk-17\\bin\\java.exe' },
  { key: 'javac', label: 'Java (javac)', placeholder: '如 C:\\Program Files\\Java\\jdk-17\\bin\\javac.exe' },
  { key: 'tsc', label: 'TypeScript (tsc)', placeholder: '如 %APPDATA%\\npm\\tsc.cmd' },
];

// ---- 区块二：文件树编译器目录 + 产物输出（Rust compile.json）----

interface CompileCfg {
  javac_dir: string;
  python_dir: string;
  gcc_dir: string;
  output_dir: string;
}

const EMPTY_COMPILE: CompileCfg = { javac_dir: '', python_dir: '', gcc_dir: '', output_dir: '' };

const COMPILE_FIELDS: { key: keyof CompileCfg; label: string; placeholder: string; hint: string }[] = [
  {
    key: 'javac_dir',
    label: 'Java 编译器（JDK bin 目录）',
    placeholder: '如 C:\\Program Files\\Java\\jdk-17\\bin',
    hint: '选到含 javac.exe 的目录即可；留空自动探测 PATH。编译 .java 输出 .class。',
  },
  {
    key: 'python_dir',
    label: 'Python 解释器目录',
    placeholder: '如 D:\\Python312',
    hint: '选到含 python.exe 的目录；留空自动探测 PATH。.py 走语法编译检查。',
  },
  {
    key: 'gcc_dir',
    label: 'C/C++ 编译器目录（gcc / g++）',
    placeholder: '如 C:\\msys64\\ucrt64\\bin',
    hint: '选到含 gcc.exe / g++.exe 的目录；留空自动探测 PATH。编译 .c/.cpp 输出 .o。',
  },
  {
    key: 'output_dir',
    label: '编译产物输出目录',
    placeholder: '留空 = 工作空间 workspace/compiled',
    hint: '编译产物（.class / .o）统一落这里；留空走「工作空间」设置的路径。',
  },
];

const inputStyle = {
  backgroundColor: '#ececec',
  color: '#1f1f1f',
  border: '1px solid #d4d4d4',
} as const;

export function ToolchainSettingsPanel(): JSX.Element {
  // 区块一
  const [paths, setPaths] = useState<Record<string, string>>({});
  const [toolSaving, setToolSaving] = useState(false);
  const [toolMessage, setToolMessage] = useState<string | null>(null);
  // 区块二
  const [cfg, setCfg] = useState<CompileCfg>(EMPTY_COMPILE);
  const [workspaceHint, setWorkspaceHint] = useState<string | null>(null);
  const [compileSaving, setCompileSaving] = useState(false);
  const [compileMessage, setCompileMessage] = useState<string | null>(null);

  const [loading, setLoading] = useState(true);

  useEffect(() => {
    Promise.allSettled([
      ipc.getToolchain().then((r) => setPaths(r.paths ?? {})),
      ipc.compileConfigGet().then((r) => setCfg({ ...EMPTY_COMPILE, ...r })),
      // 展示当前 workspace（输出目录默认基座），best-effort
      ipc.getWorkspace().then((ws) => setWorkspaceHint(ws.path)),
    ])
      .then((results) => {
        // 工具链 / 编译配置加载失败要提示；workspace 失败静默
        const toolErr = results[0];
        const compileErr = results[1];
        if (toolErr.status === 'rejected') setToolMessage(`加载失败：${String(toolErr.reason)}`);
        if (compileErr.status === 'rejected')
          setCompileMessage(`加载失败：${String(compileErr.reason)}`);
      })
      .finally(() => setLoading(false));
  }, []);

  const saveTools = async (): Promise<void> => {
    setToolSaving(true);
    setToolMessage(null);
    try {
      const r = await ipc.saveToolchain(paths);
      setPaths(r.paths ?? {});
      setToolMessage('已保存。未填写的工具将按 PATH / 常见安装目录自动探测。');
    } catch (e) {
      setToolMessage(`保存失败：${String(e)}`);
    } finally {
      setToolSaving(false);
    }
  };

  const saveCompile = async (): Promise<void> => {
    setCompileSaving(true);
    setCompileMessage(null);
    try {
      const r = await ipc.compileConfigSave(cfg);
      setCfg({ ...EMPTY_COMPILE, ...r });
      setCompileMessage('已保存。留空的编译器将按 PATH 自动探测。');
    } catch (e) {
      setCompileMessage(`保存失败：${String(e)}`);
    } finally {
      setCompileSaving(false);
    }
  };

  const browse = async (key: keyof CompileCfg): Promise<void> => {
    try {
      const selected = await open({
        multiple: false,
        directory: true,
        title: `选择${COMPILE_FIELDS.find((f) => f.key === key)?.label ?? '目录'}`,
      });
      if (!selected) return;
      const dir = Array.isArray(selected) ? selected[0] : selected;
      if (dir) setCfg((c) => ({ ...c, [key]: dir }));
    } catch (e) {
      window.alert(`打开文件夹对话框失败：${String(e)}\n请确认在 Tauri 桌面端运行（非浏览器）。`);
    }
  };

  return (
    <div className="mx-auto max-w-3xl space-y-6">
      <header>
        <h1 className="text-ui-lg font-semibold">工具链与编译 · Toolchain</h1>
        <p className="mt-1 text-2xs text-fg-muted">
          本机工具路径统一在此维护：上区供编程智能体验证器（编译 / 测试）使用，
          下区供文件树右键「编译此文件 / 编译选中」使用（Agent 离线也可编译）。
        </p>
      </header>

      {/* 区块一：工具链路径（Agent 验证器） */}
      <section>
        <h2
          className="mb-2 text-2xs font-semibold uppercase tracking-wider"
          style={{ color: '#616161' }}
        >
          工具路径（智能体验证器）
        </h2>
        <div
          className="space-y-3 rounded p-4"
          style={{ backgroundColor: '#f3f3f3', border: '1px solid #d4d4d4' }}
        >
          {loading ? (
            <div className="text-2xs text-fg-muted">加载中…</div>
          ) : (
            TOOL_FIELDS.map((f) => (
              <label key={f.key} className="block">
                <span className="mb-1 block text-2xs text-fg-muted">{f.label}</span>
                <input
                  type="text"
                  value={paths[f.key] ?? ''}
                  placeholder={f.placeholder}
                  onChange={(e) =>
                    setPaths((p) => ({ ...p, [f.key]: e.target.value }))
                  }
                  className="w-full rounded px-2 py-1 font-mono text-2xs outline-none"
                  style={inputStyle}
                />
              </label>
            ))
          )}
          <div className="flex items-center gap-3 pt-1">
            <button
              type="button"
              onClick={() => void saveTools()}
              disabled={toolSaving || loading}
              className="rounded px-3 py-1.5 text-xs font-semibold text-white disabled:opacity-50"
              style={{ backgroundColor: '#059669' }}
            >
              {toolSaving ? '保存中…' : '保存'}
            </button>
            {toolMessage && (
              <span className="text-2xs" style={{ color: '#616161' }}>
                {toolMessage}
              </span>
            )}
          </div>
        </div>
      </section>

      {/* 区块二：文件树编译（Rust 侧，离线可用） */}
      <section>
        <h2
          className="mb-2 text-2xs font-semibold uppercase tracking-wider"
          style={{ color: '#616161' }}
        >
          文件树编译（编译器目录 + 产物输出）
        </h2>
        <div
          className="space-y-4 rounded p-4"
          style={{ backgroundColor: '#f3f3f3', border: '1px solid #d4d4d4' }}
        >
          {loading ? (
            <div className="text-2xs text-fg-muted">加载中…</div>
          ) : (
            COMPILE_FIELDS.map((f) => (
              <label key={f.key} className="block">
                <span className="mb-1 block text-2xs font-semibold text-fg-muted">
                  {f.label}
                </span>
                <div className="flex items-center gap-2">
                  <input
                    type="text"
                    value={cfg[f.key]}
                    placeholder={f.placeholder}
                    onChange={(e) => setCfg((c) => ({ ...c, [f.key]: e.target.value }))}
                    className="flex-1 rounded px-2 py-1 font-mono text-2xs outline-none"
                    style={inputStyle}
                  />
                  <button
                    type="button"
                    onClick={() => void browse(f.key)}
                    className="rounded border px-2 py-1 text-2xs"
                    style={{ borderColor: '#d4d4d4', backgroundColor: '#ffffff', color: '#1f1f1f' }}
                  >
                    浏览…
                  </button>
                </div>
                <span className="mt-0.5 block text-[10px] text-fg-muted">{f.hint}</span>
              </label>
            ))
          )}
          <div className="flex items-center gap-3 pt-1">
            <button
              type="button"
              onClick={() => void saveCompile()}
              disabled={compileSaving || loading}
              className="rounded px-3 py-1.5 text-xs font-semibold text-white disabled:opacity-50"
              style={{ backgroundColor: '#059669' }}
            >
              {compileSaving ? '保存中…' : '保存'}
            </button>
            {compileMessage && (
              <span className="text-2xs" style={{ color: '#616161' }}>
                {compileMessage}
              </span>
            )}
          </div>
          {workspaceHint && (
            <p className="text-[10px] text-fg-muted">
              当前工作空间：<span className="font-mono">{workspaceHint}</span>
              （输出目录留空时，产物落 {workspaceHint.replace(/[\\/]+$/, '')}/compiled）
            </p>
          )}
        </div>
      </section>
    </div>
  );
}
