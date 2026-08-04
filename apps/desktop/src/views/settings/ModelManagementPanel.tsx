/**
 * ModelManagementPanel —— 设置内模型管理。
 *
 * 智能路由的**多模型注册表**（router.db.llm_backends）。
 * 喂给 RouterEngine 做五维评估调度（design/phase-2c-smart-router.md §5A）。
 * 数据真源：router.db，通过 /router/backends CRUD 端点持久化。
 */
import { useEffect, useState } from 'react';
import { DSparkConfigSection } from './DSparkConfigSection';

type BackendType = 'local' | 'private' | 'cloud';
type Residency = 'local' | 'private' | 'cloud';
type CircuitState = 'closed' | 'half_open' | 'open';

/** 上下文大小（tokens）：preset + custom 二选一 */
interface ContextSize {
  /** 预设档位；'custom' 时 tokens 为用户输入值 */
  preset: '8K' | '32K' | '128K' | '200K' | '400K' | '1M' | 'custom';
  /** 自定义值（preset='custom' 时生效） */
  tokens: number;
}

/** 预设档位对应的 token 数 */
const CTX_PRESETS: Record<Exclude<ContextSize['preset'], 'custom'>, number> = {
  '8K': 8192,
  '32K': 32768,
  '128K': 131072,
  '200K': 200000,
  '400K': 400000,
  '1M': 1000000,
};

/** 把数字格式化成友好字符串（>= 1000 → '200K'，否则 '32K' / '8000'） */
function formatContextTokens(n: number): string {
  if (n >= 1_000_000 && n % 1_000_000 === 0) return `${n / 1_000_000}M`;
  if (n >= 1000 && n % 1000 === 0) return `${n / 1000}K`;
  return String(n);
}

/** 把已存的 tokens 反推成 preset；找不到则视为 custom */
function tokensToPreset(n: number): ContextSize {
  for (const [k, v] of Object.entries(CTX_PRESETS) as Array<[keyof typeof CTX_PRESETS, number]>) {
    if (v === n) return { preset: k, tokens: n };
  }
  return { preset: 'custom', tokens: n };
}

interface Backend {
  name: string;
  type: BackendType;
  baseUrl: string;
  model: string;
  residency: Residency;
  costPer1k: number;
  timeout: number;
  capabilities: string[];
  circuit: CircuitState;
  enabled: boolean;
  /** 上下文窗口大小（tokens）。智能体调用模型时按此截断 history */
  maxContext: number;
  /** Keyring 占位符引用（null = 还没配 api key）。编辑时按它从 Windows Credential Manager 读真值 */
  apiKeyRef: string | null;
  /** 角色（utility / reasoning / execution），持久化时原样回传避免被覆盖 */
  role: 'utility' | 'reasoning' | 'execution';
}

const TYPE_BADGE: Record<BackendType, { label: string; color: string }> = {
  local: { label: 'LOCAL', color: '#059669' },
  private: { label: 'PRIVATE', color: '#0451a5' },
  cloud: { label: 'CLOUD', color: '#c586c0' },
};

const CB_PILL: Record<CircuitState, { label: string; color: string }> = {
  closed: { label: '● 正常', color: '#059669' },
  half_open: { label: '◐ 半开', color: '#795e26' },
  open: { label: '● 熔断', color: '#cd3131' },
};

