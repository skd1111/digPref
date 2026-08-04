/**
 * useBiznavEvents —— React hook：订阅 3 个 BIZNAV_* SSE 事件，
 * 触发 biznavStore 自动刷新 + 简易 toast 提示。
 *
 * Phase 2G V1.3 (2026-07-28): SSE 三处同步上线
 *   - Python `agent.biznav.events.emit_biznav_event()` (后端后台任务)
 *   - Python `graph/stream.py::_drain_biznav_events()` (流循环消费)
 *   - Rust `stream/sse_bridge.rs::channel::BIZNAV_*` (转发到 Tauri Event)
 *   - TS `ipc/events.ts::EVT.BIZNAV_*` (本 hook 订阅)
 *
 * 触发动作：
 *   - BIZNAV_YAML_RELOADED → toast + 触发 biznavStore.loadFeatures
 *   - BIZNAV_FEATURE_AFFECTED → toast (受影响 feature 数)
 *   - BIZNAV_EXTRACTION_DONE → toast (success/error) + 触发 biznavStore.loadFeatures
 *
 * 不破坏 V0 UX：后端未就绪时事件不推送，hook 静默无副作用。
 */
import { useEffect } from 'react';
import { listen, EVT } from '@/ipc/events';
import { useBiznavStore } from '@/store/biznavStore';

// 极简 toast —— 不引入新依赖。V1.5 会接全局 Toast 组件（暂未实装）
function showToast(message: string, kind: 'info' | 'success' | 'error' = 'info') {
  if (typeof window === 'undefined') return;
  // eslint-disable-next-line no-console
  console.log(`[biznav toast][${kind}]`, message);
}

interface BiznavYamlReloaded {
  project_name: string;
  yaml_path?: string;
  success: boolean;
  inserted?: number;
  updated?: number;
  skipped?: number;
  conflicts?: number;
  error?: string;
  ts: number;
}

interface BiznavFeatureAffected {
  project_name: string;
  affected: Array<{ feature_id: string; files: string[] }>;
  ts: number;
}

interface BiznavExtractionDone {
  job_id: number;
  project_name: string;
  success: boolean;
  features_generated: number;
  error?: string | null;
  ts: number;
}

export function useBiznavEvents(): void {
  const loadFeatures = useBiznavStore((s) => s.loadFeatures);

  useEffect(() => {
    const unlistenPromises: Array<Promise<() => void>> = [];

    // 1. YAML 热加载完成 → 自动刷新 feature 列表
    unlistenPromises.push(
      listen<BiznavYamlReloaded>(EVT.BIZNAV_YAML_RELOADED, (e) => {
        const p = e.payload;
        if (!p?.success) {
          showToast(
            `YAML 重载失败: ${p?.error ?? 'unknown'}（DB 未变更）`,
            'error'
          );
          return;
        }
        const inserted = p.inserted ?? 0;
        const updated = p.updated ?? 0;
        showToast(
          `YAML 已重新加载 · 新增 ${inserted} · 更新 ${updated} · 冲突 ${p.conflicts ?? 0}`,
          'success'
        );
        // 自动刷新列表（用 project_name 拿当前 store 的 projectName；事件内 project_name 优先）
        void loadFeatures({ project_name: p.project_name });
      }),
    );

    // 2. 文件变更影响 feature → toast 提示
    unlistenPromises.push(
      listen<BiznavFeatureAffected>(EVT.BIZNAV_FEATURE_AFFECTED, (e) => {
        const p = e.payload;
        const count = p?.affected?.length ?? 0;
        if (count === 0) return;
        showToast(
          `${count} 个业务功能点受文件变更影响（请检查一致性）`,
          'info'
        );
      }),
    );

    // 3. 后台 extract 任务完成 → 自动刷新
    unlistenPromises.push(
      listen<BiznavExtractionDone>(EVT.BIZNAV_EXTRACTION_DONE, (e) => {
        const p = e.payload;
        if (p?.success) {
          showToast(
            `业务功能点提取完成 · ${p.features_generated ?? 0} 个候选`,
            'success'
          );
        } else {
          showToast(
            `业务功能点提取失败: ${p?.error ?? 'unknown'}`,
            'error'
          );
        }
        void loadFeatures({ project_name: p?.project_name });
      }),
    );

    return () => {
      void Promise.all(unlistenPromises).then((fns) => {
        fns.forEach((fn) => {
          try {
            fn();
          } catch {
            // best-effort 卸载
          }
        });
      });
    };
  }, [loadFeatures]);
}