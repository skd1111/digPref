/**
 * EnvironmentsSettingPanel —— 多环境治理 UI。
 *
 * 功能：
 *   - 列出所有环境 + 当前 active（来自 envStore，envStore 通过 Agent /envconfig/ 拉取）
 *   - 切换 active（通过 envStore.setActive，保证 top badge / side panel 同步）
 *   - 查看 / 编辑单个环境（数据库 / API / MCP 条目）
 *   - 加密导出（passphrase → 文件下载）
 *   - 导入（上传文件 + passphrase → 解析后展示占位符清单让用户去 Keychain 绑）
 *
 * 数据流：envStore 是单一真实源；本组件只读 + 触发 envStore.setActive + envStore.refresh。
 * 不再维护本地 list/active 副本（之前会有"在 Settings 激活后顶栏不同步"的问题）。
 *
 * 安全红线：
 *   - 这里**永远不**展示明文密钥。Agent 端 scrub 之后我们只看到占位符。
 *   - 导入时返回的 placeholders 列表是给 UI 提示用（"你要在 Keychain 里绑这些账户"），
 *     不含明文。
 */
import { useEffect, useState } from 'react';
import { invoke } from '@/ipc/invoke';
import { useEnvStore } from '@/store/envStore';

const ENV_OPTIONS = [
  { value: 'dev', label: '开发 (dev)' },
  { value: 'test', label: '测试 (test)' },
  { value: 'staging', label: '准生产 (staging)' },
  { value: 'prod', label: '生产 (prod)' },
] as const;

interface DbConn {
  name: string;
  kind: string;
  host: string;
  port: number;
  database: string;
  username: string;
  password?: string;
  options?: Record<string, unknown>;
  read_only_account?: boolean;
}

interface ApiGw {
  name: string;
  base_url: string;
  api_key?: string;
  timeout_sec?: number;
  rate_limit_per_min?: number | null;
}

interface McpEntry {
  server_name: string;
  command: string;
  args: string[];
  env: Record<string, string>;
  allowed_tools: string[];
  auto_start?: boolean;
  working_dir?: string | null;
}

interface TargetServerEntry {
  name: string;
  description: string;
  host: string;
  port: number;
  protocol: string;
  username: string;
  password?: string;
  private_key_ref?: string | null;
  tags: string[];
  enabled: boolean;
}

interface EnvDetail {
  environment: string;
  label: string;
  description: string;
  databases: DbConn[];
  api_gateways: ApiGw[];
  mcp_servers: McpEntry[];
  target_servers: TargetServerEntry[];
}