export function ModelManagementPanel(): JSX.Element {
  const [backends, setBackends] = useState<Backend[]>([]);

  const [editing, setEditing] = useState<Backend | null>(null);
  const [creating, setCreating] = useState(false);
  const [toast, setToast] = useState<string | null>(null);
  const [loadingBackends, setLoadingBackends] = useState(true);
  void loadingBackends; // 暂未在 UI 展示，预留给后续 loading 骨架屏
  /** 正在探测的模型名（按钮 disabled + spinner 用） */
  const [testing, setTesting] = useState<string | null>(null);

  /** 启动时从 router.db 拉真实模型列表（持久化真源）。
   * 修复启动竞态：先 agentWaitReady（最多 15s），再 retry 一次 routerListBackends。
   * Agent 启动慢于 EAIDE 是常见情况——不能直接放弃。 */
  useEffect(() => {
    void (async () => {
      const { ipc } = await import('@/ipc/invoke');
      // 第一步：等 Agent /health 返回 2xx
      try {
        const ready = await ipc.agentWaitReady(15);
        if (!ready.ready) {
          setBackends([]);
          setLoadingBackends(false);
          flash(`⚠ Agent 未就绪（${ready.error ?? 'timeout'}）· 请检查 eaide.log 或重启 EAIDE`, 'err');
          return;
        }
      } catch (e) {
        setBackends([]);
        setLoadingBackends(false);
        flash(`⚠ agentWaitReady 失败 · ${String(e)}`, 'err');
        return;
      }
      // 第二步：拉列表（retry 2 次处理偶发超时）
      let list: Backend[] = [];
      let lastErr: unknown = null;
      for (let i = 0; i < 3; i++) {
        try {
          const r = await ipc.routerListBackends();
          list = (r.backends ?? []).map((b) => ({
            name: b.name,
            type: b.type as BackendType,
            baseUrl: b.base_url,
            model: b.model_name,
            residency: (b.data_residency as Residency) ?? 'local',
            costPer1k: b.cost_per_1k_tokens ?? 0,
            timeout: b.timeout_seconds ?? 30,
            capabilities: b.capabilities ?? [],
            circuit: 'closed',
            enabled: b.enabled,
            maxContext: b.max_context ?? 32768,
            apiKeyRef: b.api_key_ref ?? null,
            role: (b.role ?? 'execution') as 'utility' | 'reasoning' | 'execution',
          }));
          lastErr = null;
          break;
        } catch (e) {
          lastErr = e;
          await new Promise((r) => setTimeout(r, 800));
        }
      }
      if (lastErr) {
        setBackends([]);
        flash(`⚠ 模型列表加载失败（重试 3 次）· ${String(lastErr)}`, 'err');
      } else {
        setBackends(list);
        if (list.length === 0) {
          flash('ℹ router.db 暂无模型，点「＋ 新增模型」开始配置');
        }
      }
      setLoadingBackends(false);
    })();
  }, []);

  const flash = (msg: string, kind: 'ok' | 'err' = 'ok'): void => {
    setToast(JSON.stringify({ msg, kind }));
    window.setTimeout(() => setToast(null), 4000);
  };

  /** 启用/停用（同类型互斥）：立即持久化；启用时同类型其它模型自动停用。 */
  const toggleEnabled = async (name: string): Promise<void> => {
    const target = backends.find((b) => b.name === name);
    if (!target) return;
    const nextEnabled = !target.enabled;
    const prev = backends;
    // 乐观更新：启用时本地同步停用同类型其它项
    setBackends((bs) =>
      bs.map((b) => {
        if (b.name === name) return { ...b, enabled: nextEnabled };
        if (nextEnabled && b.type === target.type) return { ...b, enabled: false };
        return b;
      }),
    );
    try {
      const { ipc } = await import('@/ipc/invoke');
      const payload: Record<string, unknown> = {
        name: target.name,
        type: target.type,
        base_url: target.baseUrl,
        model_name: target.model,
        api_key_ref: target.apiKeyRef,
        capabilities: target.capabilities,
        max_context: target.maxContext,
        cost_per_1k_tokens: target.costPer1k,
        timeout_seconds: target.timeout,
        data_residency: target.residency,
        enabled: nextEnabled,
        role: target.role,
      };
      const r = await ipc.routerUpsertBackend(payload);
      if (!r.ok) {
        setBackends(prev);
        flash(`⚠ 「${target.name}」启用状态保存失败`, 'err');
        return;
      }
      const disabled = r.disabled ?? [];
      if (nextEnabled && disabled.length > 0) {
        flash(`✓ 已启用「${target.name}」 · 同类型「${disabled.join('、')}」已自动停用`);
      } else if (nextEnabled) {
        flash(`✓ 已启用「${target.name}」`);
      } else {
        flash(`✓ 已停用「${target.name}」`);
      }
    } catch (e) {
      setBackends(prev);
      flash(`⚠ 「${target.name}」启用状态保存失败 · ${String(e)}`, 'err');
    }
  };

  /** 当前绑到代码导航的 backend 名（用于「用于代码导航」单选） */
  const [codenavBound, setCodenavBound] = useState<string | null>(null);
  useEffect(() => {
    void (async () => {
      try {
        const { ipc } = await import('@/ipc/invoke');
        const r = await ipc.codeNavLlmBackend();
        setCodenavBound(r.bound ?? null);
      } catch {
        // ignore
      }
    })();
  }, []);

  /** 切换某 backend 作为代码导航默认 —— 同时记录单选状态 */
  const useForCodenav = async (name: string): Promise<void> => {
    const prev = codenavBound;
    setCodenavBound(name); // 乐观
    try {
      const { ipc } = await import('@/ipc/invoke');
      await ipc.codeNavLlmBackendBind(name);
      flash(`✓ 已绑定「${name}」为代码导航默认 backend`, 'ok');
    } catch (e) {
      setCodenavBound(prev); // 回滚
      flash(`✗ 绑定代码导航失败 · ${String(e)}`, 'err');
    }
  };

  /** 解除绑定（点「✕」按钮） */
  const unbindCodenav = async (): Promise<void> => {
    const prev = codenavBound;
    setCodenavBound(null);
    try {
      const { ipc } = await import('@/ipc/invoke');
      await ipc.codeNavLlmBackendBind(null);
      flash('✓ 已解绑代码导航默认 backend', 'ok');
    } catch (e) {
      setCodenavBound(prev);
      flash(`✗ 解绑失败 · ${String(e)}`, 'err');
    }
  };

  /** 真实测试连接：通过 Tauri → FastAPI → 后端 HTTP 探测。 */
  const testConn = async (b: Backend): Promise<void> => {
    setTesting(b.name);
    try {
      const { ipc } = await import('@/ipc/invoke');
      // api_key_ref 列直接存明文 key（配置文件模式，不走凭据管理器）
      const apiKey = b.apiKeyRef ?? '';
      const r = await ipc.routerTestConnection({
        type: b.type,
        base_url: b.baseUrl,
        model: b.model,
        ...(apiKey ? { api_key: apiKey } : {}),
        timeout_s: b.timeout,
      });
      if (r.ok) {
        const latency = r.latency_ms != null ? `${r.latency_ms}ms` : '';
        const extra =
          r.actual_model && r.actual_model !== b.model
            ? ` · 实际模型 ${r.actual_model}`
            : r.models
              ? ` · ${r.models.length} 个模型已下载`
              : '';
        flash(`✓ 「${b.name}」可达 · ${latency}${extra}`, 'ok');
      } else {
        flash(`✗ 「${b.name}」不可达 · ${r.error ?? '未知错误'}`, 'err');
      }
    } catch (e) {
      flash(`✗ 「${b.name}」探测失败 · ${String(e)}`, 'err');
    } finally {
      setTesting(null);
    }
  };

  /** 显示 Agent 版本指纹 — 诊断 404 / 老代码问题。 */
  const showAgentVersion = async (): Promise<void> => {
    try {
      const { ipc } = await import('@/ipc/invoke');
      const r = await ipc.agentGetVersion();
      if (r.ok && r.version) {
        const v = r.version;
        flash(
          `✓ Agent v${v.pid}.${v.uptime_s} · ${v.endpoints.length} endpoints`,
          'ok',
        );
        // 同时写到 console 方便复制
        // eslint-disable-next-line no-console
        console.info('[Agent Version]', v);
      } else {
        flash(`⚠ Agent 版本取不到 · HTTP ${r.status ?? '?'} · ${r.error ?? ''}`, 'err');
      }
    } catch (e) {
      flash(`⚠ agentGetVersion 失败 · ${String(e)}`, 'err');
    }
  };

  /** 手动重启 Agent：杀 :8765 占用者，下一次请求会自动 spawn 新的。 */
  const restartAgent = async (): Promise<void> => {
    if (!window.confirm('杀掉当前 :8765 占用者，下次 EAIDE 调用会触发 spawn 新的 Agent。继续？')) {
      return;
    }
    try {
      const { ipc } = await import('@/ipc/invoke');
      const r = await ipc.agentRestartNow();
      if (r.ok && r.port_freed) {
        flash('✓ 已杀掉 :8765 占用者，下次请求会触发新 Agent 启动', 'ok');
      } else {
        flash('⚠ 已发重启请求，但端口尚未释放（等待 3s 后重试）', 'err');
      }
    } catch (e) {
      flash(`⚠ 重启 Agent 失败 · ${String(e)}`, 'err');
    }
  };

  /** 显示 eaide.log 末尾 80 行 —— 一键诊断 Agent 启动失败原因。 */
  const showAgentLog = async (): Promise<void> => {
    try {
      const { ipc } = await import('@/ipc/invoke');
      const r = await ipc.agentReadLog(80);
      // 直接 console + 简化提示。详细 log 让用户在 devtools 里复制。
      // eslint-disable-next-line no-console
      console.info('[eaide.log tail @ ' + r.path + ']\n' + r.tail);
      const lastErr = r.tail
        .split('\n')
        .reverse()
        .find((l) => l.includes('[agent_manager]') || l.includes('[agent:')) ?? '';
      flash(
        lastErr
          ? `📋 日志末条: ${lastErr.slice(0, 120)}（完整见 devtools）`
          : `📋 日志 ${r.line_count} 行（完整见 devtools console）`,
        lastErr.includes('fail') || lastErr.includes('失败') || lastErr.includes('未找到') ? 'err' : 'ok',
      );
    } catch (e) {
      flash(`⚠ 读日志失败 · ${String(e)}`, 'err');
    }
  };

  const saveBackend = async (b: Backend): Promise<void> => {
    // 注意：API Key 已在 modal.submit() 里写到 Keyring，b.apiKeyRef 已被 modal 更新好
    // 第一步：本地 state 乐观更新
    setBackends((bs) => {
      const exists = bs.some((x) => x.name === b.name);
      return exists ? bs.map((x) => (x.name === b.name ? b : x)) : [...bs, b];
    });
    setEditing(null);
    setCreating(false);

    // 第二步：真实持久化到 router.db
    try {
      const { ipc } = await import('@/ipc/invoke');
      const payload: Record<string, unknown> = {
        name: b.name,
        type: b.type,
        base_url: b.baseUrl,
        model_name: b.model,
        api_key_ref: b.apiKeyRef, // 只存占位符引用，明文不入 SQLite
        capabilities: b.capabilities,
        max_context: b.maxContext,
        cost_per_1k_tokens: b.costPer1k,
        timeout_seconds: b.timeout,
        data_residency: b.residency,
        enabled: b.enabled,
        role: b.role,
      };
      const r = await ipc.routerUpsertBackend(payload);
      if (!r.ok) {
        flash(`⚠ 「${b.name}」模型已存到本地，但后端返回失败`, 'err');
      } else {
        const disabled = r.disabled ?? [];
        if (b.enabled && disabled.length > 0) {
          // 同类型互斥：后端自动停用了其它同类型模型，同步本地列表
          setBackends((bs) =>
            bs.map((x) => (disabled.includes(x.name) ? { ...x, enabled: false } : x)),
          );
          flash(`✓ 已保存「${b.name}」 · 同类型「${disabled.join('、')}」已自动停用`);
        } else {
          const keyMsg = b.apiKeyRef ? ` · Keyring: ${b.apiKeyRef}` : '';
          flash(`✓ 已保存「${b.name}」到 router.db${keyMsg}`);
        }
      }
    } catch (e) {
      flash(`⚠ 「${b.name}」保存失败 · ${String(e)}`, 'err');
    }
  };

  /** 真实删除：先调 IPC，失败回滚本地 state。 */
  const doDeleteBackend = async (name: string): Promise<void> => {
    if (!window.confirm(`确认删除模型「${name}」？删除后依赖此模型的任务将降级到链尾。`)) {
      return;
    }
    // 乐观更新本地
    const prev = backends;
    setBackends((bs) => bs.filter((b) => b.name !== name));
    try {
      const { ipc } = await import('@/ipc/invoke');
      const r = await ipc.routerDeleteBackend(name);
      if (!r.ok) {
        // 失败回滚
        setBackends(prev);
        flash(`✗ 删除「${name}」失败`, 'err');
      } else {
        flash(`✓ 已删除「${name}」（router.db 已同步）`);
      }
    } catch (e) {
      setBackends(prev);
      flash(`✗ 删除「${name}」失败 · ${String(e)}`, 'err');
    }
  };

  return (
    <div className="mx-auto max-w-4xl space-y-6">
      <header className="flex items-start justify-between">
        <div>
          <h1 className="text-ui-lg font-semibold">模型管理</h1>
          <p className="mt-1 text-2xs text-fg-muted">
            多模型注册表，喂给五维评估调度（能力/成本/延迟/合规/可用性）。
            <span style={{ color: '#059669' }}> API Key 走系统 Keyring 占位符，绝不明文落库。</span>
            <span style={{ color: '#795e26' }}> 持久化真源：router.db</span>
          </p>
        </div>
        <div className="flex flex-shrink-0 gap-2">
          <button
            type="button"
            onClick={() => void showAgentVersion()}
            className="rounded px-3 py-1.5 text-ui"
            style={{ backgroundColor: '#ececec', color: '#333333' }}
            title="查看 Agent 版本指纹（诊断 404）"
          >
            🔍 Agent 版本
          </button>
          <button
            type="button"
            onClick={() => void showAgentLog()}
            className="rounded px-3 py-1.5 text-ui"
            style={{ backgroundColor: '#ececec', color: '#333333' }}
            title="读 eaide.log 末尾（devtools console 拿完整）"
          >
            📋 日志
          </button>
          <button
            type="button"
            onClick={() => void restartAgent()}
            className="rounded px-3 py-1.5 text-ui"
            style={{ backgroundColor: '#ececec', color: '#cd3131' }}
            title="杀掉 :8765 占用者，下一次请求触发重启"
          >
            🔄 重启 Agent
          </button>
          <button
            type="button"
            onClick={() => setCreating(true)}
            className="rounded px-3 py-1.5 text-ui font-semibold"
            style={{ backgroundColor: '#007acc', color: '#fff' }}
          >
            ＋ 新增模型
          </button>
        </div>
      </header>

      {/* 模型列表 */}
      <section>
        <SectionTitle>模型列表（{backends.length}）</SectionTitle>
        <div className="space-y-2">
          {backends.map((b) => {
            const badge = TYPE_BADGE[b.type];
            const cb = CB_PILL[b.circuit];
            return (
              <div
                key={b.name}
                className="rounded p-3"
                style={{ backgroundColor: '#f3f3f3', border: '1px solid #d4d4d4', opacity: b.enabled ? 1 : 0.55 }}
              >
                <div className="flex items-center gap-2">
                  <span className="font-mono text-ui font-semibold text-fg">{b.name}</span>
                  <span className="rounded px-1.5 text-2xs font-bold" style={{ backgroundColor: badge.color, color: '#ffffff', fontSize: 10 }}>
                    {badge.label}
                  </span>
                  <span className="text-2xs" style={{ color: cb.color }}>{cb.label}</span>
                  <span className="ml-auto flex items-center gap-1.5">
                    <button type="button" onClick={() => void testConn(b)} disabled={testing === b.name} className="rounded px-2 py-0.5 text-2xs" style={{ backgroundColor: '#ececec', color: '#333333', opacity: testing === b.name ? 0.6 : 1, cursor: testing === b.name ? 'wait' : 'pointer' }}>
                      {testing === b.name ? '探测中…' : '测试连接'}
                    </button>
                    <button type="button" onClick={() => setEditing(b)} className="rounded px-2 py-0.5 text-2xs" style={{ backgroundColor: '#ececec', color: '#333333' }}>编辑</button>
                    <button type="button" onClick={() => void doDeleteBackend(b.name)} className="rounded px-2 py-0.5 text-2xs" style={{ backgroundColor: '#ececec', color: '#cd3131' }}>删除</button>
                    {/* 用于代码导航 单选（互斥）：点亮表示作为 codenav 默认 backend */}
                    {b.enabled && (
                      <button
                        type="button"
                        onClick={() => void (codenavBound === b.name ? unbindCodenav() : useForCodenav(b.name))}
                        title={codenavBound === b.name ? '点击解绑' : '点击设为代码导航默认'}
                        className="rounded px-1.5 py-0.5 text-2xs"
                        style={{
                          backgroundColor: codenavBound === b.name ? '#0e639c' : '#ececec',
                          color: codenavBound === b.name ? '#ffffff' : '#059669',
                          border: `1px solid ${codenavBound === b.name ? '#0e639c' : '#059669'}`,
                        }}
                      >
                        {codenavBound === b.name ? '✓ 代码导航' : '用于代码导航'}
                      </button>
                    )}
                    <label className="ml-1 flex cursor-pointer items-center gap-1 text-2xs" style={{ color: '#333333' }}>
                      <input type="checkbox" checked={b.enabled} onChange={() => toggleEnabled(b.name)} />
                      启用
                    </label>
                  </span>
                </div>
                <div className="mt-1.5 grid grid-cols-5 gap-x-4 gap-y-1 text-2xs" style={{ color: '#6e6e6e' }}>
                  <span>模型：<span style={{ color: '#795e26' }}>{b.model}</span></span>
                  <span>驻留：<span style={{ color: b.residency === 'cloud' ? '#c586c0' : '#059669' }}>{b.residency}</span></span>
                  <span>上下文：<span style={{ color: '#0b6bcb' }}>{formatContextTokens(b.maxContext)}</span></span>
                  <span>成本：{b.costPer1k === 0 ? '免费' : `$${b.costPer1k}/1k`}</span>
                  <span>超时：{b.timeout}s</span>
                  <span className="col-span-5 truncate">能力：{b.capabilities.join(' · ')}</span>
                  <span className="col-span-5 truncate" style={{ color: '#6a6a6a' }}>{b.baseUrl}</span>
                </div>
              </div>
            );
          })}
        </div>
      </section>



      {toast && (() => {
        const parsed = (() => {
          try {
            return JSON.parse(toast) as { msg: string; kind: 'ok' | 'err' };
          } catch {
            return { msg: toast, kind: 'ok' as const };
          }
        })();
        const accent = parsed.kind === 'err' ? '#cd3131' : '#059669';
        return (
          <div
            className="fixed bottom-6 right-6 z-[210] max-w-[520px] rounded px-4 py-2 text-ui shadow-2xl"
            style={{ backgroundColor: '#f3f3f3', color: '#1f1f1f', border: `1px solid ${accent}` }}
          >
            {parsed.msg}
          </div>
        );
      })()}

      {(editing || creating) && (
        <BackendEditModal
          initial={editing}
          existingNames={backends.map((b) => b.name)}
          onCancel={() => { setEditing(null); setCreating(false); }}
          onSave={(b) => void saveBackend(b)}
        />
      )}

      {/* Phase 13 V0: DSpark 推测解码策略配置 */}
      <DSparkConfigSection />
    </div>
  );
}

