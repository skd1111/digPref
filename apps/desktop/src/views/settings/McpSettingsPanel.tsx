/**
 * McpSettingsPanel —— MCP 服务器配置面板（设置页顶级页签 /settings/mcp）。
 *
 * 功能：
 *   - 列出 / 新增 / 编辑 / 删除 mcp.yaml 里注册的 MCP server（stdio transport）
 *   - 连通性测试：真实拉起目标进程做 MCP 握手 + list_tools（Agent 侧执行）
 *   - 模板快速添加：duckduckgo / brave-search / tavily / open-websearch
 *   - 保存后一键热重载（免重启 Agent）
 *
 * 安全红线：
 *   - env 敏感值只允许 `__KEYRING_REF:<account>__` 占位符（Agent 侧也会硬拒明文密钥）；
 *     真实密钥请在「Secrets」页签绑定到系统 keychain。
 */
import { useEffect, useState } from 'react';
import { ipc } from '@/ipc/invoke';
import type { McpConfigResponse, McpServerSpec, McpTestResult } from '@/ipc/invoke';

// ---- 模板：常见现成 MCP server 一键添加 ------------------------------------

interface McpTemplate {
  key: string;
  label: string;
  hint: string;
  name: string;
  spec: McpServerSpec;
}

const KEYRING_HINT = '（真实密钥去「Secrets」页签绑定，这里只写占位符）';

const TEMPLATES: McpTemplate[] = [
  {
    key: 'duckduckgo',
    label: 'DuckDuckGo 搜索',
    hint: '免费 · 无需 API key · Python (uvx)',
    name: 'websearch',
    spec: {
      command: 'uvx',
      args: ['duckduckgo-mcp-server'],
      env: {},
      allowed_tools: [],
      auto_start: false,
      working_dir: null,
    },
  },
  {
    key: 'brave',
    label: 'Brave Search',
    hint: '需 BRAVE_API_KEY · Node (npx)',
    name: 'brave-search',
    spec: {
      command: 'npx',
      args: ['-y', '@modelcontextprotocol/server-brave-search'],
      env: { BRAVE_API_KEY: '__KEYRING_REF:mcp.brave.api_key__' },
      allowed_tools: [],
      auto_start: false,
      working_dir: null,
    },
  },
  {
    key: 'tavily',
    label: 'Tavily 搜索+提取',
    hint: '需 TAVILY_API_KEY · Node (npx)',
    name: 'tavily',
    spec: {
      command: 'npx',
      args: ['-y', 'tavily-mcp@latest'],
      env: { TAVILY_API_KEY: '__KEYRING_REF:mcp.tavily.api_key__' },
      allowed_tools: [],
      auto_start: false,
      working_dir: null,
    },
  },
  {
    key: 'open-websearch',
    label: 'OpenWebSearch 多引擎',
    hint: '免费 · Bing/Baidu/DDG 聚合 · Node (npx)',
    name: 'open-websearch',
    spec: {
      command: 'npx',
      args: ['-y', 'open-websearch@latest'],
      env: {},
      allowed_tools: [],
      auto_start: false,
      working_dir: null,
    },
  },
];

function emptySpec(): McpServerSpec {
  return { command: '', args: [], env: {}, allowed_tools: [], auto_start: false, working_dir: null };
}

// ---- 主组件 ----------------------------------------------------------------

