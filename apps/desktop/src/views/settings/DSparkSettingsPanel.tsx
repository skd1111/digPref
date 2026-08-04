/**
 * DSparkSettingsPanel —— Phase 13 推测解码独立设置页。
 *
 * Settings 顶级 Tab，与 Models / Secrets / Terminal 平级（路由 /settings/dspark）。
 * ModelManagementPanel 内的 DSparkConfigSection 已瘦身成 SummaryCard + 跳转链接。
 *
 * 功能：
 *   - 草稿模型路径（Tauri 文件对话框选择 .gguf）
 *   - 上下文大小（preset 快捷按钮 + 自定义输入）
 *   - GPU 加速（仅 CPU / 自动全部 / 指定层数，类似 LM Studio）
 *   - 全局开关 + 短输出阈值
 *   - 场景策略表 + 决策统计 + 重新加载 yaml
 *
 * 类型全部来自 @eaide/shared-protocol/dspark（与 FastAPI Pydantic + Rust serde 对齐）。
 */
import { useCallback, useEffect, useState } from 'react';
import { open } from '@tauri-apps/plugin-dialog';
import {
  DSPARK_CONTEXT_SIZE_MAX,
  DSPARK_CONTEXT_SIZE_MIN,
  DSPARK_GPU_LAYERS_MAX,
  MODE_COLOR,
  REASON_COLOR,
  type DSparkRuntimeConfig,
  type SpeculativePolicy,
} from '@eaide/shared-protocol';
import { ipc } from '@/ipc/invoke';

// ---- 上下文大小 preset ------------------------------------------------------

interface CtxPreset {
  label: string;
  tokens: number; // -1 = custom
}

const CTX_PRESETS: CtxPreset[] = [
  { label: '2K', tokens: 2048 },
  { label: '4K', tokens: 4096 },
  { label: '8K', tokens: 8192 },
  { label: '16K', tokens: 16384 },
  { label: '32K', tokens: 32768 },
  { label: '64K', tokens: 65536 },
  { label: '128K', tokens: 131072 },
  { label: '自定义', tokens: -1 },
];

function isPreset(n: number): CtxPreset | undefined {
  return CTX_PRESETS.find((p) => p.tokens === n && p.tokens !== -1);
}

function formatTokens(n: number): string {
  if (n >= 1000 && n % 1000 === 0) return `${n / 1000}K`;
  return String(n);
}

function clamp(v: number, lo: number, hi: number): number {
  return Math.max(lo, Math.min(hi, v));
}

// ---- 主组件 ----------------------------------------------------------------

