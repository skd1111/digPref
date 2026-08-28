/**
 * RightTraceView —— 「控制台」面板。
 *
 * Phase 16：开发模式的「执行链路」升级为「思维链」（ThinkingChainPanel）：
 *   中文思考过程 + 文件操作徽章 + hover diff 预览。
 *   模式隔离：仅 mode='full' 渲染思维链；其他模式后端照常记录
 *   thinking_steps（金融合规审计），前端回退旧执行链路。
 *
 * 面板内容（仅两类，其余不展示）：
 *   1. 会话思维链 —— 启动时自动加载最近会话；实时会话经 SSE 切换
 *   2. AI 解释 —— 仅在用户手动选区点「AI 解释」后才出现（无内容时整块隐藏）
 *
 * 新条目推入后自动滚到底（贴底跟随：用户上滚回看时不打断，2026-08-25；
 * BUGFIX #151：跟随意图改由滚动事件记录 —— 防批量刷新一次增高 >80px 时
 * 「渲染后才量距」误判为未贴底、永久断随）。
 */
import { useEffect, useRef, useState, useMemo } from 'react';
import { ExecutionTrace } from '@/components/trace/ExecutionTrace';
import { ThinkingChainPanel } from '@/components/thinking/ThinkingChainPanel';
import { isMockSource, isMockText } from '@/lib/mockFilter';
import { useThinkingStore } from '@/store/thinkingStore';
import { useTraceStore } from '@/store/traceStore';
import { useUIStore } from '@/store/uiStore';

export function RightTraceView(): JSX.Element {
  const rawConsole = useTraceStore((s) => s.consoleEntries);
  const clearConsole = useTraceStore((s) => s.clearConsole);
  const mode = useUIStore((s) => s.mode);
  // 思维链步数变化 = 新条目到达 → 触发外层滚动（此前只依赖下方代码解释条目，
  // 思维链超屏后新增不跟随，2026-08-25）
  const thinkingStepCount = useThinkingStore((s) => s.steps.length);
  const thinkingLoading = useThinkingStore((s) => s.loading);
  const scrollRef = useRef<HTMLDivElement>(null);
  const contentRef = useRef<HTMLDivElement>(null);
  // 贴底跟随意图（BUGFIX #151）：由用户滚动事件实时记录，而非在新内容渲染后才量距——
  // 防抖刷新一次拉回多条长卡片（单次增高 >80px）时，旧方案把「被内容撑离底部」
  // 误判成「用户上滚」，从此不再跟随（实测：思维链停在半屏，新条目撞出视口）。
  const stickToBottom = useRef(true);
  const handleScroll = (): void => {
    const el = scrollRef.current;
    if (!el) return;
    stickToBottom.current = el.scrollHeight - el.scrollTop - el.clientHeight < 80;
  };
  // 渲染层兜底：
  //   1. 控制台不显示任何 mock 数据（含历史条目）
  //   2. 只展示手动触发的 AI 解释（codenav.explain）；agent 运行时日志不入控制台
  const consoleEntries = useMemo(
    () =>
      rawConsole.filter(
        (e) => e.category === 'codenav.explain' && !isMockSource(e.source) && !isMockText(e.text),
      ),
    [rawConsole],
  );

  // Phase 12 V1 自动展开最新一条「ok」entry（不让用户手动找）
  const lastOkId = useMemo(() => {
    for (let i = consoleEntries.length - 1; i >= 0; i--) {
      if (consoleEntries[i].status === 'ok') return consoleEntries[i].id;
    }
    return null;
  }, [consoleEntries]);
  // 用户手动折叠过的 id（避免被 React 重渲染强制展开）
  const [collapsedOverride, setCollapsedOverride] = useState<Set<string>>(new Set());
  const handleToggle = (id: string, open: boolean): void => {
    if (!open) {
      // 用户手动折叠 → 加入 override（除非它是最新 ok，那强制展开）
      if (id !== lastOkId) {
        setCollapsedOverride((prev) => new Set(prev).add(id));
      }
    } else {
      setCollapsedOverride((prev) => {
        const next = new Set(prev);
        next.delete(id);
        return next;
      });
    }
  };

  // 新条目 / 流式增量 / 思维链刷新时自动滚到底；用户上滚回看时不强制拉回。
  useEffect(() => {
    const el = scrollRef.current;
    if (!el) return;
    if (stickToBottom.current) {
      el.scrollTop = el.scrollHeight;
    }
  }, [consoleEntries, thinkingStepCount, thinkingLoading]);

  // 内容增长兑底（#151）：卡片展开/折叠、字体/异步渲染等不触发上方依赖的
  // 高度变化也由 ResizeObserver 接住，贴底意图存续期间持续钉底。
  useEffect(() => {
    const target = contentRef.current;
    const scroller = scrollRef.current;
    if (!target || !scroller) return;
    const ro = new ResizeObserver(() => {
      if (stickToBottom.current) scroller.scrollTop = scroller.scrollHeight;
    });
    ro.observe(target);
    return () => ro.disconnect();
  }, []);

  return (
    <div className="flex h-full flex-col">
      <header
        className="flex items-center justify-between border-b px-3 py-2 text-sm font-semibold"
        style={{ color: '#1f1f1f' }}
      >
        <span>控制台 · 执行过程</span>
        <button
          type="button"
          onClick={() => clearConsole()}
          disabled={consoleEntries.length === 0}
          title="清空控制台"
          className="rounded px-2 py-0.5 text-2xs"
          style={{
            backgroundColor: '#ececec',
            color: consoleEntries.length === 0 ? '#616161' : '#333333',
            cursor: consoleEntries.length === 0 ? 'not-allowed' : 'pointer',
          }}
        >
          清空
        </button>
      </header>

      <div ref={scrollRef} onScroll={handleScroll} className="flex-1 overflow-auto p-2">
        <div ref={contentRef}>
        {/* 上半：Phase 16 思维链（仅开发模式；其他模式后端照常记录但不显示） */}
        <div className="mb-3">
          <div className="mb-1 text-2xs font-semibold uppercase tracking-wider" style={{ color: '#616161' }}>
            {mode === 'full' ? '执行过程' : '执行链路'}
          </div>
          {mode === 'full' ? <ThinkingChainPanel /> : <ExecutionTrace />}
        </div>

        {/* 下半：代码解释 —— 仅在用户手动选区点解释后出现，无内容时整块隐藏 */}
        {consoleEntries.length > 0 && (
          <div>
            <div className="mb-1 text-2xs font-semibold uppercase tracking-wider" style={{ color: '#616161' }}>
              代码解释（{consoleEntries.length}）
            </div>
            <ConsoleLogList
              entries={consoleEntries}
              lastOkId={lastOkId}
              collapsedOverride={collapsedOverride}
              onToggle={handleToggle}
            />
          </div>
        )}
        </div>
      </div>
    </div>
  );
}

