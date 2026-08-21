/**
 * CompileSettingsPanel — 编译配置面板（2026-08-19 用户要求）。
 *
 * 文件树右键编译使用的编译器目录 + 产物输出目录：
 *   - 每项支持手动输入路径或「浏览…」文件夹对话框选择
 *   - 编译器留空 → 自动探测 PATH
 *   - 输出目录留空 → 默认 workspace/compiled（workspace 见「工作空间」面板）
 *
 * 持久化在 Rust 侧 compile.json（安装目录），与 Agent 解耦（Agent 离线也能编译）。
 */
import { useEffect, useState } from 'react';
import { open } from '@tauri-apps/plugin-dialog';
import { ipc } from '@/ipc/invoke';

interface CompileCfg {
  javac_dir: string;
  python_dir: string;
  gcc_dir: string;
  output_dir: string;
}

const EMPTY: CompileCfg = { javac_dir: '', python_dir: '', gcc_dir: '', output_dir: '' };

const FIELDS: { key: keyof CompileCfg; label: string; placeholder: string; hint: string }[] = [
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

export function CompileSettingsPanel(): JSX.Element {
  const [cfg, setCfg] = useState<CompileCfg>(EMPTY);
  const [workspaceHint, setWorkspaceHint] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [message, setMessage] = useState<string | null>(null);

  useEffect(() => {
    ipc
      .compileConfigGet()
      .then((r) => setCfg({ ...EMPTY, ...r }))
      .catch((e) => setMessage(`加载失败：${String(e)}`))
      .finally(() => setLoading(false));
    // 展示当前 workspace（输出目录默认基座），best-effort
    ipc
      .getWorkspace()
      .then((ws) => setWorkspaceHint(ws.path))
      .catch(() => undefined);
  }, []);

  const browse = async (key: keyof CompileCfg): Promise<void> => {
    try {
      const selected = await open({
        multiple: false,
        directory: true,
        title: `选择${FIELDS.find((f) => f.key === key)?.label ?? '目录'}`,
      });
      if (!selected) return;
      const dir = Array.isArray(selected) ? selected[0] : selected;
      if (dir) setCfg((c) => ({ ...c, [key]: dir }));
    } catch (e) {
      window.alert(`打开文件夹对话框失败：${String(e)}\n请确认在 Tauri 桌面端运行（非浏览器）。`);
    }
  };

  const save = async (): Promise<void> => {
    setSaving(true);
    setMessage(null);
    try {
      const r = await ipc.compileConfigSave(cfg);
      setCfg({ ...EMPTY, ...r });
      setMessage('已保存。留空的编译器将按 PATH 自动探测。');
    } catch (e) {
      setMessage(`保存失败：${String(e)}`);
    } finally {
      setSaving(false);
    }
  };

  return (
    <div className="mx-auto max-w-3xl space-y-6">
      <header>
        <h1 className="text-ui-lg font-semibold">编译配置 · Compile</h1>
        <p className="mt-1 text-2xs text-fg-muted">
          文件树右键「编译此文件 / 编译选中」使用的编译器与产物输出位置。
          文件列表里 Ctrl+单击可多选文件 / 目录后一键编译。
        </p>
      </header>

      <section>
        <h2
          className="mb-2 text-2xs font-semibold uppercase tracking-wider"
          style={{ color: '#616161' }}
        >
          编译器与输出目录
        </h2>
        <div
          className="space-y-4 rounded p-4"
          style={{ backgroundColor: '#f3f3f3', border: '1px solid #d4d4d4' }}
        >
          {loading ? (
            <div className="text-2xs text-fg-muted">加载中…</div>
          ) : (
            FIELDS.map((f) => (
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
                    style={{
                      backgroundColor: '#ececec',
                      color: '#1f1f1f',
                      border: '1px solid #d4d4d4',
                    }}
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
              onClick={() => void save()}
              disabled={saving || loading}
              className="rounded px-3 py-1.5 text-xs font-semibold text-white disabled:opacity-50"
              style={{ backgroundColor: '#059669' }}
            >
              {saving ? '保存中…' : '保存'}
            </button>
            {message && (
              <span className="text-2xs" style={{ color: '#616161' }}>
                {message}
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