export function EnvironmentsSettingPanel(): JSX.Element {
  // 单一数据源：envStore
  const list = useEnvStore((s) => s.list);
  const activeEnv = useEnvStore((s) => s.activeEnv);
  const loading = useEnvStore((s) => s.loading);
  const storeError = useEnvStore((s) => s.error);
  const refresh = useEnvStore((s) => s.refresh);
  const setActive = useEnvStore((s) => s.setActive);

  // 本地 UI state
  const [editing, setEditing] = useState<EnvDetail | null>(null);
  const [creating, setCreating] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [importing, setImporting] = useState(false);
  const [exporting, setExporting] = useState(false);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  // 错误合并展示
  const displayError = error ?? storeError;

  // 空白起一个环境 —— 弹 modal 让用户输入任意 env 名 + label
  const createEnv = async (env: string, label: string, description: string): Promise<void> => {
    try {
      // env 名做规范化：小写 + 去前后空白
      const normalized = env.trim().toLowerCase();
      const empty: EnvDetail = {
        environment: normalized,
        label,
        description,
        databases: [],
        api_gateways: [],
        mcp_servers: [],
        target_servers: [],
      };
      await invoke('envconfig_save', { env: normalized, config: empty });
      setCreating(false);
      await refresh();
    } catch (e) {
      setError(String(e));
    }
  };

  const activate = async (env: string): Promise<void> => {
    try {
      // ★ 走 envStore.setActive（不是直接调 IPC）—— 保证顶栏 / SidePanel 同步
      await setActive(env);
    } catch (e) {
      setError(String(e));
    }
  };

  const remove = async (env: string): Promise<void> => {
    if (!window.confirm(`删除环境 ${env}?（不会删除 Keychain 里的密钥）`)) return;
    try {
      await invoke('envconfig_delete', { env });
      await refresh();
    } catch (e) {
      setError(String(e));
    }
  };

  const openEditor = async (env: string): Promise<void> => {
    try {
      const d = await invoke<EnvDetail>('envconfig_get', { env });
      setEditing(d);
    } catch (e) {
      setError(String(e));
    }
  };

  const saveEditing = async (): Promise<void> => {
    if (!editing) return;
    try {
      await invoke('envconfig_save', { env: editing.environment, config: editing });
      setEditing(null);
      await refresh();
    } catch (e) {
      setError(String(e));
    }
  };

  const doExport = async (): Promise<void> => {
    if (list.length === 0) {
      setError('没有可导出的环境');
      return;
    }
    const passphrase = window.prompt('导出 passphrase（用于加密）');
    if (!passphrase) return;
    setExporting(true);
    try {
      const r = await invoke<{
        ciphertext_base64: string;
        env_count: number;
        placeholder_count: number;
        plaintext_bytes: number;
        ciphertext_bytes: number;
      }>('envconfig_export', {
        req: {
          passphrase,
          environments: list.map((e) => e.environment),
        },
      });
      // 触发下载
      const blob = new Blob(
        [Uint8Array.from(atob(r.ciphertext_base64), (c) => c.charCodeAt(0))],
        { type: 'application/octet-stream' },
      );
      const a = document.createElement('a');
      a.href = URL.createObjectURL(blob);
      a.download = `eaide-envs-${new Date().toISOString().slice(0, 10)}.eae`;
      a.click();
      URL.revokeObjectURL(a.href);
      alert(
        `已导出 ${r.env_count} 个环境（明文 ${r.plaintext_bytes} B → 密文 ${r.ciphertext_bytes} B，包含 ${r.placeholder_count} 个占位符）`,
      );
    } catch (e) {
      setError(String(e));
    } finally {
      setExporting(false);
    }
  };

  const doImport = async (): Promise<void> => {
    const input = document.createElement('input');
    input.type = 'file';
    input.accept = '.eae';
    input.onchange = async () => {
      const file = input.files?.[0];
      if (!file) return;
      const passphrase = window.prompt('导入 passphrase');
      if (!passphrase) return;
      setImporting(true);
      try {
        const buf = await file.arrayBuffer();
        const b64 = btoa(String.fromCharCode(...new Uint8Array(buf)));
        const r = await invoke<{
          env_count: number;
          placeholders: string[];
          environments: EnvDetail[];
        }>('envconfig_import', {
          req: { passphrase, ciphertext_base64: b64, plaintext_ok: false },
        });
        const placeholdersList = r.placeholders.join('\n  - ');
        alert(
          `导入成功：${r.env_count} 个环境\n\n占位符账户（请在系统 Keychain / Credential Manager 中绑定）：\n  - ${placeholdersList}`,
        );
        await refresh();
      } catch (e) {
        setError(String(e));
      } finally {
        setImporting(false);
      }
    };
    input.click();
  };

  return (
    <div className="mx-auto max-w-4xl space-y-6">
      <header className="flex items-center justify-between">
        <div>
          <h1 className="text-ui-lg font-semibold">Environments · 多环境</h1>
          <p className="mt-1 text-2xs text-fg-muted">
            dev / test / staging / prod 四套独立配置。**明文密钥永不落盘** —— 永远只写占位符，
            真正密钥经 OS Keychain 注入。
          </p>
        </div>
        <div className="flex gap-2">
          <button
            type="button"
            onClick={() => setCreating(true)}
            className="rounded px-3 py-1 text-ui font-semibold"
            style={{ backgroundColor: '#007acc', color: '#ffffff' }}
          >
            ＋ 创建环境
          </button>
          <button
            type="button"
            onClick={() => void doExport()}
            disabled={exporting || list.length === 0}
            className="rounded px-3 py-1 text-ui"
            style={{ backgroundColor: '#ececec', color: '#333333', border: '1px solid #b8b8b8' }}
          >
            {exporting ? '导出中…' : '加密导出'}
          </button>
          <button
            type="button"
            onClick={() => void doImport()}
            disabled={importing}
            className="rounded px-3 py-1 text-ui"
            style={{ backgroundColor: '#ececec', color: '#333333', border: '1px solid #b8b8b8' }}
          >
            {importing ? '导入中…' : '导入 .eae'}
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

      {displayError && (
        <div
          className="rounded border px-3 py-2 text-2xs"
          style={{ backgroundColor: '#fbeaea', borderColor: '#ff5566', color: '#ff8888' }}
        >
          {displayError}
        </div>
      )}

      {loading ? (
        <div className="text-fg-muted">加载中…</div>
      ) : list.length === 0 ? (
        <div
          className="flex flex-col items-center gap-3 rounded p-8 text-center"
          style={{ backgroundColor: '#f3f3f3', border: '1px solid #d4d4d4' }}
        >
          <div className="text-ui font-semibold text-fg">尚未注册任何环境</div>
          <div className="text-2xs text-fg-muted">
            点右上角"＋ 创建环境"开始。系统会自动建一个空环境（4 个标准：dev / test / staging / prod）。
          </div>
          <button
            type="button"
            onClick={() => setCreating(true)}
            className="rounded px-4 py-1.5 text-ui font-semibold"
            style={{ backgroundColor: '#007acc', color: '#ffffff' }}
          >
            ＋ 创建第一个环境
          </button>
        </div>
      ) : (
        <table
          className="w-full text-ui"
          style={{ backgroundColor: '#f3f3f3', border: '1px solid #d4d4d4' }}
        >
          <thead style={{ backgroundColor: '#ffffff' }}>
            <tr className="text-2xs uppercase tracking-wider text-fg-muted">
              <th className="px-3 py-2 text-left">环境</th>
              <th className="px-3 py-2 text-left">标签</th>
              <th className="px-3 py-2 text-left">描述</th>
              <th className="px-3 py-2 text-left">更新</th>
              <th className="px-3 py-2 text-right">操作</th>
            </tr>
          </thead>
          <tbody>
            {list.map((e) => (
              <tr
                key={e.environment}
                style={{ borderTop: '1px solid #d4d4d4' }}
              >
                <td className="px-3 py-2 font-mono">{e.environment}</td>
                <td className="px-3 py-2">
                  {e.label}
                  {activeEnv === e.environment && (
                    <span className="ml-2 rounded px-1.5 text-2xs" style={{ backgroundColor: '#007acc' }}>
                      active
                    </span>
                  )}
                  {!e.configured && (
                    <span
                      className="ml-2 rounded px-1.5 text-2xs"
                      style={{ backgroundColor: '#ececec', color: '#616161' }}
                      title="首次启动自动 seed 4 个预设之一，尚未编辑"
                    >
                      未配置
                    </span>
                  )}
                </td>
                <td className="px-3 py-2 text-fg-muted">{e.description}</td>
                <td className="px-3 py-2 text-2xs text-fg-muted">{e.updated_at || '—'}</td>
                <td className="px-3 py-2 text-right">
                  {activeEnv !== e.environment && (
                    <button
                      type="button"
                      onClick={() => void activate(e.environment)}
                      className="rounded px-2 py-0.5 text-2xs hover:bg-vscode-border"
                    >
                      激活
                    </button>
                  )}
                  <button
                    type="button"
                    onClick={() => void openEditor(e.environment)}
                    className="ml-2 rounded px-2 py-0.5 text-2xs hover:bg-vscode-border"
                  >
                    {e.configured ? '编辑' : '立即配置'}
                  </button>
                  <button
                    type="button"
                    onClick={() => void remove(e.environment)}
                    className="ml-2 rounded px-2 py-0.5 text-2xs text-accent-danger hover:bg-vscode-border"
                  >
                    删除
                  </button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}

      {editing && (
        <EnvEditor
          initial={editing}
          onChange={setEditing}
          onSave={saveEditing}
          onCancel={() => setEditing(null)}
        />
      )}

      {creating && (
        <CreateEnvModal
          existingEnvNames={list.map((e) => e.environment)}
          onCancel={() => setCreating(false)}
          onCreate={createEnv}
        />
      )}
    </div>
  );
}

// ---- 子组件：创建环境 modal ---------------------------------------------

// env 名服务端校验规则（与后端 Environment._ENV_PATTERN 保持一致）：
//   ^[^\W\d][\w.\-]{0,62}$   开头为字母（unicode），后跟字母/数字/. _ -
const ENV_NAME_RE = /^[^\W\d][\w.\-]{0,62}$/u;

function CreateEnvModal({
  onCancel,
  onCreate,
  existingEnvNames,
}: {
  onCancel: () => void;
  onCreate: (env: string, label: string, description: string) => Promise<void>;
  existingEnvNames: string[];
}): JSX.Element {
  const [env, setEnv] = useState<string>('');
  const [label, setLabel] = useState('');
  const [description, setDescription] = useState('');
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  const normalized = env.trim().toLowerCase();
  const validName = ENV_NAME_RE.test(normalized);
  const alreadyExists = existingEnvNames.includes(normalized);

  return (
    <div
      className="fixed inset-0 z-[180] flex items-center justify-center"
      style={{ backgroundColor: 'rgba(0,0,0,0.5)' }}
      onClick={onCancel}
    >
      <div
        className="w-[480px] rounded p-4 shadow-2xl"
        style={{ backgroundColor: '#f3f3f3', border: '1px solid #d0d0d0' }}
        onClick={(e) => e.stopPropagation()}
      >
        <h2 className="mb-3 text-ui-lg font-semibold">创建新环境</h2>

        <label className="mb-1 block">
          <span className="mb-1 block text-2xs text-fg-muted">环境名（唯一 key，只允许小写字母/数字/点/下划线/中划线）</span>
          <input
            value={env}
            onChange={(e) => setEnv(e.target.value)}
            placeholder="例如：华东-dev / 生产-2026q3"
            className="w-full rounded px-2 py-1 font-mono text-ui outline-none"
            style={{ backgroundColor: '#ececec', color: '#1f1f1f', border: '1px solid #d4d4d4' }}
          />
        </label>
        {env.trim() && !validName && (
          <div className="mb-2 text-2xs" style={{ color: '#ff8888' }}>
            名称不合法：小写字母开头，可含 a-z / 0-9 / . _ -
          </div>
        )}
        {validName && alreadyExists && (
          <div className="mb-2 text-2xs" style={{ color: '#ff8888' }}>
            「{normalized}」已存在；换个名字或去列表里点「编辑」
          </div>
        )}

        {/* 已有 / 推荐的 env 名，让用户能一键填入而不必手敲 */}
        {(existingEnvNames.length > 0 || ENV_OPTIONS.length > 0) && (
          <div className="mb-3">
            <span className="mb-1 block text-2xs text-fg-muted">
              {existingEnvNames.length > 0 ? '已存在（点击填入，可重命名）' : '推荐起始名（首次启动 seed）'}
            </span>
            <div className="flex flex-wrap gap-1">
              {Array.from(new Set([...ENV_OPTIONS.map((o) => o.value), ...existingEnvNames])).map((name) => (
                <button
                  key={name}
                  type="button"
                  onClick={() => setEnv(name)}
                  className="rounded px-2 py-0.5 font-mono text-2xs hover:bg-vscode-border"
                  style={{
                    backgroundColor: '#ececec',
                    color: existingEnvNames.includes(name) ? '#616161' : '#333333',
                    border: '1px solid #d4d4d4',
                  }}
                >
                  {name}
                  {existingEnvNames.includes(name) && (
                    <span className="ml-1" style={{ color: '#007acc' }}>●</span>
                  )}
                </button>
              ))}
            </div>
          </div>
        )}

        <label className="mb-3 block">
          <span className="mb-1 block text-2xs text-fg-muted">标签（人类可读，可任意文案）</span>
          <input
            value={label}
            onChange={(e) => setLabel(e.target.value)}
            placeholder="例如：生产环境"
            className="w-full rounded px-2 py-1 text-ui outline-none"
            style={{ backgroundColor: '#ececec', color: '#1f1f1f', border: '1px solid #d4d4d4' }}
          />
        </label>

        <label className="mb-3 block">
          <span className="mb-1 block text-2xs text-fg-muted">描述（可选）</span>
          <input
            value={description}
            onChange={(e) => setDescription(e.target.value)}
            placeholder="例如：线上生产"
            className="w-full rounded px-2 py-1 text-ui outline-none"
            style={{ backgroundColor: '#ececec', color: '#1f1f1f', border: '1px solid #d4d4d4' }}
          />
        </label>

        {err && (
          <div className="mb-3 rounded px-2 py-1 text-2xs" style={{ color: '#ff8888', backgroundColor: '#fbeaea' }}>
            {err}
          </div>
        )}

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
            disabled={!validName || alreadyExists || busy || !label.trim()}
            onClick={() => {
              if (!validName || alreadyExists || !label.trim()) return;
              setBusy(true);
              setErr(null);
              onCreate(normalized, label.trim(), description.trim())
                .catch((e) => setErr(String(e)))
                .finally(() => setBusy(false));
            }}
            className="rounded px-3 py-1.5 text-ui font-semibold disabled:opacity-50"
            style={{ backgroundColor: '#007acc', color: '#ffffff' }}
          >
            {busy ? '创建中…' : '创建'}
          </button>
        </div>
      </div>
    </div>
  );
}

// ---- 子组件：单个环境的编辑器 ---------------------------------------------

function EnvEditor({
  initial,
  onChange,
  onSave,
  onCancel,
}: {
  initial: EnvDetail;
  onChange: (d: EnvDetail) => void;
  onSave: () => void;
  onCancel: () => void;
}): JSX.Element {
  const [draft, setDraft] = useState<EnvDetail>(initial);

  const update = (patch: Partial<EnvDetail>): void => {
    const next = { ...draft, ...patch };
    setDraft(next);
    onChange(next);
  };

  const updateDb = (i: number, patch: Partial<DbConn>): void => {
    const next = draft.databases.map((d, idx) => (idx === i ? { ...d, ...patch } : d));
    update({ databases: next });
  };
  const addDb = (): void =>
    update({
      databases: [
        ...draft.databases,
        { name: 'new.db', kind: 'postgres', host: 'localhost', port: 5432, database: 'x', username: 'u' },
      ],
    });
  const delDb = (i: number): void =>
    update({ databases: draft.databases.filter((_, idx) => idx !== i) });

  return (
    <div
      className="fixed inset-0 z-[180] flex items-center justify-center"
      style={{ backgroundColor: 'rgba(0,0,0,0.5)' }}
      onClick={onCancel}
    >
      <div
        className="max-h-[85vh] w-[760px] overflow-auto rounded p-4 shadow-2xl"
        style={{ backgroundColor: '#f3f3f3', border: '1px solid #d0d0d0' }}
        onClick={(e) => e.stopPropagation()}
      >
        <h2 className="mb-3 text-ui-lg font-semibold">
          编辑环境 <code className="ml-1 font-mono text-2xs">{draft.environment}</code>
        </h2>

        <div className="mb-3 grid grid-cols-2 gap-3">
          <Field
            label="Label"
            value={draft.label}
            onChange={(v) => update({ label: v })}
          />
          <Field
            label="Description"
            value={draft.description}
            onChange={(v) => update({ description: v })}
          />
        </div>

        <section className="mb-4">
          <header className="mb-2 flex items-center justify-between">
            <h3 className="text-2xs font-semibold uppercase tracking-wider text-fg-muted">
              Databases
            </h3>
            <button
              type="button"
              onClick={addDb}
              className="rounded px-2 py-0.5 text-2xs"
              style={{ backgroundColor: '#ececec', color: '#333333' }}
            >
              ＋ 新增
            </button>
          </header>
          {draft.databases.map((d, i) => (
            <div
              key={i}
              className="mb-2 rounded p-2"
              style={{ backgroundColor: '#ffffff', border: '1px solid #d4d4d4' }}
            >
              <div className="mb-1 grid grid-cols-3 gap-2">
                <Field label="name" value={d.name} onChange={(v) => updateDb(i, { name: v })} />
                <Field label="kind" value={d.kind} onChange={(v) => updateDb(i, { kind: v })} />
                <Field label="read_only" value={String(d.read_only_account ?? true)} onChange={(v) => updateDb(i, { read_only_account: v === 'true' })} />
              </div>
              <div className="mb-1 grid grid-cols-3 gap-2">
                <Field label="host" value={d.host} onChange={(v) => updateDb(i, { host: v })} />
                <Field label="port" value={String(d.port)} onChange={(v) => updateDb(i, { port: Number(v) || 5432 })} />
                <Field label="database" value={d.database} onChange={(v) => updateDb(i, { database: v })} />
              </div>
              <div className="grid grid-cols-2 gap-2">
                <Field label="username" value={d.username} onChange={(v) => updateDb(i, { username: v })} />
                <Field label="password (Keyring)" value={d.password ?? ''} onChange={(v) => updateDb(i, { password: v })} placeholder="__KEYRING_REF:..." />
              </div>
              <button
                type="button"
                onClick={() => delDb(i)}
                className="mt-1 rounded px-2 py-0.5 text-2xs text-accent-danger"
              >
                删除
              </button>
            </div>
          ))}
        </section>

        <section className="mb-4">
          <header className="mb-2 flex items-center justify-between">
            <h3 className="text-2xs font-semibold uppercase tracking-wider text-fg-muted">
              Target Servers (SSH / RDP / DB-on-host)
            </h3>
            <button
              type="button"
              onClick={() =>
                update({
                  target_servers: [
                    ...draft.target_servers,
                    {
                      name: 'new.host',
                      description: '',
                      host: '',
                      port: 22,
                      protocol: 'ssh',
                      username: 'root',
                      password: '',
                      private_key_ref: null,
                      tags: [],
                      enabled: true,
                    },
                  ],
                })
              }
              className="rounded px-2 py-0.5 text-2xs"
              style={{ backgroundColor: '#ececec', color: '#333333' }}
            >
              ＋ 新增
            </button>
          </header>
          {draft.target_servers.map((t, i) => (
            <div
              key={i}
              className="mb-2 rounded p-2"
              style={{ backgroundColor: '#ffffff', border: '1px solid #d4d4d4' }}
            >
              <div className="mb-1 grid grid-cols-3 gap-2">
                <Field
                  label="name (e.g. web.prod.01)"
                  value={t.name}
                  onChange={(v) =>
                    update({
                      target_servers: draft.target_servers.map((x, idx) =>
                        idx === i ? { ...x, name: v } : x,
                      ),
                    })
                  }
                />
                <Field
                  label="protocol"
                  value={t.protocol}
                  onChange={(v) =>
                    update({
                      target_servers: draft.target_servers.map((x, idx) =>
                        idx === i ? { ...x, protocol: v } : x,
                      ),
                    })
                  }
                />
                <Field
                  label="port"
                  value={String(t.port)}
                  onChange={(v) =>
                    update({
                      target_servers: draft.target_servers.map((x, idx) =>
                        idx === i ? { ...x, port: Number(v) || 22 } : x,
                      ),
                    })
                  }
                />
              </div>
              <div className="mb-1 grid grid-cols-3 gap-2">
                <Field
                  label="host / IP"
                  value={t.host}
                  onChange={(v) =>
                    update({
                      target_servers: draft.target_servers.map((x, idx) =>
                        idx === i ? { ...x, host: v } : x,
                      ),
                    })
                  }
                />
                <Field
                  label="username"
                  value={t.username}
                  onChange={(v) =>
                    update({
                      target_servers: draft.target_servers.map((x, idx) =>
                        idx === i ? { ...x, username: v } : x,
                      ),
                    })
                  }
                />
                <Field
                  label="password (Keyring)"
                  value={t.password ?? ''}
                  placeholder="__KEYRING_REF:target_servers.x.password__"
                  onChange={(v) =>
                    update({
                      target_servers: draft.target_servers.map((x, idx) =>
                        idx === i ? { ...x, password: v } : x,
                      ),
                    })
                  }
                />
              </div>
              <Field
                label="description"
                value={t.description}
                onChange={(v) =>
                  update({
                    target_servers: draft.target_servers.map((x, idx) =>
                      idx === i ? { ...x, description: v } : x,
                    ),
                  })
                }
              />
              <div className="mt-1 flex items-center justify-between">
                <label className="flex items-center gap-1 text-2xs text-fg-muted">
                  <input
                    type="checkbox"
                    checked={t.enabled}
                    onChange={(e) =>
                      update({
                        target_servers: draft.target_servers.map((x, idx) =>
                          idx === i ? { ...x, enabled: e.target.checked } : x,
                        ),
                      })
                    }
                  />
                  <span>enabled</span>
                </label>
                <button
                  type="button"
                  onClick={() =>
                    update({
                      target_servers: draft.target_servers.filter((_, idx) => idx !== i),
                    })
                  }
                  className="rounded px-2 py-0.5 text-2xs text-accent-danger"
                >
                  删除
                </button>
              </div>
            </div>
          ))}
          {draft.target_servers.length === 0 && (
            <div className="text-2xs text-fg-muted">
              尚无目标服务器。点 ＋ 新增 加一台，填 IP/端口/密码（密码走 Keyring 占位符）。
            </div>
          )}
        </section>

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
            onClick={onSave}
            className="rounded px-3 py-1.5 text-ui font-semibold"
            style={{ backgroundColor: '#007acc', color: '#ffffff' }}
          >
            保存
          </button>
        </div>
      </div>
    </div>
  );
}

function Field({
  label,
  value,
  onChange,
  placeholder,
}: {
  label: string;
  value: string;
  onChange: (v: string) => void;
  placeholder?: string;
}): JSX.Element {
  return (
    <label className="block">
      <span className="mb-0.5 block text-2xs text-fg-muted">{label}</span>
      <input
        value={value}
        onChange={(e) => onChange(e.target.value)}
        placeholder={placeholder}
        className="w-full rounded px-2 py-1 text-ui outline-none"
        style={{ backgroundColor: '#ececec', color: '#1f1f1f', border: '1px solid #d4d4d4' }}
      />
    </label>
  );
}
