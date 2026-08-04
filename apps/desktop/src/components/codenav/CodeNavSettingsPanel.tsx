/**
 * CodeNavSettingsPanel —— Settings → 代码导航 Tab。
 *
 * V0 提供：
 *  - 当前索引状态（总文件 / 总符号 / 扫描时间）
 *  - 白名单允许的根路径（只读展示 + 环境变量提示）
 *  - 用户导入区：路径输入框（目录或单文件路径，**在白名单内**才会被接受）
 *  - 「索引选中路径」按钮（调 /codenav/index 加 addRoots/files）
 *  - 「重新全量扫描」按钮（调 /codenav/index 默认行为）
 *  - LLM 配置状态 + 重读配置按钮
 *
 * 错误展示：白名单违规 / 路径不存在 → 显示后端 400/403/404 detail。
 */
import { useEffect, useState } from 'react';

import { ipc } from '@/ipc/invoke';

interface IndexStatus {
  total_files: number;
  total_symbols: number;
  last_full_scan: number | null;
  last_incremental: number | null;
  is_scanning: boolean;
}

interface BackendCandidate {
  name: string;
  type: string;
  base_url: string;
  model: string;
  enabled: boolean;
}

interface LlmBackendInfo {
  bound: string | null;
  resolved: {
    name: string;
    type: string;
    base_url: string;
    model: string;
    has_api_key: boolean;
    source: 'router_db_bound' | 'router_db_default' | 'env';
  } | null;
  candidates: BackendCandidate[];
}

interface AllowedRootsResp {
  roots: string[];
  extra_env: string;
}

function formatTs(ts: number | null): string {
  if (!ts) return '—';
  return new Date(ts * 1000).toLocaleString();
}

