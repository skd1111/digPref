/**
 * useAgentHealth —— React hook：轮询 Agent /health（经 Rust agent_wait_ready 命令），
 * 把真实连通性写回 uiStore.agentStatus。
 *
 * 背景 bug：agentStatus 初始值为 'unknown'，此前全工程无任何调用方更新它，
 * 导致 TopBar / StatusBar 永远显示「Agent: 未连接」。
 *
 * 状态映射：
 *   chatStore.busy === true            → 'busy'（处理中…）
 *   /health 返回 2xx                    → 'ready'（就绪）
 *   /health 不可达                      → 'error'（出错）
 *
 * 生命周期：组件挂载时启动轮询（首次 1.5s，之后每 5s），卸载时清理。
 */
import { useEffect } from 'react';
import { ipc } from '@/ipc/invoke';
import { useUIStore } from '@/store/uiStore';
import { useChatStore } from '@/store/chatStore';

const POLL_INTERVAL_MS = 5_000;
const FIRST_DELAY_MS = 1_500;

export function useAgentHealth(): void {
  useEffect(() => {
    let disposed = false;
    let timer: ReturnType<typeof setTimeout> | undefined;

    const check = async (): Promise<void> => {
      if (disposed) return;
      try {
        // timeoutS=3：单次探测上限 3s，避免轮询被长时间阻塞
        const r = await ipc.agentWaitReady(3);
        if (!disposed) {
          useUIStore
            .getState()
            .setAgentStatus(useChatStore.getState().busy ? 'busy' : r.ready ? 'ready' : 'error');
        }
      } catch {
        if (!disposed) {
          useUIStore
            .getState()
            .setAgentStatus(useChatStore.getState().busy ? 'busy' : 'error');
        }
      }
      if (!disposed) timer = setTimeout(() => void check(), POLL_INTERVAL_MS);
    };

    timer = setTimeout(() => void check(), FIRST_DELAY_MS);

    // chatStore.busy 变化即时同步 → 状态栏「处理中…」不依赖下一轮轮询
    const unsub = useChatStore.subscribe((state, prev) => {
      if (state.busy !== prev.busy) {
        useUIStore.getState().setAgentStatus(state.busy ? 'busy' : 'idle');
      }
    });

    return () => {
      disposed = true;
      if (timer) clearTimeout(timer);
      unsub();
    };
  }, []);
}
