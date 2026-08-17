/**
 * GenLimitsPanel —— 设置内「模型与回复」（生成限制两级回退）。
 *
 * 借鉴 DeepSeek Harness（dsh）的配置层级：每模型值优先，缺失回退全局默认。
 * 数据真源：router.db.llm_kv（key='gen_limits'），经 /router/gen-limits CRUD；
 * PUT 后后端热生效（LMRouter.reload_max_context），无需重启 Agent。
 *
 * 字段语义：
 *   - 最大输出长度（max_output_tokens）：输出上限（cap），限制一次生成的最大
 *     token 数防止过度输出；只降不升各调用点自带的任务预算。
 *   - 默认上下文长度（default_context_window）：模型未显式设置上下文时的回退值；
 *     每模型值在「模型管理」里配置，优先级更高。
 */
import { useEffect, useState } from 'react';

interface GenLimits {
  max_output_tokens: number;
  default_context_window: number;
}

/** 上下文长度预设档位（与 ModelManagementPanel 对齐） */
const CTX_PRESET_VALUES: number[] = [8192, 32768, 131072, 200000, 400000, 1000000];

function formatTokens(n: number): string {
  if (n >= 1_000_000 && n % 1_000_000 === 0) return `${n / 1_000_000}M`;
  if (n >= 1000 && n % 1000 === 0) return `${n / 1000}K`;
  return String(n);
}

function Row({
  title,
  desc,
  children,
}: {
  title: string;
  desc: string;
  children: React.ReactNode;
}): JSX.Element {
  return (
    <div
      className="mb-3 flex items-center justify-between gap-4 rounded border px-4 py-3"
      style={{ borderColor: '#d4d4d4', backgroundColor: '#fafafa' }}
    >
      <div className="min-w-0">
        <div className="text-ui font-semibold" style={{ color: '#1f1f1f' }}>
          {title}
        </div>
        <div className="mt-0.5 text-2xs leading-relaxed" style={{ color: '#616161' }}>
          {desc}
        </div>
      </div>
      <div className="flex flex-shrink-0 items-center gap-2">{children}</div>
    </div>
  );
}

