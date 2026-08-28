/**
 * ThinkingChainPanel —— 思维链时间线（Phase 16）。
 *
 * 取代开发模式的「执行链路」：垂直时间线展示 AI 的中文思考过程，
 * 文件操作渲染为 FileReferenceBadge（hover 预览 diff）。
 *
 * 数据流：
 *   - 每次启动面板为空（不自动加载历史会话）
 *   - SSE trace 事件（useAgentStream）→ thinkingStore.setSessionId(runId) 切到实时会话
 *   - traceStore.steps 变化（有新节点执行）→ 防抖 300ms refresh
 *   - done 事件 → 最终 refresh
 *
 * 模式隔离：**仅开发模式（mode='full'）挂载本组件**；其他模式后端照常
 * 记录 thinking_steps（金融合规审计），前端不渲染。
 */
import { useEffect, useRef, useState } from 'react';
import type { ThinkingStep } from '@eaide/shared-protocol';
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
  // SSE 执行链路 step 数量变化 = 后端有新节点执行 → 触发思维链刷新
  const traceCount = useTraceStore((s) => s.steps.length);
  const timerRef = useRef<ReturnType<typeof setTimeout> | null>(null);

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

  // 执行块点击跳转（BUGFIX #153）：按工具名/节点名定位步骤，滚到视口中央并闪烁 2s；
  // 匹配优先级：工具调用记录 > 节点名 > 思考文本包含。
  // occurrence（2026-08-27 树形合并）：同名工具多次调用时定位第 N 次命中（1 基，
  // 从早到晚），缺省回退最新一条（旧行为）。
  const highlight = useTraceStore((s) => s.highlight);
  const [flashId, setFlashId] = useState<string | null>(null);
  useEffect(() => {
    if (!highlight) return;
    const q = highlight.query.trim().toLowerCase();
    if (!q) return;
    const matches: ThinkingStep[] = [];
    for (const st of steps) {
      const hitTool = (st.tool_calls ?? []).some(
        (tc) => tc.name.toLowerCase() === q,
      );
      const hitNode = st.node_name.toLowerCase() === q;
      const hitText = `${st.thinking ?? ''}${st.decision ?? ''}`
        .toLowerCase()
        .includes(q);
      if (hitTool || hitNode || hitText) matches.push(st);
    }
    if (matches.length === 0) return;
    const nth = highlight.occurrence;
    const target =
      nth != null && nth >= 1 && nth <= matches.length
        ? matches[nth - 1]
        : matches[matches.length - 1];
    document
      .getElementById(`think-step-${target.id}`)
      ?.scrollIntoView({ block: 'center', behavior: 'smooth' });
    setFlashId(target.id);
    const timer = setTimeout(() => setFlashId(null), 2000);
    return () => clearTimeout(timer);
  }, [highlight, steps]);

  if (!sessionId && steps.length === 0) {
    // 无历史会话也无实时会话 → 留白，不展示占位文案
    return <div />;
  }

  // 不自带滚动容器（2026-08-25）：此前内层 h-full 在非固定高度父容器里塌陷，
  // 条目实际撞进外层 RightTraceView 滚动区，内层「滚到底」全空转；
  // 自动滚动上移至外层（含贴底跟随，见 RightTraceView）。
  return (
    <div>
      <ol className="space-y-2 p-2">
        {steps.map((step) => (
          <ThinkingStepCard key={step.id} step={step} flash={flashId === step.id} />
        ))}
      </ol>
      {loading && (
        <div className="px-3 pb-2 text-2xs" style={{ color: '#616161' }}>
          ⏳ 执行过程记录中…
        </div>
      )}
    </div>
  );
}