export function CodeNavSettingsPanel(): JSX.Element {
  const [status, setStatus] = useState<IndexStatus | null>(null);
  const [backend, setBackend] = useState<LlmBackendInfo | null>(null);
  const [allowed, setAllowed] = useState<AllowedRootsResp | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [info, setInfo] = useState<string | null>(null);

  const refresh = async () => {
    setError(null);
    try {
      const [s, b, a] = await Promise.all([
        ipc.codeNavStatus(),
        ipc.codeNavLlmBackend(),
        ipc.codeNavAllowedRoots(),
      ]);
      setStatus(s);
      // 防御：Agent 未就绪时字段可能缺失
      if (b && !Array.isArray(b.candidates)) b.candidates = [];
      setBackend(b);
      if (a && !Array.isArray(a.roots)) a.roots = [];
      setAllowed(a);
    } catch (e) {
      setError(String(e));
    }
  };

  useEffect(() => {
    refresh();
  }, []);

  const onBindBackend = async (name: string | null) => {
    setBusy(true);
    setError(null);
    setInfo(null);
    try {
      const b = await ipc.codeNavLlmBackendBind(name);
      setBackend(b);
      setInfo(
        name
          ? `✅ 代码导航已绑定到 "${name}"`
          : '✅ 已解绑（将走环境变量或 mock）',
      );
    } catch (e) {
      setError(`绑定失败：${String(e)}`);
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="space-y-6 p-4">
      <header>
        <h2 className="text-lg font-semibold text-fg">🔍 代码导航</h2>
        <p className="text-xs text-fg-muted mt-1">
          Tree-sitter AST 索引 + SQLite 符号库 + AI 跳转（Ctrl+F12）+ AI 解释（Ctrl+K）
        </p>
      </header>

      {/* 索引状态 */}
      <section className="rounded border border-border bg-bg-2 p-3">
        <div className="flex items-center justify-between mb-2">
          <h3 className="text-sm font-semibold text-fg">索引状态</h3>
          <button
            type="button"
            onClick={refresh}
            className="text-xs px-2 py-1 rounded bg-bg-3 hover:bg-bg-active text-fg"
          >
            ⟳ 刷新
          </button>
        </div>
        {status ? (
          <dl className="grid grid-cols-2 gap-x-4 gap-y-1 text-xs text-fg">
            <dt className="text-fg-muted">总文件</dt>
            <dd>{status.total_files}</dd>
            <dt className="text-fg-muted">总符号</dt>
            <dd>{status.total_symbols}</dd>
            <dt className="text-fg-muted">上次全量扫描</dt>
            <dd>{formatTs(status.last_full_scan)}</dd>
            <dt className="text-fg-muted">上次增量</dt>
            <dd>{formatTs(status.last_incremental)}</dd>
            <dt className="text-fg-muted">扫描中</dt>
            <dd>{status.is_scanning ? '是' : '否'}</dd>
          </dl>
        ) : (
          <p className="text-xs text-fg-muted">加载中…</p>
        )}
      </section>

      {/* 导入指引：不再提供手动路径输入框，统一走顶部 File 菜单 */}
      <section className="rounded border border-border bg-bg-2 p-3">
        <h3 className="text-sm font-semibold text-fg mb-2">导入文件 / 目录</h3>
        <p className="text-xs text-fg-muted">
          在顶部菜单 <span className="font-mono text-fg">File → Open File…</span> 打开单个文件，
          或 <span className="font-mono text-fg">File → Open Folder…</span> 导入整个项目（自动建索引）。
          导入后左侧文件树可点击展开。
        </p>
        {allowed && Array.isArray(allowed.roots) && (
          <details className="text-xs text-fg-muted mt-2">
            <summary className="cursor-pointer">允许的根目录（{allowed?.roots?.length ?? 0}）</summary>
            <ul className="mt-1 ml-4 list-disc">
              {allowed.roots.map((r) => (
                <li key={r} className="font-mono">{r}</li>
              ))}
            </ul>
            <p className="mt-1 text-[10px]">
              额外白名单通过环境变量 <code>{allowed.extra_env}</code> 设置（分号分隔绝对路径）。
            </p>
          </details>
        )}
      </section>

      {/* LLM backend 选择器（从模型管理 candidates 挑） */}
      <section className="rounded border border-border bg-bg-2 p-3">
        <h3 className="text-sm font-semibold text-fg mb-2">AI 模型（代码导航推断 / 解释用）</h3>
        <p className="text-xs text-fg-muted mb-2">
          两个入口都可以绑定：① 上一行「✓ 代码导航」单选按钮（最方便），
          ② 或在本面板下方下拉框选。base_url / model / API_key 都在「🗄 模型管理」配，
          api_key 走 Windows Credential Manager（系统 keychain），不落 SQLite。
        </p>
        {backend ? (
          <>
            <div className="flex items-center gap-2 mb-2">
              <label className="text-xs text-fg-muted">默认 backend：</label>
              <select
                value={backend.bound ?? '__none__'}
                onChange={(e) => onBindBackend(e.target.value === '__none__' ? null : e.target.value)}
                disabled={busy}
                className="flex-1 rounded bg-bg border border-border px-2 py-1 text-xs text-fg"
              >
                <option value="__none__">（未绑定 — 走环境变量 / mock）</option>
                {(backend.candidates ?? []).filter((c) => c.enabled).map((c) => (
                  <option key={c.name} value={c.name}>
                    {c.name}  ({c.type} / {c.model})
                  </option>
                ))}
              </select>
              <button
                type="button"
                onClick={refresh}
                className="text-xs px-2 py-1 rounded bg-bg-3 hover:bg-bg-active text-fg"
              >
                ⟳ 刷新
              </button>
            </div>

            {/* 当前生效 */}
            {backend.resolved ? (
              <dl className="grid grid-cols-2 gap-x-4 gap-y-1 text-xs text-fg mt-2 pt-2 border-t border-border">
                <dt className="text-fg-muted">实际生效</dt>
                <dd>
                  <span className="font-mono">{backend.resolved.name}</span>{' '}
                  <span className="text-fg-muted">({backend.resolved.source})</span>
                </dd>
                <dt className="text-fg-muted">Base URL</dt>
                <dd className="font-mono break-all">{backend.resolved.base_url}</dd>
                <dt className="text-fg-muted">Model</dt>
                <dd className="font-mono">{backend.resolved.model}</dd>
                <dt className="text-fg-muted">API Key</dt>
                <dd>{backend.resolved.has_api_key ? '✓ 已从 keyring 取到' : '✗ 未设置（mock 兜底）'}</dd>
              </dl>
            ) : (
              <p className="text-xs text-yellow-400 mt-2">
                ⚠ 当前没有可用的 backend（既未绑定、也无环境变量）。跳转会走 mock。
              </p>
            )}

            {(backend.candidates ?? []).length === 0 && (
              <p className="text-xs mt-2" style={{ color: '#795e26' }}>
                ⚠ 「🗄 模型管理」里还没有模型。先去那里点「＋ 新增模型」保存，再回来这里点「✓ 代码导航」绑定。
              </p>
            )}
          </>
        ) : (
          <p className="text-xs text-fg-muted">加载中…</p>
        )}
      </section>

      {/* 状态消息 */}
      {error && (
        <div className="rounded border border-red-500 bg-red-900/20 px-3 py-2 text-xs text-red-300">
          {error}
        </div>
      )}
      {info && (
        <div className="rounded border border-green-500 bg-green-900/20 px-3 py-2 text-xs text-green-300">
          {info}
        </div>
      )}
    </div>
  );
}