export function GenLimitsPanel(): JSX.Element {
  const [limits, setLimits] = useState<GenLimits | null>(null);
  const [draft, setDraft] = useState<{ maxOutput: string; defaultCtx: string }>({
    maxOutput: '',
    defaultCtx: '',
  });
  const [saving, setSaving] = useState(false);
  const [toast, setToast] = useState<{ text: string; kind: 'ok' | 'err' } | null>(null);

  const flash = (text: string, kind: 'ok' | 'err'): void => {
    setToast({ text, kind });
    window.setTimeout(() => setToast(null), 3500);
  };

  useEffect(() => {
    let cancelled = false;
    void (async () => {
      const { ipc } = await import('@/ipc/invoke');
      // 等 Agent 就绪（启动竞态：Agent 慢于 EAIDE 是常见情况）
      try {
        const ready = await ipc.agentWaitReady(15);
        if (!ready.ready) {
          if (!cancelled) flash(`⚠ Agent 未就绪（${ready.error ?? 'timeout'}）`, 'err');
          return;
        }
      } catch (e) {
        if (!cancelled) flash(`⚠ agentWaitReady 失败 · ${String(e)}`, 'err');
        return;
      }
      for (let i = 0; i < 3; i++) {
        try {
          const r = await ipc.routerGetGenLimits();
          if (!cancelled) {
            setLimits(r.limits);
            setDraft({
              maxOutput: String(r.limits.max_output_tokens),
              defaultCtx: String(r.limits.default_context_window),
            });
          }
          return;
        } catch {
          await new Promise((res) => setTimeout(res, 800));
        }
      }
      if (!cancelled) flash('⚠ 读取生成限制失败 · 请检查 Agent 日志', 'err');
    })();
    return () => {
      cancelled = true;
    };
  }, []);

  const parsePositiveInt = (s: string): number | null => {
    const n = Number(s.trim());
    return Number.isInteger(n) && n > 0 ? n : null;
  };

  const save = async (): Promise<void> => {
    const maxOutput = parsePositiveInt(draft.maxOutput);
    const defaultCtx = parsePositiveInt(draft.defaultCtx);
    if (maxOutput === null || maxOutput < 1 || maxOutput > 1_000_000) {
      flash('最大输出长度需为 1 ~ 1,000,000 的整数', 'err');
      return;
    }
    if (defaultCtx === null || defaultCtx < 1024 || defaultCtx > 10_000_000) {
      flash('默认上下文长度需为 1024 ~ 10,000,000 的整数', 'err');
      return;
    }
    setSaving(true);
    try {
      const { ipc } = await import('@/ipc/invoke');
      const r = await ipc.routerSetGenLimits({
        max_output_tokens: maxOutput,
        default_context_window: defaultCtx,
      });
      setLimits(r.limits);
      flash('✓ 已保存并热生效（无需重启）', 'ok');
    } catch (e) {
      flash(`⚠ 保存失败 · ${String(e)}`, 'err');
    } finally {
      setSaving(false);
    }
  };

  const dirty =
    limits !== null &&
    (draft.maxOutput !== String(limits.max_output_tokens) ||
      draft.defaultCtx !== String(limits.default_context_window));

  return (
    <div className="mx-auto max-w-3xl">
      <h1 className="text-ui-lg font-semibold" style={{ color: '#1f1f1f' }}>
        模型与回复
      </h1>
      <p className="mb-4 mt-1 text-2xs" style={{ color: '#616161' }}>
        全局生成限制（两级回退）：每个模型的独立配置优先，未显式设置时回退到这里的默认值。
      </p>

      {toast && (
        <div
          className="mb-3 rounded border px-3 py-2 text-2xs"
          style={{
            borderColor: toast.kind === 'ok' ? '#059669' : '#cd3131',
            color: toast.kind === 'ok' ? '#059669' : '#cd3131',
            backgroundColor: '#ffffff',
          }}
        >
          {toast.text}
        </div>
      )}

      <Row
        title="默认上下文长度"
        desc="模型未显式设置时的上下文长度。每个模型可在「模型管理」里单独配置，优先级更高；该值同时作为后端未设上下文时的回退。"
      >
        <select
          className="rounded border px-2 py-1 text-2xs outline-none"
          style={{ borderColor: '#d4d4d4', backgroundColor: '#ffffff', color: '#1f1f1f' }}
          value={CTX_PRESET_VALUES.includes(Number(draft.defaultCtx)) ? draft.defaultCtx : 'custom'}
          onChange={(e) => {
            if (e.target.value !== 'custom') {
              setDraft((d) => ({ ...d, defaultCtx: e.target.value }));
            }
          }}
          disabled={limits === null}
        >
          {CTX_PRESET_VALUES.map((v) => (
            <option key={v} value={String(v)}>
              {formatTokens(v)}
            </option>
          ))}
          <option value="custom">自定义</option>
        </select>
        <input
          className="w-28 rounded border px-2 py-1 text-2xs outline-none"
          style={{ borderColor: '#d4d4d4', backgroundColor: '#ffffff', color: '#1f1f1f' }}
          value={draft.defaultCtx}
          onChange={(e) => setDraft((d) => ({ ...d, defaultCtx: e.target.value }))}
          placeholder="32768"
          inputMode="numeric"
          disabled={limits === null}
        />
      </Row>

      <Row
        title="最大输出长度"
        desc="限制模型一次生成的最大 Token 数，防止过度输出。作为全局上限注入所有模型调用，不会抬高各任务自带的预算。"
      >
        <input
          className="w-28 rounded border px-2 py-1 text-2xs outline-none"
          style={{ borderColor: '#d4d4d4', backgroundColor: '#ffffff', color: '#1f1f1f' }}
          value={draft.maxOutput}
          onChange={(e) => setDraft((d) => ({ ...d, maxOutput: e.target.value }))}
          placeholder="32768"
          inputMode="numeric"
          disabled={limits === null}
        />
      </Row>

      <div className="mt-4 flex justify-end">
        <button
          type="button"
          onClick={() => void save()}
          disabled={!dirty || saving}
          className="rounded px-4 py-1.5 text-ui text-white disabled:opacity-50"
          style={{ backgroundColor: '#0e639c' }}
        >
          {saving ? '保存中…' : '保存'}
        </button>
      </div>
    </div>
  );
}
