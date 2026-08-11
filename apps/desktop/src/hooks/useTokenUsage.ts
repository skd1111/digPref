/**
 * useTokenUsage —— React hook：轮询 Token 用量快照（经 Rust token_usage_get 命令
 * 转发 GET /llm/token-usage），供 TopBar / StatusBar「Agent: 就绪」旁实时展示。
 *
 * 展示内容：
 *   - 实时速率（区分上传 prompt / 下载 completion，后端 30s 滑动窗口均值）
 *   - 当日总量（后端落 router.db，跨重启保留，按日滚动）
 *
 * 生命周期：挂载即拉一次 + 每 2s 轮询；Agent 未就绪 / 请求失败静默保留上次值
 * （与 useAgentHealth 的静默降级风格一致，不打扰用户）。
 */
import { useEffect, useState } from 'react';
import { ipc } from '@/ipc/invoke';
import type { TokenUsageSnapshot } from '@/ipc/invoke';

const POLL_INTERVAL_MS = 2_000;

export function useTokenUsage(): TokenUsageSnapshot | null {
  const [snapshot, setSnapshot] = useState<TokenUsageSnapshot | null>(null);

  useEffect(() => {
    let disposed = false;

    const tick = async (): Promise<void> => {
      try {
        const s = await ipc.tokenUsageGet();
        if (!disposed) setSnapshot(s);
      } catch {
        /* Agent 未就绪 / 网络失败 → 静默（保留上次快照） */
      }
    };

    void tick();
    const timer = setInterval(() => void tick(), POLL_INTERVAL_MS);
    return () => {
      disposed = true;
      clearInterval(timer);
    };
  }, []);

  return snapshot;
}
