/**
 * ThinkingChainPanel —— 思维链时间线（Phase 16）。
 *
 * 取代开发模式的「执行链路」：垂直时间线展示 AI 的中文思考过程，
 * 文件操作渲染为 FileReferenceBadge（hover 预览 diff）。
 *
 * 数据流：
 *   - 挂载时 → loadLatestSession() 自动加载最近会话的思维链（Agent 未就绪时静默重试）
 *   - SSE trace 事件（useAgentStream）→ thinkingStore.setSessionId(runId) 切到实时会话
 *   - traceStore.steps 变化（有新节点执行）→ 防抖 300ms refresh
 *   - done 事件 → 最终 refresh
 *
 * 模式隔离：**仅开发模式（mode='full'）挂载本组件**；其他模式后端照常
 * 记录 thinking_steps（金融合规审计），前端不渲染。
 */
import { useEffect, useRef } from 'react';
import { useThinkingStore } from '@/store/thinkingStore';
import { useTraceStore } from '@/store/traceStore';
import { ThinkingStepCard } from './ThinkingStepCard';

/** SSE 活动 → 刷新的防抖窗口（避免节点密集执行时疯狂请求） */
const REFRESH_DEBOUNCE_MS = 300;

export function ThinkingChainPanel(): JSX.Element {
  const steps = useThinkingStore((s) => s.steps);
  const sessionId = useThinkingStore((s) => s.sessionId);
  const loading = useThinkingStore((s) => s.loading);
  const refresh = useThinkingStore((s) => s.refresh);
  const loadLatestSession = useThinkingStore((s) => s.loadLatestSession);
  // SSE 执行链路 step 数量变化 = 后端有新节点执行 → 触发思维链刷新
  const traceCount = useTraceStore((s) => s.steps.length);
  const scrollRef = useRef<HTMLDivElement>(null);
  const timerRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  // 挂载时自动加载最近会话思维链；Agent 未就绪（启动竞态）时静默重试
  useEffect(() => {
    let cancelled = false;
    const timers: Array<ReturnType<typeof setTimeout>> = [];
    const tryLoad = async (attempt: number): Promise<void> => {
      await loadLatestSession();
      if (cancelled || useThinkingStore.getState().sessionId || attempt >= 10) return;
      timers.push(setTimeout(() => void tryLoad(attempt + 1), 3000));
    };
    void tryLoad(0);
    return () => {
      cancelled = true;
      timers.forEach((t) => clearTimeout(t));
    };
  }, [loadLatestSession]);

  // trace 活动防抖刷新
  useEffect(() => {
    if (!sessionId) return;
    if (timerRef.current) clearTimeout(timerRef.current);
    timerRef.current = setTimeout(() => {
      void refresh();
    }, REFRESH_DEBOUNCE_MS);
    return () => {
      if (timerRef.current) clearTimeout(timerRef.current);
    };
  }, [traceCount, sessionId, refresh]);

  // 新步骤追加时自动滚到底
  useEffect(() => {
    if (scrollRef.current) {
      scrollRef.current.scrollTop = scrollRef.current.scrollHeight;
    }
  }, [steps.length]);

  if (!sessionId && steps.length === 0) {
    // 无历史会话也无实时会话 → 留白，不展示占位文案
    return <div className="h-full" />;
  }

  return (
    <div ref={scrollRef} className="h-full overflow-auto">
      <ol className="space-y-2 p-2">
        {steps.map((step) => (
          <ThinkingStepCard key={step.id} step={step} />
        ))}
      </ol>
      {loading && (
        <div className="px-3 pb-2 text-2xs" style={{ color: '#616161' }}>
          ⏳ 思维链记录中…
        </div>
      )}
    </div>
  );
}