export function McpSettingsPanel(): JSX.Element {
  const [config, setConfig] = useState<McpConfigResponse | null>(null);
  const [servers, setServers] = useState<Record<string, McpServerSpec>>({});
  const [dirty, setDirty] = useState(false);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [info, setInfo] = useState<string | null>(null);
  const [editing, setEditing] = useState<{ name: string; isNew: boolean; spec: McpServerSpec } | null>(null);
  const [testResults, setTestResults] = useState<Record<string, McpTestResult>>({});

  const refresh = async (): Promise<void> => {
    setError(null);
    setLoading(true);
    try {
      const c = await ipc.mcpConfigGet();
      setConfig(c);
      setServers(c.servers ?? {});
      setDirty(false);
    } catch (e) {
      setError(String(e));
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    void refresh();
  }, []);

  const names = Object.keys(servers);

  const save = async (): Promise<void> => {
    setBusy('save');
    setError(null);
    setInfo(null);
    try {
      await ipc.mcpConfigSave(servers);
      setDirty(false);
      setInfo('✅ 已保存到 mcp.yaml');
    } catch (e) {
      setError(String(e));
    } finally {
      setBusy(null);
    }
  };

  const reload = async (): Promise<void> => {
    setBusy('reload');
    setError(null);
    setInfo(null);
    try {
      const r = await ipc.mcpConfigReload();
      setInfo(`✅ 已热重载 ${r.servers.length} 个 MCP server：${r.servers.join(', ') || '（无）'}`);
    } catch (e) {
      setError(String(e));
    } finally {
      setBusy(null);
    }
  };

  const removeServer = (name: string): void => {
    if (!window.confirm(`删除 MCP server「${name}」？（保存后生效）`)) return;
    const next = { ...servers };
    delete next[name];
    setServers(next);
    setDirty(true);
  };

  const testServer = async (name: string, spec: McpServerSpec): Promise<void> => {
    setBusy(`test:${name}`);
    setTestResults((prev) => ({ ...prev, [name]: { ok: false, error: '测试中…' } }));
    try {
      const r = await ipc.mcpConfigTest({ name, ...spec });
      setTestResults((prev) => ({ ...prev, [name]: r }));
    } catch (e) {
      setTestResults((prev) => ({ ...prev, [name]: { ok: false, error: String(e) } }));
    } finally {
      setBusy(null);
    }
  };

  const addTemplate = (tpl: McpTemplate): void => {
    let name = tpl.name;
    let i = 2;
    while (servers[name]) name = `${tpl.name}-${i++}`;
    setServers((prev) => ({ ...prev, [name]: tpl.spec }));
    setDirty(true);
    setInfo(`已添加模板「${tpl.label}」→ ${name}，检查无误后点「保存」`);
  };

  const openCreate = (): void => setEditing({ name: '', isNew: true, spec: emptySpec() });
  const openEdit = (name: string): void =>
    setEditing({ name, isNew: false, spec: { ...servers[name] } });

  const commitEdit = (newName: string, spec: McpServerSpec): void => {
    if (!editing) return;
    const next = { ...servers };
    if (editing.isNew || newName !== editing.name) delete next[editing.name];
    next[newName] = spec;
    setServers(next);
    setDirty(true);
    setEditing(null);
  };

  return (
    <div className="mx-auto max-w-4xl space-y-6">
      <header className="flex items-center justify-between">
        <div>
          <h1 className="text-ui-lg font-semibold">MCP · 服务器配置</h1>
          <p className="mt-1 text-2xs text-fg-muted">
            管理 mcp.yaml 注册的 MCP server（stdio）。敏感值只写{' '}
            <code className="font-mono">__KEYRING_REF:</code> 占位符{KEYRING_HINT}。
          </p>
          {config && (
            <p className="mt-1 font-mono text-2xs text-fg-muted">📄 {config.path}</p>
          )}
        </div>
        <div className="flex gap-2">
          <button
            type="button"
            onClick={() => void save()}
            disabled={!dirty || busy !== null}
            className="rounded px-3 py-1 text-ui font-semibold disabled:opacity-50"
            style={{ backgroundColor: '#007acc', color: '#ffffff' }}
          >
            {busy === 'save' ? '保存中…' : '💾 保存'}
          </button>
          <button
            type="button"
            onClick={() => void reload()}
            disabled={busy !== null}
            className="rounded px-3 py-1 text-ui"
            style={{ backgroundColor: '#ececec', color: '#333333', border: '1px solid #b8b8b8' }}
          >
            {busy === 'reload' ? '重载中…' : '⟳ 热重载'}
          </button>
          <button
            type="button"
            onClick={() => void refresh()}
            className="rounded px-3 py-1 text-ui"
            style={{ backgroundColor: '#ececec', color: '#333333', border: '1px solid #b8b8b8' }}
          >
            ↻ 刷新
          </button>
        </div>
      </header>

      {error && (
        <div
          className="rounded border px-3 py-2 text-2xs"
          style={{ backgroundColor: '#fbeaea', borderColor: '#ff5566', color: '#ff8888' }}
        >
          {error}
        </div>
      )}
      {info && (
        <div
          className="rounded border px-3 py-2 text-2xs"
          style={{ backgroundColor: '#eafbee', borderColor: '#4ec959', color: '#2e7d32' }}
        >
          {info}
        </div>
      )}

      {/* 模板快速添加 */}
      <section>
        <h3 className="mb-2 text-2xs font-semibold uppercase tracking-wider text-fg-muted">
          模板快速添加（联网搜索等常用 server）
        </h3>
        <div className="grid grid-cols-2 gap-2">
          {TEMPLATES.map((tpl) => (
            <button
              key={tpl.key}
              type="button"
              onClick={() => addTemplate(tpl)}
              className="rounded p-2 text-left transition-colors hover:bg-vscode-border"
              style={{ backgroundColor: '#f3f3f3', border: '1px solid #d4d4d4' }}
            >
              <div className="text-ui font-semibold">＋ {tpl.label}</div>
              <div className="mt-0.5 text-2xs text-fg-muted">{tpl.hint}</div>
            </button>
          ))}
        </div>
      </section>

      {/* 已注册列表 */}
      <section>
        <header className="mb-2 flex items-center justify-between">
          <h3 className="text-2xs font-semibold uppercase tracking-wider text-fg-muted">
            已注册（{names.length}）
          </h3>
          <button
            type="button"
            onClick={openCreate}
            className="rounded px-2 py-0.5 text-2xs"
            style={{ backgroundColor: '#ececec', color: '#333333', border: '1px solid #d4d4d4' }}
          >
            ＋ 新增空白
          </button>
        </header>

        {loading ? (
          <div className="text-2xs text-fg-muted">加载中…</div>
        ) : names.length === 0 ? (
          <div
            className="rounded p-6 text-center text-2xs text-fg-muted"
            style={{ backgroundColor: '#f3f3f3', border: '1px dashed #d4d4d4' }}
          >
            尚未注册任何 MCP server。上方模板可一键添加联网搜索能力（如 DuckDuckGo）。
          </div>
        ) : (
          <div className="space-y-2">
            {names.map((name) => {
              const spec = servers[name];
              const result = testResults[name];
              const envKeys = Object.keys(spec.env ?? {});
              return (
                <div
                  key={name}
                  className="rounded p-3"
                  style={{ backgroundColor: '#ffffff', border: '1px solid #d4d4d4' }}
                >
                  <div className="flex items-center justify-between">
                    <div className="flex items-center gap-2">
                      <span className="font-mono text-ui font-semibold">{name}</span>
                      {spec.auto_start && (
                        <span
                          className="rounded px-1.5 text-2xs"
                          style={{ backgroundColor: '#e8f0fe', color: '#007acc' }}
                        >
                          auto-start
                        </span>
                      )}
                      {spec.allowed_tools.length > 0 && (
                        <span className="text-2xs text-fg-muted">
                          白名单 {spec.allowed_tools.length} 工具
                        </span>
                      )}
                    </div>
                    <div className="flex gap-1">
                      <button
                        type="button"
                        onClick={() => void testServer(name, spec)}
                        disabled={busy !== null}
                        className="rounded px-2 py-0.5 text-2xs disabled:opacity-50"
                        style={{ backgroundColor: '#ececec', color: '#333333' }}
                      >
                        {busy === `test:${name}` ? '测试中…' : '🔌 测试连接'}
                      </button>
                      <button
                        type="button"
                        onClick={() => openEdit(name)}
                        className="rounded px-2 py-0.5 text-2xs"
                        style={{ backgroundColor: '#ececec', color: '#333333' }}
                      >
                        编辑
                      </button>
                      <button
                        type="button"
                        onClick={() => removeServer(name)}
                        className="rounded px-2 py-0.5 text-2xs text-accent-danger"
                        style={{ backgroundColor: '#ececec' }}
                      >
                        删除
                      </button>
                    </div>
                  </div>
                  <div className="mt-1 break-all font-mono text-2xs text-fg-muted">
                    {spec.command} {spec.args.join(' ')}
                  </div>
                  {envKeys.length > 0 && (
                    <div className="mt-1 text-2xs text-fg-muted">
                      env：{envKeys.map((k) => (
                        <code key={k} className="mr-1 font-mono">
                          {k}={(spec.env[k] ?? '').startsWith('__KEYRING_REF:') ? '🔒 keyring' : '•••'}
                        </code>
                      ))}
                    </div>
                  )}
                  {result && (
                    <div
                      className="mt-2 rounded px-2 py-1 text-2xs"
                      style={{
                        backgroundColor: result.ok ? '#eafbee' : '#fbeaea',
                        color: result.ok ? '#2e7d32' : '#ff8888',
                      }}
                    >
                      {result.ok
                        ? `✅ 握手成功，发现 ${result.tools?.length ?? 0} 个工具：${(result.tools ?? [])
                            .map((t) => t.name)
                            .join(', ')}`
                        : `✗ ${result.error}`}
                    </div>
                  )}
                </div>
              );
            })}
          </div>
        )}
      </section>

      {editing && (
        <ServerEditorModal
          initialName={editing.name}
          isNew={editing.isNew}
          spec={editing.spec}
          existingNames={names}
          onCancel={() => setEditing(null)}
          onCommit={commitEdit}
        />
      )}
    </div>
  );
}

