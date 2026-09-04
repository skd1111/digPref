/**
 * ScoringWeightsEditor —— 5 维评分权重滑块（Phase 2C V2.0）。
 *
 * V2 增量：
 *   - 启动时从后端 `routerGetWeights()` 拉真值（V0 用 store 默认值）
 *   - 滑块 onChange 仅改前端 store（实时预览）
 *   - 「保存」按钮调 `routerSetWeights()` → PUT /router/weights（落库 + 热生效 Engine）
 *   - 「恢复默认」按钮：前端 store + 后端都重置
 *   - Σ ≠ 1 警告 + 保存按钮 disabled（防止前端编辑漂移）
 */
import { useEffect, useState } from 'react';
import { useRouterStore, DEFAULT_WEIGHTS } from '@/store/routerStore';
import { ipc } from '@/ipc/invoke';

const DIMENSIONS = [
  { key: 'capability' as const, label: '能力', color: '#0451a5' },
  { key: 'cost' as const, label: '成本', color: '#059669' },
  { key: 'latency' as const, label: '延迟', color: '#795e26' },
  { key: 'compliance' as const, label: '合规', color: '#c586c0' },
  { key: 'availability' as const, label: '可用', color: '#cd3131' },
];

export function ScoringWeightsEditor(): JSX.Element {
  const weights = useRouterStore((s) => s.weights);
  const setWeights = useRouterStore((s) => s.setWeights);
  const resetWeights = useRouterStore((s) => s.resetWeights);

  const [saving, setSaving] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [hint, setHint] = useState<string | null>(null);

  const total = Object.values(weights).reduce((a, b) => a + b, 0);
  const sumValid = Math.abs(total - 1) < 0.02;

  // 启动时从后端拉真值（覆盖 store 默认值）
  useEffect(() => {
    void (async () => {
      try {
        const r = await ipc.routerGetWeights();
        setWeights(r.weights);
      } catch (e) {
        // 后端未就绪时回退到默认
        // eslint-disable-next-line no-console
        console.warn('[ScoringWeightsEditor] load weights failed:', e);
      }
    })();
  }, [setWeights]);

  const handleSave = async (): Promise<void> => {
    if (!sumValid) {
      setErr(`Σ 必须 = 1.00（当前 ${total.toFixed(2)}）`);
      return;
    }
    setSaving(true);
    setErr(null);
    setHint(null);
    try {
      const r = await ipc.routerSetWeights(weights);
      setHint('✓ 已保存，引擎已热生效');
      // 用后端返回的真值覆盖（防御性：保证前端与后端一致）
      setWeights(r.weights);
    } catch (e) {
      setErr(String(e));
    } finally {
      setSaving(false);
    }
  };

  const handleReset = async (): Promise<void> => {
    resetWeights();
    setSaving(true);
    setErr(null);
    try {
      const r = await ipc.routerSetWeights(DEFAULT_WEIGHTS);
      setWeights(r.weights);
      setHint('✓ 已恢复默认权重（0.35 / 0.25 / 0.20 / 0.15 / 0.05）');
    } catch (e) {
      setErr(String(e));
    } finally {
      setSaving(false);
    }
  };

  return (
    <div className="rounded border p-4" style={{ backgroundColor: '#f3f3f3', borderColor: '#d4d4d4' }}>
      <h3 className="mb-2 text-ui font-semibold" style={{ color: '#1f1f1f' }}>
        📊 评分权重
      </h3>
      <p className="mb-3 text-2xs" style={{ color: '#616161' }}>
        五维评分权重之和需等于 1，保存后后端热生效。
      </p>

      {err && (
        <div className="mb-2 rounded px-2 py-1 text-2xs" style={{ backgroundColor: '#3c1e1e', color: '#cd3131' }}>
          {err}
        </div>
      )}
      {hint && (
        <div className="mb-2 text-2xs" style={{ color: '#059669' }}>
          {hint}
        </div>
      )}

      {DIMENSIONS.map((dim) => (
        <div key={dim.key} className="mb-3">
          <div className="mb-1 flex items-center justify-between">
            <span className="text-2xs" style={{ color: dim.color }}>{dim.label}</span>
            <span className="font-mono text-2xs" style={{ color: '#1f1f1f' }}>
              {weights[dim.key].toFixed(2)}
            </span>
          </div>
          <input
            type="range"
            min="0"
            max="1"
            step="0.05"
            value={weights[dim.key]}
            onChange={(e) => setWeights({ [dim.key]: Number(e.target.value) })}
            className="w-full"
          />
        </div>
      ))}
      <div
        className="mt-2 flex items-center justify-between border-t pt-2"
        style={{ borderColor: '#d4d4d4' }}
      >
        <span className="text-2xs" style={{ color: '#616161' }}>
          合计 {total.toFixed(2)}{' '}
          {!sumValid && <span style={{ color: '#cd3131' }}>（应为 1.00）</span>}
        </span>
        <div className="flex gap-2">
          <button
            type="button"
            onClick={() => void handleReset()}
            disabled={saving}
            className="rounded px-2 py-0.5 text-2xs"
            style={{
              backgroundColor: saving ? '#ececec' : '#fdeaea',
              color: saving ? '#616161' : '#cd3131',
              cursor: saving ? 'not-allowed' : 'pointer',
            }}
          >
            恢复默认
          </button>
          <button
            type="button"
            onClick={() => void handleSave()}
            disabled={saving || !sumValid}
            className="rounded px-2 py-0.5 text-2xs"
            style={{
              backgroundColor: saving || !sumValid ? '#ececec' : '#0e639c',
              color: saving || !sumValid ? '#616161' : '#ffffff',
              cursor: saving || !sumValid ? 'not-allowed' : 'pointer',
            }}
          >
            {saving ? '保存中…' : '保存'}
          </button>
        </div>
      </div>
    </div>
  );
}