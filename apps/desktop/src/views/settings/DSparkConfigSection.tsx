/**
 * DSparkConfigSection —— 摘要卡（Phase 13 V0）。
 *
 * 设计意图：编辑能力收归 DSparkSettingsPanel（/settings/dspark）。
 * 本组件只读展示：草稿模型路径 + 全局开关 + 本次会话决策数 + DSpark 启用率。
 * 一键「打开 DSpark 设置」跳转到顶级设置页，避免双源真相。
 *
 * 文档：[docs/design/phase-13-dspark.md](../../../../docs/design/phase-13-dspark.md)
 */
import { useEffect, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import type { DSparkRuntimeConfig } from '@eaide/shared-protocol';
import { ipc } from '@/ipc/invoke';

export function DSparkConfigSection(): JSX.Element {
  const [cfg, setCfg] = useState<DSparkRuntimeConfig | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const navigate = useNavigate();

  useEffect(() => {
    void (async () => {
      try {
        const c = await ipc.dsparkGetConfig();
        setCfg(c);
      } catch (e) {
        setErr(String(e));
      }
    })();
  }, []);

  return (
    <div
      className="mt-4 rounded border p-4"
      style={{ backgroundColor: '#f3f3f3', borderColor: '#d4d4d4' }}
    >
      <div className="mb-3 flex items-center justify-between">
        <h3 className="text-ui font-semibold" style={{ color: '#1f1f1f' }}>
          ⚡ 推测解码（DSpark）
        </h3>
        <button
          type="button"
          onClick={() => navigate('/settings/dspark')}
          className="rounded px-2.5 py-0.5 text-2xs"
          style={{ backgroundColor: '#0e639c', color: '#ffffff' }}
          title="打开 DSpark 完整设置（草稿模型路径 / 上下文 / GPU / 策略表）"
        >
          打开设置 →
        </button>
      </div>

      <p className="mb-3 text-2xs" style={{ color: '#616161' }}>
        V0: 决策层（4 字段注入 RouteDecision）+ 12 条策略表。
        <span style={{ color: '#795e26' }}> 编辑能力收归顶级设置页</span>，本卡片仅供快速浏览。
      </p>

      {err && (
        <div
          className="mb-3 rounded px-2 py-1 text-2xs"
          style={{ backgroundColor: '#3c1e1e', color: '#cd3131' }}
        >
          {err}
        </div>
      )}

      {cfg && (
        <div className="mb-1 grid grid-cols-2 gap-2 text-2xs">
          <KV
            k="草稿模型"
            v={cfg.draft_model_path ?? '（未配置）'}
            color={cfg.draft_model_path ? '#059669' : '#616161'}
          />
          <KV
            k="全局开关"
            v={cfg.enable_global ? '✓ 开' : '✗ 关'}
            color={cfg.enable_global ? '#059669' : '#cd3131'}
          />
          <KV
            k="上下文窗口"
            v={`${cfg.context_size.toLocaleString()} tokens（V1 加载生效）`}
            color="#9cdcfe"
          />
          <KV
            k="GPU 层数"
            v={
              cfg.gpu_layers === -1
                ? '全部'
                : cfg.gpu_layers === 0
                  ? '纯 CPU'
                  : `${cfg.gpu_layers} 层`
            }
            color="#9cdcfe"
          />
          <KV
            k="本次会话决策"
            v={`${cfg.stats.total_decisions}（重启归零）`}
            color="#9cdcfe"
          />
          <KV
            k="DSpark 启用率"
            v={`${cfg.stats.dspark_enabled_pct.toFixed(1)}%`}
            color="#4ec9b0"
          />
        </div>
      )}
    </div>
  );
}

function KV({ k, v, color }: { k: string; v: string; color?: string }): JSX.Element {
  return (
    <div className="flex items-center gap-2">
      <span style={{ color: '#616161' }}>{k}</span>
      <span className="truncate font-mono" style={{ color: color ?? '#1f1f1f' }} title={v}>
        {v}
      </span>
    </div>
  );
}