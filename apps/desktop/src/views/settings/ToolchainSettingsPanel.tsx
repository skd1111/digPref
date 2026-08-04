/**
 * ToolchainSettingsPanel — Phase 18 工具链路径配置。
 *
 * Coding 框架分层验证器（编译/测试）依赖本机工具链；未配置时智能体按
 * 「配置路径 → PATH → 常见安装目录」探测，均失败则降级为纯语法检查。
 */
import { useEffect, useState } from 'react';
import { ipc } from '@/ipc/invoke';

const FIELDS: { key: string; label: string; placeholder: string }[] = [
  { key: 'python', label: 'Python', placeholder: '如 D:\\Python312\\python.exe' },
  { key: 'node', label: 'Node.js', placeholder: '如 C:\\Program Files\\nodejs\\node.exe' },
  { key: 'pnpm', label: 'pnpm', placeholder: '如 %LOCALAPPDATA%\\pnpm\\pnpm.exe' },
  { key: 'java', label: 'Java (java)', placeholder: '如 C:\\Program Files\\Java\\jdk-17\\bin\\java.exe' },
  { key: 'javac', label: 'Java (javac)', placeholder: '如 C:\\Program Files\\Java\\jdk-17\\bin\\javac.exe' },
  { key: 'tsc', label: 'TypeScript (tsc)', placeholder: '如 %APPDATA%\\npm\\tsc.cmd' },
];

export function ToolchainSettingsPanel(): JSX.Element {
  const [paths, setPaths] = useState<Record<string, string>>({});
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [message, setMessage] = useState<string | null>(null);

  useEffect(() => {
    ipc
      .getToolchain()
      .then((r) => setPaths(r.paths ?? {}))
      .catch((e) => setMessage(`加载失败：${String(e)}`))
      .finally(() => setLoading(false));
  }, []);

  const save = async (): Promise<void> => {
    setSaving(true);
    setMessage(null);
    try {
      const r = await ipc.saveToolchain(paths);
      setPaths(r.paths ?? {});
      setMessage('已保存。未填写的工具将按 PATH / 常见安装目录自动探测。');
    } catch (e) {
      setMessage(`保存失败：${String(e)}`);
    } finally {
      setSaving(false);
    }
  };

  return (
    <div className="mx-auto max-w-3xl space-y-6">
      <header>
        <h1 className="text-ui-lg font-semibold">工具链 · Toolchain</h1>
        <p className="mt-1 text-2xs text-fg-muted">
          编程智能体验证器（编译 / 测试）使用的本机工具路径。留空则自动探测
          （PATH → 常见安装目录）；都找不到时降级为纯语法检查并明确告知。
        </p>
      </header>

      <section>
        <h2
          className="mb-2 text-2xs font-semibold uppercase tracking-wider"
          style={{ color: '#616161' }}
        >
          工具路径
        </h2>
        <div
          className="space-y-3 rounded p-4"
          style={{ backgroundColor: '#f3f3f3', border: '1px solid #d4d4d4' }}
        >
          {loading ? (
            <div className="text-2xs text-fg-muted">加载中…</div>
          ) : (
            FIELDS.map((f) => (
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
                  style={{
                    backgroundColor: '#ececec',
                    color: '#1f1f1f',
                    border: '1px solid #d4d4d4',
                  }}
                />
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
        </div>
      </section>
    </div>
  );
}