// ---- 子组件：新增 / 编辑 modal ----------------------------------------------

const NAME_RE = /^[a-zA-Z0-9._-]+$/;

function ServerEditorModal({
  initialName,
  isNew,
  spec,
  existingNames,
  onCancel,
  onCommit,
}: {
  initialName: string;
  isNew: boolean;
  spec: McpServerSpec;
  existingNames: string[];
  onCancel: () => void;
  onCommit: (name: string, spec: McpServerSpec) => void;
}): JSX.Element {
  const [name, setName] = useState(initialName);
  const [draft, setDraft] = useState<McpServerSpec>(spec);
  const [argsText, setArgsText] = useState(spec.args.join(' '));
  const [allowedText, setAllowedText] = useState(spec.allowed_tools.join(', '));
  const [envRows, setEnvRows] = useState<{ k: string; v: string }[]>(
    Object.entries(spec.env ?? {}).map(([k, v]) => ({ k, v })),
  );

  const validName = NAME_RE.test(name) && name.length <= 64;
  const nameConflict =
    validName && name !== initialName && existingNames.includes(name);
  const env: Record<string, string> = {};
  for (const row of envRows) {
    if (row.k.trim()) env[row.k.trim()] = row.v;
  }

  const canCommit =
    validName && !nameConflict && draft.command.trim().length > 0;

  const commit = (): void => {
    if (!canCommit) return;
    onCommit(name, {
      ...draft,
      command: draft.command.trim(),
      args: argsText.trim() ? argsText.trim().split(/\s+/) : [],
      env,
      allowed_tools: allowedText
        .split(/[,，]/)
        .map((s) => s.trim())
        .filter(Boolean),
    });
  };

  return (
    <div
      className="fixed inset-0 z-[180] flex items-center justify-center"
      style={{ backgroundColor: 'rgba(0,0,0,0.5)' }}
      onClick={onCancel}
    >
      <div
        className="max-h-[85vh] w-[640px] overflow-auto rounded p-4 shadow-2xl"
        style={{ backgroundColor: '#f3f3f3', border: '1px solid #d0d0d0' }}
        onClick={(e) => e.stopPropagation()}
      >
        <h2 className="mb-3 text-ui-lg font-semibold">
          {isNew ? '新增 MCP Server' : `编辑 MCP Server「${initialName}」`}
        </h2>

        <label className="mb-3 block">
          <span className="mb-1 block text-2xs text-fg-muted">
            名称（唯一，仅字母/数字/._-）
          </span>
          <input
            value={name}
            onChange={(e) => setName(e.target.value)}
            placeholder="例如：websearch"
            className="w-full rounded px-2 py-1 font-mono text-ui outline-none"
            style={{ backgroundColor: '#ececec', color: '#1f1f1f', border: '1px solid #d4d4d4' }}
          />
          {name.trim() && !validName && (
            <span className="text-2xs" style={{ color: '#ff8888' }}>
              名称不合法
            </span>
          )}
          {nameConflict && (
            <span className="text-2xs" style={{ color: '#ff8888' }}>
              「{name}」已存在
            </span>
          )}
        </label>

        <div className="mb-3 grid grid-cols-2 gap-2">
          <label className="block">
            <span className="mb-1 block text-2xs text-fg-muted">command（可执行文件）</span>
            <input
              value={draft.command}
              onChange={(e) => setDraft({ ...draft, command: e.target.value })}
              placeholder="uvx / npx / mcp-server-database"
              className="w-full rounded px-2 py-1 font-mono text-ui outline-none"
              style={{ backgroundColor: '#ececec', color: '#1f1f1f', border: '1px solid #d4d4d4' }}
            />
          </label>
          <label className="block">
            <span className="mb-1 block text-2xs text-fg-muted">args（空格分隔）</span>
            <input
              value={argsText}
              onChange={(e) => setArgsText(e.target.value)}
              placeholder="-y duckduckgo-mcp-server"
              className="w-full rounded px-2 py-1 font-mono text-ui outline-none"
              style={{ backgroundColor: '#ececec', color: '#1f1f1f', border: '1px solid #d4d4d4' }}
            />
          </label>
        </div>

        {/* env 键值对 */}
        <div className="mb-3">
          <div className="mb-1 flex items-center justify-between">
            <span className="text-2xs text-fg-muted">
              env（敏感值写 <code className="font-mono">__KEYRING_REF:&lt;account&gt;__</code> 占位符{KEYRING_HINT}）
            </span>
            <button
              type="button"
              onClick={() => setEnvRows([...envRows, { k: '', v: '' }])}
              className="rounded px-2 py-0.5 text-2xs"
              style={{ backgroundColor: '#ececec', color: '#333333' }}
            >
              ＋ 加一行
            </button>
          </div>
          {envRows.length === 0 && (
            <div className="text-2xs text-fg-muted">（无）</div>
          )}
          {envRows.map((row, i) => (
            <div key={i} className="mb-1 flex gap-1">
              <input
                value={row.k}
                onChange={(e) =>
                  setEnvRows(envRows.map((r, idx) => (idx === i ? { ...r, k: e.target.value } : r)))
                }
                placeholder="KEY"
                className="w-[35%] rounded px-2 py-1 font-mono text-2xs outline-none"
                style={{ backgroundColor: '#ececec', color: '#1f1f1f', border: '1px solid #d4d4d4' }}
              />
              <input
                value={row.v}
                onChange={(e) =>
                  setEnvRows(envRows.map((r, idx) => (idx === i ? { ...r, v: e.target.value } : r)))
                }
                placeholder="__KEYRING_REF:mcp.xxx.key__"
                className="flex-1 rounded px-2 py-1 font-mono text-2xs outline-none"
                style={{ backgroundColor: '#ececec', color: '#1f1f1f', border: '1px solid #d4d4d4' }}
              />
              <button
                type="button"
                onClick={() => setEnvRows(envRows.filter((_, idx) => idx !== i))}
                className="rounded px-2 text-2xs text-accent-danger"
                style={{ backgroundColor: '#ececec' }}
              >
                ✕
              </button>
            </div>
          ))}
        </div>

        <div className="mb-3 grid grid-cols-2 gap-2">
          <label className="block">
            <span className="mb-1 block text-2xs text-fg-muted">allowed_tools（逗号分隔，空 = 全部）</span>
            <input
              value={allowedText}
              onChange={(e) => setAllowedText(e.target.value)}
              placeholder="search, fetch"
              className="w-full rounded px-2 py-1 font-mono text-ui outline-none"
              style={{ backgroundColor: '#ececec', color: '#1f1f1f', border: '1px solid #d4d4d4' }}
            />
          </label>
          <label className="block">
            <span className="mb-1 block text-2xs text-fg-muted">working_dir（可选）</span>
            <input
              value={draft.working_dir ?? ''}
              onChange={(e) =>
                setDraft({ ...draft, working_dir: e.target.value.trim() || null })
              }
              placeholder="留空 = 默认"
              className="w-full rounded px-2 py-1 font-mono text-ui outline-none"
              style={{ backgroundColor: '#ececec', color: '#1f1f1f', border: '1px solid #d4d4d4' }}
            />
          </label>
        </div>

        <label className="mb-4 flex items-center gap-2 text-2xs text-fg-muted">
          <input
            type="checkbox"
            checked={draft.auto_start}
            onChange={(e) => setDraft({ ...draft, auto_start: e.target.checked })}
          />
          <span>auto_start（随 Agent 启动自动拉起）</span>
        </label>

        <div className="flex justify-end gap-2">
          <button
            type="button"
            onClick={onCancel}
            className="rounded px-3 py-1.5 text-ui"
            style={{ backgroundColor: '#ececec', color: '#333333' }}
          >
            取消
          </button>
          <button
            type="button"
            disabled={!canCommit}
            onClick={commit}
            className="rounded px-3 py-1.5 text-ui font-semibold disabled:opacity-50"
            style={{ backgroundColor: '#007acc', color: '#ffffff' }}
          >
            确定
          </button>
        </div>
      </div>
    </div>
  );
}
