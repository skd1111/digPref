/**
 * WorkspaceSettingsPanel — 工作空间路径配置。
 *
 * 底层规则：智能体运行中创建的任何文件默认都落到当前工作空间内，
 * 并按类型自动分类建目录（docs / data / images / other）；
 * 仅当用户在对话中显式指定输出目录时才尊重用户指定。
 *
 * 默认为安装目录下 /workspace/，此处可自定义；清空保存 = 恢复默认。
 */
import { useCallback, useEffect, useState } from 'react';
import { ipc } from '@/ipc/invoke';

export function WorkspaceSettingsPanel(): JSX.Element {
  const [current, setCurrent] = useState('');
  const [defaultValue, setDefaultValue] = useState('');
  const [custom, setCustom] = useState<string | null>(null);
  const [input, setInput] = useState('');
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [message, setMessage] = useState<string | null>(null);

  const apply = useCallback(
    (r: { path: string; custom: string | null; default: string }): void => {
      setCurrent(r.path);
      setDefaultValue(r.default);
      setCustom(r.custom);
      setInput(r.custom ?? '');
    },
    [],
  );

  useEffect(() => {
    ipc
      .getWorkspace()
      .then(apply)
      .catch((e) => setMessage(`加载失败：${String(e)}`))
      .finally(() => setLoading(false));
  }, [apply]);

  const save = async (): Promise<void> => {
    setSaving(true);
    setMessage(null);
    try {
      const r = await ipc.saveWorkspace(input.trim());
      apply(r);
      setMessage(
        r.custom
          ? `已保存，工作空间切换到 ${r.path}`
          : '已恢复默认（安装目录/workspace）。',
      );
    } catch (e) {
      setMessage(`保存失败：${String(e)}`);
    } finally {
      setSaving(false);
    }
  };

  return (
    <div className="mx-auto max-w-3xl space-y-6">
      <header>
        <h1 className="text-ui-lg font-semibold">工作空间 · Workspace</h1>
        <p className="mt-1 text-2xs text-fg-muted">
          智能体运行中创建的文件默认都保存到工作空间内，并按类型自动分类建目录
          （docs / data / images / other）；对话中显式指定输出目录时以指定为准。
          默认为安装目录下 /workspace/，支持自定义。
        </p>
      </header>

      <section>
        <h2
          className="mb-2 text-2xs font-semibold uppercase tracking-wider"
          style={{ color: '#616161' }}
        >
          路径配置
        </h2>
        <div
          className="space-y-3 rounded p-4"
          style={{ backgroundColor: '#f3f3f3', border: '1px solid #d4d4d4' }}
        >
          {loading ? (
            <div className="text-2xs text-fg-muted">加载中…</div>
          ) : (
            <>
              <div className="space-y-1">
                <div className="text-2xs text-fg-muted">当前生效</div>
                <div className="rounded px-2 py-1 font-mono text-2xs">
                  {current}
                </div>
                <div className="text-2xs text-fg-muted">
                  {custom ? '（自定义）' : '（默认）'} · 默认值：{defaultValue}
                </div>
              </div>
              <label className="block">
                <span className="mb-1 block text-2xs text-fg-muted">
                  自定义工作空间路径（留空保存 = 恢复默认）
                </span>
                <input
                  type="text"
                  value={input}
                  placeholder={defaultValue}
                  onChange={(e) => setInput(e.target.value)}
                  className="w-full rounded px-2 py-1 font-mono text-2xs outline-none"
                  style={{
                    backgroundColor: '#ececec',
                    color: '#1f1f1f',
                    border: '1px solid #d4d4d4',
                  }}
                />
              </label>
            </>
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