// ---- 新增/编辑弹窗 ---------------------------------------------------------

function BackendEditModal({
  initial,
  existingNames,
  onCancel,
  onSave,
}: {
  initial: Backend | null;
  existingNames: string[];
  onCancel: () => void;
  onSave: (b: Backend) => void;
}): JSX.Element {
  const [b, setB] = useState<Backend>(
    initial ?? {
      name: '', type: 'local', baseUrl: '', model: '', residency: 'local',
      costPer1k: 0, timeout: 30, capabilities: [], circuit: 'closed', enabled: true,
      maxContext: 32768, apiKeyRef: null, role: 'execution',
    },
  );
  const [apiKey, setApiKey] = useState('');
  const [apiKeyLoaded, setApiKeyLoaded] = useState(false);
  const [role, setRole] = useState<'utility' | 'reasoning' | 'execution'>(
    (initial as any)?.role ?? 'execution'
  );
  const [err, setErr] = useState<string | null>(null);
  const isNew = initial === null;

  // 编辑现有模型：api_key_ref 直接存明文 key，无需读凭据管理器
  useEffect(() => {
    if (initial?.apiKeyRef) {
      setApiKey(initial.apiKeyRef);
    }
    setApiKeyLoaded(true);
  }, [initial?.apiKeyRef]);

  const set = <K extends keyof Backend>(k: K, v: Backend[K]): void => setB((x) => ({ ...x, [k]: v }));

  const submit = async (): Promise<void> => {
    if (!b.name.trim()) return setErr('名称必填');
    if (isNew && existingNames.includes(b.name)) return setErr('名称已存在');
    if (!/^https?:\/\//.test(b.baseUrl)) return setErr('Base URL 需以 http(s):// 开头');
    if (!Number.isInteger(b.maxContext) || b.maxContext < 256 || b.maxContext > 2_000_000) {
      return setErr('上下文大小必须在 256 ~ 2,000,000 tokens 之间（整数）');
    }
    // Phase 2C V2.5 协议校验
    if ((b.type === 'private' || b.type === 'cloud') && !b.baseUrl.trim()) {
      return setErr(`${b.type} 模型必须配置 base_url`);
    }
    if (b.type === 'cloud' && !apiKey.trim() && isNew) {
      return setErr('云端必须配置 API Key');
    }

    // API Key 直接存入 DB（配置文件模式，不走系统凭据管理器）
    let apiKeyRef = b.apiKeyRef;
    if (apiKey && apiKey.trim()) {
      apiKeyRef = apiKey.trim();
    }

    onSave({ ...b, name: b.name.trim(), role, apiKeyRef } as any);
  };

  return (
    <div className="fixed inset-0 z-[220] flex items-center justify-center" style={{ backgroundColor: 'rgba(0,0,0,0.55)' }} onClick={onCancel}>
      <div className="w-[520px] rounded shadow-2xl" style={{ backgroundColor: '#f3f3f3', border: '1px solid #007acc' }} onClick={(e) => e.stopPropagation()}>
        <div className="border-b px-4 py-2 text-ui-lg font-semibold text-fg" style={{ borderColor: '#d4d4d4' }}>
          {isNew ? '新增模型' : `编辑模型 · ${b.name}`}
        </div>
        <div className="space-y-3 p-4">
          <Field label="名称（唯一）" value={b.name} disabled={!isNew} onChange={(v) => set('name', v)} />
          <div className="grid grid-cols-2 gap-3">
            <Select label="类型" value={b.type} options={['local', 'private', 'cloud']} onChange={(v) => set('type', v as BackendType)} />
            <Select label="数据驻留" value={b.residency} options={['local', 'private', 'cloud']} onChange={(v) => set('residency', v as Residency)} />
          </div>
          {/* Phase 2C V2.5: 模型角色（utility / reasoning / execution）*/}
          <div>
            <label className="mb-1 block text-2xs" style={{ color: '#616161' }}>角色（Phase 2C V2.5：路由按角色选）</label>
            <div className="flex gap-2">
              {([
                { v: 'utility' as const, l: '端侧小模型', d: 'SQL 语法检查 / 简单意图' },
                { v: 'reasoning' as const, l: '推理模型', d: '计划 + 大纲' },
                { v: 'execution' as const, l: '复杂模型', d: '具体实现' },
              ]).map((opt) => (
                <label
                  key={opt.v}
                  className="flex-1 cursor-pointer rounded border px-2 py-1.5"
                  style={{
                    backgroundColor: role === opt.v ? '#0e639c20' : '#ffffff',
                    borderColor: role === opt.v ? '#0e639c' : '#1f1f1f',
                  }}
                >
                  <input
                    type="radio"
                    name="role"
                    checked={role === opt.v}
                    onChange={() => setRole(opt.v)}
                    className="mr-1"
                  />
                  <span className="text-2xs font-semibold" style={{ color: '#1f1f1f' }}>
                    {opt.l}
                  </span>
                  <div className="text-2xs" style={{ color: '#616161' }}>{opt.d}</div>
                </label>
              ))}
            </div>
          </div>
          <Field label="Base URL" value={b.baseUrl} onChange={(v) => set('baseUrl', v)} />
          <ContextSizePicker
            value={b.maxContext}
            onChange={(n) => set('maxContext', n)}
          />
          <Field label="Model" value={b.model} onChange={(v) => set('model', v)} />
          <Field
            label={
              apiKeyLoaded
                ? 'API Key（写入系统 Keyring，仅存占位符引用）'
                : 'API Key（从 Keyring 读取中…）'
            }
            type="password"
            value={apiKey}
            onChange={setApiKey}
            disabled={!apiKeyLoaded}
          />
          {b.apiKeyRef && (
            <div className="text-2xs" style={{ color: '#6a9955' }}>
              🔑 Keyring 占位符：<code>{b.apiKeyRef}</code>
              {apiKey ? '（已加载）' : '（尚未配置）'}
            </div>
          )}
          <div className="grid grid-cols-2 gap-3">
            <Field label="成本 $/1k tokens" type="number" value={String(b.costPer1k)} onChange={(v) => set('costPer1k', Number(v) || 0)} />
            <Field label="超时（秒）" type="number" value={String(b.timeout)} onChange={(v) => set('timeout', Number(v) || 30)} />
          </div>
          <Field label="能力（逗号分隔）" value={b.capabilities.join(', ')} onChange={(v) => set('capabilities', v.split(',').map((s) => s.trim()).filter(Boolean))} />
          {err && <div className="text-2xs" style={{ color: '#cd3131' }}>{err}</div>}
        </div>
        <div className="flex justify-end gap-2 border-t px-4 py-2" style={{ borderColor: '#d4d4d4' }}>
          <button type="button" onClick={onCancel} className="rounded px-3 py-1.5 text-ui" style={{ backgroundColor: '#ececec', color: '#333333' }}>取消</button>
          <button type="button" onClick={() => void submit()} className="rounded px-3 py-1.5 text-ui font-semibold" style={{ backgroundColor: '#007acc', color: '#fff' }}>保存</button>
        </div>
      </div>
    </div>
  );
}

// ---- helpers ---------------------------------------------------------------

function SectionTitle({ children }: { children: React.ReactNode }): JSX.Element {
  return <h2 className="mb-2 text-2xs font-semibold uppercase tracking-wider" style={{ color: '#616161' }}>{children}</h2>;
}

function Field({
  label, value, onChange, type = 'text', disabled = false,
}: {
  label: string; value: string; onChange: (v: string) => void;
  type?: 'text' | 'password' | 'number'; disabled?: boolean;
}): JSX.Element {
  return (
    <label className="block">
      <span className="mb-1 block text-2xs text-fg-muted">{label}</span>
      <input
        type={type}
        value={value}
        disabled={disabled}
        onChange={(e) => onChange(e.target.value)}
        className="w-full rounded px-2 py-1 text-ui outline-none"
        style={{ backgroundColor: disabled ? '#e8e8e8' : '#ececec', color: disabled ? '#616161' : '#1f1f1f', border: '1px solid #d4d4d4' }}
      />
    </label>
  );
}

function Select({
  label, value, options, onChange,
}: {
  label: string; value: string; options: string[]; onChange: (v: string) => void;
}): JSX.Element {
  return (
    <label className="block">
      <span className="mb-1 block text-2xs text-fg-muted">{label}</span>
      <select
        value={value}
        onChange={(e) => onChange(e.target.value)}
        className="w-full rounded px-2 py-1 text-ui outline-none"
        style={{ backgroundColor: '#ececec', color: '#1f1f1f', border: '1px solid #d4d4d4' }}
      >
        {options.map((o) => (
          <option key={o} value={o}>{o}</option>
        ))}
      </select>
    </label>
  );
}

/**
 * ContextSizePicker —— 上下文大小选择器。
 *
 * UX：
 *   - 7 个 chip：8K / 32K / 128K / 200K / 400K / 1M / 自定义
 *   - 选「自定义」时下方出现 input，输入整数（256 ~ 2,000,000）
 *   - 智能体调用模型时按此 tokens 截断 history；超长会自动 summarise
 *
 * 选中的 tokens 值通过 onChange 抛出，存到 backend.maxContext。
 */
function ContextSizePicker({
  value,
  onChange,
}: {
  value: number;
  onChange: (tokens: number) => void;
}): JSX.Element {
  const cur = tokensToPreset(value);
  const PRESETS: ContextSize['preset'][] = ['8K', '32K', '128K', '200K', '400K', '1M', 'custom'];

  const pickPreset = (p: ContextSize['preset']): void => {
    if (p === 'custom') {
      // 进 custom：保留当前 tokens 或默认 32K
      onChange(cur.preset === 'custom' ? cur.tokens : 32768);
      return;
    }
    onChange(CTX_PRESETS[p]);
  };

  return (
    <div>
      <span className="mb-1 block text-2xs text-fg-muted">上下文大小（智能体调用时按此截断 history）</span>
      <div className="flex flex-wrap gap-1.5">
        {PRESETS.map((p) => {
          const selected = cur.preset === p;
          const label = p === 'custom' ? `自定义${cur.preset === 'custom' ? ` · ${formatContextTokens(cur.tokens)}` : ''}` : p;
          return (
            <button
              key={p}
              type="button"
              onClick={() => pickPreset(p)}
              className="rounded px-2.5 py-1 text-2xs font-mono font-semibold transition-colors"
              style={{
                backgroundColor: selected ? '#0e639c' : '#ececec',
                color: selected ? '#ffffff' : '#333333',
                border: `1px solid ${selected ? '#0e639c' : '#1f1f1f'}`,
              }}
              title={
                p === 'custom'
                  ? '自定义 tokens（256 ~ 2,000,000）'
                  : `${CTX_PRESETS[p].toLocaleString()} tokens`
              }
            >
              {label}
            </button>
          );
        })}
      </div>
      {cur.preset === 'custom' && (
        <div className="mt-1.5 flex items-center gap-2">
          <input
            type="number"
            min={256}
            max={2_000_000}
            step={256}
            value={cur.tokens}
            onChange={(e) => {
              const n = Number(e.target.value);
              if (Number.isFinite(n)) onChange(Math.max(256, Math.min(2_000_000, Math.floor(n))));
            }}
            className="w-40 rounded px-2 py-1 text-ui font-mono outline-none"
            style={{ backgroundColor: '#ececec', color: '#1f1f1f', border: '1px solid #d4d4d4' }}
          />
          <span className="text-2xs text-fg-muted">tokens（256 ~ 2,000,000）</span>
          <span className="text-2xs" style={{ color: '#6a9955' }}>
            ≈ {formatContextTokens(cur.tokens)} · 估算字符数 {Math.round(cur.tokens * 1.5).toLocaleString()}
          </span>
        </div>
      )}
    </div>
  );
}