export function DSparkSettingsPanel(): JSX.Element {
  const [cfg, setCfg] = useState<DSparkRuntimeConfig | null>(null);
  const [policies, setPolicies] = useState<SpeculativePolicy[]>([]);
  const [loading, setLoading] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [reloading, setReloading] = useState(false);

  // 瞬时编辑态（立即保存，无 Save 按钮）
  const [saving, setSaving] = useState<string | null>(null); // 哪个字段在保存中
  const [saveHint, setSaveHint] = useState<string | null>(null);

  // 自定义上下文大小输入
  const [customCtx, setCustomCtx] = useState<number>(4096);

  const refresh = useCallback(async (): Promise<void> => {
    setLoading(true);
    setErr(null);
    try {
      const [c, p] = await Promise.all([
        ipc.dsparkGetConfig(),
        ipc.dsparkGetPolicies(),
      ]);
      setCfg(c);
      setPolicies(p);
      // 同步 custom ctx
      if (!isPreset(c.context_size)) {
        setCustomCtx(c.context_size);
      }
    } catch (e) {
      setErr(String(e));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  // 即时保存单个字段
  const saveField = useCallback(
    async (field: string, value: unknown): Promise<void> => {
      setSaving(field);
      setSaveHint(null);
      setErr(null);
      try {
        await ipc.dsparkUpdateConfig({ [field]: value } as Parameters<typeof ipc.dsparkUpdateConfig>[0]);
        // V0 决策层只持久化；context_size / gpu_layers / short_output_threshold 真正生效要等
        // V1 llama.cpp 加载草稿模型时。draft_model_path 是路径指针，立即生效。
        const runtimeFields = new Set(['draft_model_path', 'enable_global']);
        const hint = runtimeFields.has(field)
          ? `✓ ${field} 已保存（运行时立即生效）`
          : `✓ ${field} 已保存（V1 llama.cpp 加载时生效）`;
        setSaveHint(hint);
        await refresh();
      } catch (e) {
        setErr(String(e));
      } finally {
        setSaving(null);
      }
    },
    [refresh],
  );

  // GPU 层数切换
  const setGpuMode = useCallback(
    (layers: number): void => {
      void saveField('gpu_layers', layers);
    },
    [saveField],
  );

  // 上下文 preset 点击
  const setContextPreset = useCallback(
    (preset: CtxPreset): void => {
      if (preset.tokens === -1) {
        // 切到自定义模式
        void saveField('context_size', customCtx);
      } else {
        void saveField('context_size', preset.tokens);
      }
    },
    [saveField, customCtx],
  );

  // 自定义上下文确认
  const commitCustomCtx = useCallback((): void => {
    const v = clamp(Math.round(customCtx), 512, 262144);
    setCustomCtx(v);
    void saveField('context_size', v);
  }, [saveField, customCtx]);

  // 文件浏览
  const handleBrowseDraftPath = useCallback(async (): Promise<void> => {
    setErr(null);
    setSaveHint(null);
    let selected: string | string[] | null = null;
    try {
      selected = await open({
        multiple: false,
        directory: false,
        title: '选择草稿模型（GGUF）',
        filters: [
          { name: 'GGUF 模型', extensions: ['gguf'] },
          { name: '全部文件', extensions: ['*'] },
        ],
      });
    } catch (e) {
      // eslint-disable-next-line no-console
      console.error('[DSpark] file dialog failed:', e);
      setErr(`打开文件对话框失败：${String(e)}`);
      return;
    }
    if (!selected) return;
    const filePath = Array.isArray(selected) ? selected[0] : selected;
    if (!filePath) return;

    setSaving('draft_model_path');
    try {
      const res = await ipc.dsparkSetDraftModelPath(filePath);
      setSaveHint(`✓ 已保存到 ${res.persisted_to}`);
      await refresh();
    } catch (e) {
      setErr(String(e));
    } finally {
      setSaving(null);
    }
  }, [refresh]);

  const handleClearDraftPath = useCallback(async (): Promise<void> => {
    setSaving('draft_model_path');
    setSaveHint(null);
    try {
      await ipc.dsparkSetDraftModelPath(null);
      setSaveHint('✓ 已清空（DSpark 全局禁用）');
      await refresh();
    } catch (e) {
      setErr(String(e));
    } finally {
      setSaving(null);
    }
  }, [refresh]);

  const handleReload = useCallback(async (): Promise<void> => {
    setReloading(true);
    setErr(null);
    try {
      await ipc.dsparkReloadPolicies();
      await refresh();
      setSaveHint('✓ 策略已重新加载');
    } catch (e) {
      setErr(String(e));
    } finally {
      setReloading(false);
    }
  }, [refresh]);

  // ---------------- 渲染 ----------------

  return (
    <div>
      <div className="mb-4 flex items-center justify-between">
        <div>
          <h1 className="text-lg font-semibold" style={{ color: '#1f1f1f' }}>
            ⚡ 推测解码（DSpark）
          </h1>
          <p className="mt-1 text-2xs" style={{ color: '#616161' }}>
            Phase 13 V0：决策层（4 字段注入 RouteDecision）+ 模型配置 + 12 条策略
          </p>
        </div>
        <button
          type="button"
          onClick={() => void refresh()}
          disabled={loading}
          className="rounded px-2 py-0.5 text-2xs"
          style={{
            backgroundColor: '#ececec',
            color: loading ? '#616161' : '#333333',
            cursor: loading ? 'wait' : 'pointer',
          }}
        >
          {loading ? '⟳' : '刷新'}
        </button>
      </div>

      {err && (
        <div
          className="mb-3 rounded px-2 py-1 text-2xs"
          style={{ backgroundColor: '#3c1e1e', color: '#cd3131' }}
        >
          {err}
        </div>
      )}

      {saveHint && (
        <div className="mb-3 text-2xs" style={{ color: '#059669' }}>
          {saveHint}
        </div>
      )}

      {/* ==================== 模型配置 ==================== */}
      <SectionTitle title="模型配置" />

      {/* 草稿模型路径 */}
      <SettingRow
        label="草稿模型路径"
        hint="Qwen3.5-0.8B-Instruct-Q4_K_M.gguf（运行时立即生效 + 持久化）"
      >
        <div className="flex items-center gap-2">
          <span
            className="flex-1 overflow-hidden rounded px-2 py-1 font-mono text-2xs text-ellipsis whitespace-nowrap"
            style={{
              backgroundColor: '#ffffff',
              color: cfg?.draft_model_path ? '#059669' : '#616161',
              border: '1px solid #d4d4d4',
            }}
            title={cfg?.draft_model_path ?? undefined}
          >
            {cfg?.draft_model_path ?? '（未配置 — 点击「浏览」选择 .gguf 文件）'}
          </span>
          <button
            type="button"
            onClick={() => void handleBrowseDraftPath()}
            disabled={saving === 'draft_model_path'}
            className="rounded px-3 py-1 text-2xs whitespace-nowrap"
            style={{
              backgroundColor: saving === 'draft_model_path' ? '#ececec' : '#0e639c',
              color: '#ffffff',
              cursor: saving === 'draft_model_path' ? 'not-allowed' : 'pointer',
            }}
          >
            {saving === 'draft_model_path' ? '…' : '浏览…'}
          </button>
          <button
            type="button"
            onClick={() => void handleClearDraftPath()}
            disabled={saving === 'draft_model_path' || !cfg?.draft_model_path}
            className="rounded px-2 py-1 text-2xs whitespace-nowrap"
            style={{
              backgroundColor: saving === 'draft_model_path' || !cfg?.draft_model_path ? '#ececec' : '#fdeaea',
              color: saving === 'draft_model_path' || !cfg?.draft_model_path ? '#616161' : '#cd3131',
              cursor: saving === 'draft_model_path' || !cfg?.draft_model_path ? 'not-allowed' : 'pointer',
            }}
            title="清空路径"
          >
            清空
          </button>
        </div>
      </SettingRow>

      {/* 上下文大小 */}
      <SettingRow label="上下文大小" hint={`当前：${cfg ? formatTokens(cfg.context_size) : '—'}（V1 llama.cpp 加载时生效）`}>
        <div className="flex flex-wrap items-center gap-1.5">
          {CTX_PRESETS.map((p) => {
            const active = cfg && (p.tokens === -1 ? !isPreset(cfg.context_size) : cfg.context_size === p.tokens);
            return (
              <button
                key={p.label}
                type="button"
                onClick={() => setContextPreset(p)}
                disabled={saving === 'context_size'}
                className="rounded px-2.5 py-0.5 text-2xs font-mono"
                style={{
                  backgroundColor: active ? '#0e639c' : '#ececec',
                  color: active ? '#ffffff' : '#333333',
                  cursor: saving === 'context_size' ? 'wait' : 'pointer',
                }}
              >
                {p.label}
              </button>
            );
          })}
        </div>
        {cfg && !isPreset(cfg.context_size) && (
          <div className="mt-1.5 flex items-center gap-2">
            <input
              type="number"
              min={DSPARK_CONTEXT_SIZE_MIN}
              max={DSPARK_CONTEXT_SIZE_MAX}
              step={256}
              value={customCtx}
              onChange={(e) => setCustomCtx(Number(e.target.value))}
              onBlur={() => commitCustomCtx()}
              onKeyDown={(e) => { if (e.key === 'Enter') commitCustomCtx(); }}
              disabled={saving === 'context_size'}
              className="w-24 rounded px-2 py-0.5 font-mono text-2xs"
              style={{
                backgroundColor: '#ffffff',
                color: '#795e26',
                border: '1px solid #dcdcaa',
              }}
            />
            <span className="text-2xs" style={{ color: '#616161' }}>
              tokens（{DSPARK_CONTEXT_SIZE_MIN.toLocaleString()} ~ {DSPARK_CONTEXT_SIZE_MAX.toLocaleString()}，回车确认）
            </span>
          </div>
        )}
      </SettingRow>

      {/* GPU 加速 */}
      <SettingRow label="GPU 加速" hint="llama.cpp n_gpu_layers（V1 加载时生效）">
        <div className="flex flex-col gap-2">
          {/* 三选一 radio */}
          <div className="flex items-center gap-4">
            <GpuRadio
              checked={cfg?.gpu_layers === 0}
              onChange={() => setGpuMode(0)}
              label="仅 CPU"
              disabled={saving === 'gpu_layers'}
            />
            <GpuRadio
              checked={cfg != null && cfg.gpu_layers > 0}
              onChange={() => setGpuMode(cfg?.gpu_layers ? cfg.gpu_layers : 16)}
              label="指定层数"
              disabled={saving === 'gpu_layers'}
            />
            <GpuRadio
              checked={cfg?.gpu_layers === -1}
              onChange={() => setGpuMode(-1)}
              label="自动全部"
              disabled={saving === 'gpu_layers'}
            />
          </div>
          {/* 层数滑块（仅 "指定层数" 模式） */}
          {cfg && cfg.gpu_layers > 0 && (
            <div className="flex items-center gap-2">
              <span className="text-2xs font-mono" style={{ color: '#616161', minWidth: 24 }}>
                {cfg.gpu_layers}
              </span>
              <input
                type="range"
                min={1}
                max={DSPARK_GPU_LAYERS_MAX}
                value={Math.min(cfg.gpu_layers, DSPARK_GPU_LAYERS_MAX)}
                onChange={(e) => {
                  const v = Number(e.target.value);
                  if (cfg) setCfg({ ...cfg, gpu_layers: v });
                }}
                onMouseUp={() => {
                  if (cfg) void saveField('gpu_layers', cfg.gpu_layers);
                }}
                onTouchEnd={() => {
                  if (cfg) void saveField('gpu_layers', cfg.gpu_layers);
                }}
                disabled={saving === 'gpu_layers'}
                className="flex-1"
                style={{ accentColor: '#0e639c' }}
              />
              <input
                type="number"
                min={1}
                max={DSPARK_GPU_LAYERS_MAX}
                value={cfg.gpu_layers}
                onChange={(e) => {
                  const v = clamp(Number(e.target.value), 1, DSPARK_GPU_LAYERS_MAX);
                  if (cfg) setCfg({ ...cfg, gpu_layers: v });
                }}
                onBlur={() => {
                  if (cfg) void saveField('gpu_layers', cfg.gpu_layers);
                }}
                onKeyDown={(e) => {
                  if (e.key === 'Enter' && cfg) void saveField('gpu_layers', cfg.gpu_layers);
                }}
                disabled={saving === 'gpu_layers'}
                className="w-14 rounded px-1.5 py-0.5 font-mono text-2xs"
                style={{
                  backgroundColor: '#ffffff',
                  color: '#1f1f1f',
                  border: '1px solid #d4d4d4',
                }}
              />
              <span className="text-2xs" style={{ color: '#616161' }}>
                / {DSPARK_GPU_LAYERS_MAX} layers
              </span>
            </div>
          )}
        </div>
      </SettingRow>

      {/* ==================== 推理控制 ==================== */}
      <SectionTitle title="推理控制" />

      {/* 全局开关 */}
      <SettingRow label="全局开关" hint={cfg?.enable_global ? 'DSpark 已启用' : 'DSpark 已禁用'}>
        <div className="flex items-center gap-2">
          <Toggle
            checked={cfg?.enable_global ?? true}
            onChange={() => {
              if (cfg) void saveField('enable_global', !cfg.enable_global);
            }}
            disabled={saving === 'enable_global'}
          />
          <span className="text-2xs font-mono" style={{ color: cfg?.enable_global ? '#059669' : '#cd3131' }}>
            {cfg?.enable_global ? '✓ 开' : '✗ 关'}
          </span>
        </div>
      </SettingRow>

      {/* 短输出阈值 */}
      <SettingRow label="短输出阈值" hint="低于此 token 数自动跳过 DSpark">
        <div className="flex items-center gap-2">
          <input
            type="number"
            min={1}
            max={999}
            value={cfg?.short_output_threshold ?? 20}
            onChange={(e) => {
              const v = clamp(Number(e.target.value), 1, 999);
              if (cfg) setCfg({ ...cfg, short_output_threshold: v });
            }}
            onBlur={() => {
              if (cfg) void saveField('short_output_threshold', cfg.short_output_threshold);
            }}
            onKeyDown={(e) => {
              if (e.key === 'Enter' && cfg) void saveField('short_output_threshold', cfg.short_output_threshold);
            }}
            disabled={saving === 'short_output_threshold'}
            className="w-20 rounded px-2 py-0.5 font-mono text-2xs"
            style={{
              backgroundColor: '#ffffff',
              color: '#1f1f1f',
              border: '1px solid #d4d4d4',
            }}
          />
          <span className="text-2xs" style={{ color: '#616161' }}>tokens</span>
        </div>
      </SettingRow>

      {/* 配置摘要 */}
      {cfg && (
        <div className="mt-4 grid grid-cols-2 gap-x-6 gap-y-1 text-2xs">
          <KV k="策略 yaml" v={cfg.yaml_path ?? '（默认）'} />
          <KV k="已加载策略" v={`${cfg.profile_count} 条`} color="#9cdcfe" />
          <KV
            k="本次会话决策"
            v={`${cfg.stats.total_decisions}（重启归零，V1 落盘）`}
            color="#9cdcfe"
          />
          <KV k="DSpark 启用率" v={`${cfg.stats.dspark_enabled_pct.toFixed(1)}%`} color="#4ec9b0" />
        </div>
      )}

      {/* ==================== 场景策略 ==================== */}
      <SectionTitle title="场景化策略" />

      {policies.length > 0 && (
        <table className="mb-3 w-full text-2xs">
          <thead>
            <tr style={{ color: '#616161' }}>
              <th className="text-left py-1">task_category</th>
              <th className="text-left py-1">mode</th>
              <th className="text-right py-1">K</th>
              <th className="text-right py-1">threshold</th>
              <th className="text-right py-1">enabled</th>
            </tr>
          </thead>
          <tbody>
            {policies.map((p) => (
              <tr key={p.task_category} style={{ borderTop: '1px solid #e0e0e0' }}>
                <td className="py-1 pr-3 font-mono" style={{ color: '#0b6bcb' }}>
                  {p.task_category}
                </td>
                <td className="py-1 pr-3" style={{ color: MODE_COLOR[p.mode] }}>
                  {p.mode}
                </td>
                <td className="py-1 pr-3 text-right font-mono" style={{ color: '#1f1f1f' }}>
                  {p.n_draft}
                </td>
                <td className="py-1 pr-3 text-right font-mono" style={{ color: '#1f1f1f' }}>
                  {p.draft_p_min.toFixed(2)}
                </td>
                <td className="py-1 text-right" style={{ color: p.enabled ? '#059669' : '#616161' }}>
                  {p.enabled ? '✓' : '–'}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}

      {/* 决策原因统计 */}
      {cfg && cfg.stats.total_decisions > 0 && (
        <div className="mb-4">
          <div className="mb-2 text-2xs font-semibold uppercase tracking-wider" style={{ color: '#616161' }}>
            本次会话决策原因（最近 100 条，重启归零）
          </div>
          <div className="flex flex-wrap gap-1">
            {Object.entries(cfg.stats.per_reason).map(([reason, count]) => {
              const color = (REASON_COLOR as Record<string, string>)[reason] ?? '#616161';
              return (
                <span
                  key={reason}
                  className="rounded px-1.5 py-0.5 text-2xs"
                  style={{
                    backgroundColor: '#ffffff',
                    color,
                    borderLeft: `3px solid ${color}`,
                  }}
                >
                  {reason} × {count}
                </span>
              );
            })}
          </div>
        </div>
      )}

      {/* 重新加载 */}
      <button
        type="button"
        onClick={() => void handleReload()}
        disabled={reloading}
        className="rounded px-3 py-1 text-2xs"
        style={{
          backgroundColor: reloading ? '#ececec' : '#0e639c',
          color: '#ffffff',
          cursor: reloading ? 'wait' : 'pointer',
        }}
      >
        {reloading ? '重新加载中…' : '重新加载策略'}
      </button>
    </div>
  );
}

// ---- 辅助子组件 ------------------------------------------------------------

function SectionTitle({ title }: { title: string }): JSX.Element {
  return (
    <div
      className="mb-3 mt-4 border-b pb-1 text-2xs font-semibold uppercase tracking-wider"
      style={{ color: '#616161', borderColor: '#d4d4d4' }}
    >
      {title}
    </div>
  );
}

function SettingRow({
  label,
  hint,
  children,
}: {
  label: string;
  hint?: string;
  children: React.ReactNode;
}): JSX.Element {
  return (
    <div className="mb-3">
      <div className="mb-1 flex items-baseline gap-2">
        <span className="text-2xs font-semibold" style={{ color: '#333333' }}>
          {label}
        </span>
        {hint && (
          <span className="text-2xs" style={{ color: '#616161' }}>
            — {hint}
          </span>
        )}
      </div>
      {children}
    </div>
  );
}

function Toggle({
  checked,
  onChange,
  disabled,
}: {
  checked: boolean;
  onChange: () => void;
  disabled?: boolean;
}): JSX.Element {
  return (
    <button
      type="button"
      role="switch"
      aria-checked={checked}
      onClick={onChange}
      disabled={disabled}
      className="relative inline-flex h-5 w-9 items-center rounded-full transition-colors"
      style={{
        backgroundColor: checked ? '#0e639c' : '#ececec',
        cursor: disabled ? 'not-allowed' : 'pointer',
        opacity: disabled ? 0.5 : 1,
      }}
    >
      <span
        className="inline-block h-3.5 w-3.5 rounded-full bg-white shadow transition-transform"
        style={{
          transform: checked ? 'translateX(18px)' : 'translateX(4px)',
        }}
      />
    </button>
  );
}

function GpuRadio({
  checked,
  onChange,
  label,
  disabled,
}: {
  checked: boolean;
  onChange: () => void;
  label: string;
  disabled?: boolean;
}): JSX.Element {
  return (
    <label
      className="flex items-center gap-1.5 text-2xs"
      style={{
        color: checked ? '#059669' : '#616161',
        cursor: disabled ? 'not-allowed' : 'pointer',
      }}
    >
      <input
        type="radio"
        checked={checked}
        onChange={onChange}
        disabled={disabled}
        style={{ accentColor: '#0e639c' }}
      />
      {label}
    </label>
  );
}

function KV({ k, v, color }: { k: string; v: string; color?: string }): JSX.Element {
  return (
    <div className="flex items-center gap-2">
      <span style={{ color: '#616161' }}>{k}</span>
      <span className="font-mono" style={{ color: color ?? '#1f1f1f' }}>
        {v}
      </span>
    </div>
  );
}