// ---- AI 解释 / agent 日志子组件 -------------------------------------

const CATEGORY_COLORS: Record<string, string> = {
  'codenav.explain': '#c586c0',
  'codenav.jump': '#059669',
  intent: '#059669',
  plan: '#0451a5',
  repair: '#795e26',
  decompose: '#c586c0',
  tool_call: '#0b6bcb',
  tool_result: '#0b6bcb',
  log: '#616161',
};

const STATUS_ICON: Record<string, string> = {
  running: '⏳',
  ok: '✓',
  err: '✗',
};

function ConsoleLogList({
  entries,
  lastOkId,
  collapsedOverride,
  onToggle,
}: {
  entries: ReturnType<typeof useTraceStore.getState>['consoleEntries'];
  lastOkId: string | null;
  collapsedOverride: Set<string>;
  onToggle: (id: string, open: boolean) => void;
}): JSX.Element {
  if (entries.length === 0) {
    // 无手动触发的 AI 解释 → 不展示任何占位文案
    return <></>;
  }
  return (
    <div className="space-y-1">
      {entries.map((e) => (
        <ConsoleLogRow
          key={e.id}
          entry={e}
          autoOpen={e.id === lastOkId && !collapsedOverride.has(e.id)}
          onToggle={onToggle}
        />
      ))}
    </div>
  );
}

function ConsoleLogRow({
  entry,
  autoOpen,
  onToggle,
}: {
  entry: ReturnType<typeof useTraceStore.getState>['consoleEntries'][number];
  autoOpen: boolean;
  onToggle: (id: string, open: boolean) => void;
}): JSX.Element {
  const color = CATEGORY_COLORS[entry.category] ?? '#616161';
  const icon = STATUS_ICON[entry.status ?? 'ok'] ?? '✓';
  const latency = entry.latencyMs != null ? ` · ${entry.latencyMs}ms` : '';
  const confidence = entry.confidence != null ? ` · ${(entry.confidence * 100).toFixed(0)}%` : '';
  const ts = new Date(entry.ts).toLocaleTimeString();

  return (
    <details
      open={entry.status === 'running' || autoOpen || undefined}
      onToggle={(e) => onToggle(entry.id, (e.target as HTMLDetailsElement).open)}
      className="rounded font-mono text-2xs"
      style={{ backgroundColor: '#f3f3f3', borderLeft: `3px solid ${color}` }}
    >
      <summary
        className="flex cursor-pointer items-center gap-2 px-3 py-1.5 hover:bg-[#2a2d2e]"
        style={{ color: '#1f1f1f' }}
      >
        <span className="flex-shrink-0 font-bold" style={{ color }}>
          {icon}
        </span>
        <span
          className="flex-shrink-0 font-semibold uppercase tracking-wider"
          style={{ color }}
        >
          {entry.category}
        </span>
        {entry.symbol && (
          <span className="flex-shrink-0 font-mono" style={{ color: '#0b6bcb' }}>
            {entry.symbol}
          </span>
        )}
        <span className="flex-1 truncate">{entry.text}</span>
        <span className="flex-shrink-0" style={{ color: '#616161' }}>
          {latency}
          {confidence}
        </span>
        <span className="flex-shrink-0" style={{ color: '#616161' }}>
          {ts}
        </span>
      </summary>
      {/* 展开区：完整 text + 来源信息 */}
      <div
        className="border-t px-3 py-2"
        style={{ borderColor: '#e0e0e0', color: '#6e6e6e' }}
      >
        <pre className="whitespace-pre-wrap break-all font-mono text-[11px]" style={{ color: '#1f1f1f' }}>
          {entry.fullText ?? entry.text}
        </pre>
        <div className="mt-1 flex flex-wrap gap-x-3 text-[10px]" style={{ color: '#6a6a6a' }}>
          {entry.source && <span>source={entry.source}</span>}
          {entry.backend && <span>backend={entry.backend}</span>}
          {entry.confidence != null && <span>confidence={(entry.confidence * 100).toFixed(1)}%</span>}
        </div>
      </div>
    </details>
  );
}
