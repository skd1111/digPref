/**
 * AgentReadyGate —— Agent 未就绪期间的「整屏模糊 + 禁交互」启动闸门。
 *
 * 问题：Tauri 开窗比 Python Agent(:8765) 冷启动快数秒（打包态可达 30s+），
 * 这段时间界面看着正常但点什么都失败，用户容易在空窗期乱点，制造一堆
 * connection refused 的错误横幅，误以为软件坏了。
 *
 * 做法（2026-09-04）：
 *   1. 挂载即触发 lib/agentBoot.ts 的阻塞等待（Rust agent_wait_ready，
 *      /health 一通立刻返回），就绪前 uiStore.agentBootState !== 'ready'；
 *   2. body 挂 .agent-booting → globals.css 把 #root 整体 blur + pointer-events:none，
 *      遮罩卡片本身走 createPortal 挂到 body，不在 #root 里，所以不受模糊影响；
 *   3. capture 阶段拦下全局快捷键（Ctrl+Shift+P 等），并把跑出遮罩的焦点弹回，
 *      遮罩内的按键（按钮 Enter/Space、日志选中复制）放行；
 *   4. 首轮超时/异常 → 卡片切失败态，给出重试 / 重启 Agent / 查看日志 /
 *      跳过闸门四个出口，同时后台每 3s 复探，Agent 起来后自动放行。
 *
 * 就绪后组件返回 null 且移除 body 类名，对布局与既有交互零影响。
 */
import { useEffect, useLayoutEffect, useRef, useState, type CSSProperties } from 'react';
import { createPortal } from 'react-dom';
import { useUIStore } from '@/store/uiStore';
import {
  BOOT_FIRST_TIMEOUT_S,
  BOOT_REPROBE_INTERVAL_MS,
  readAgentLogTail,
  restartAgentForGate,
  runAgentBootGate,
  skipAgentBootGate,
  stopAgentBootReprobe,
} from '@/lib/agentBoot';

/** body 上的闸门类名（与 globals.css 的 body.agent-booting 规则对应） */
const BOOTING_BODY_CLASS = 'agent-booting';

type BusyKind = 'retry' | 'restart';

export function AgentReadyGate(): JSX.Element | null {
  const bootState = useUIStore((s) => s.agentBootState);
  const bootError = useUIStore((s) => s.agentBootError);
  const bootElapsedMs = useUIStore((s) => s.agentBootElapsedMs);
  const gated = bootState !== 'ready';

  const cardRef = useRef<HTMLDivElement | null>(null);
  const [elapsed, setElapsed] = useState(0);
  const [busy, setBusy] = useState<BusyKind | null>(null);
  const [logOpen, setLogOpen] = useState(false);
  const [logTail, setLogTail] = useState<string | null>(null);
  const [logLoading, setLogLoading] = useState(false);

  // 挂载即启动闸门等待（去重在 agentBoot 内部：重试连点共享同一轮）；
  // 卸载时停掉失败态留下的后台复探定时器
  useEffect(() => {
    void runAgentBootGate();
    return () => stopAgentBootReprobe();
  }, []);

  // 已用时计时器：冷启动是真实进程拉起，让用户知道它活着而不是卡死
  // 依赖 bootState：失败后点「重试」回到 booting 时重新从 0 计
  useEffect(() => {
    if (!gated) {
      setElapsed(0);
      return;
    }
    setElapsed(0);
    const timer = setInterval(() => setElapsed((s) => s + 1), 1000);
    return () => clearInterval(timer);
  }, [gated, bootState]);

  // body 类名驱动模糊 + 禁点（#root 整棵子树），卸载时务必摘掉。
  // 用 layout effect：在浏览器首帧绘制前就挂上类名，避免刚开窗闪一帧可点的清晰界面。
  useLayoutEffect(() => {
    if (typeof document === 'undefined') return;
    document.body.classList.toggle(BOOTING_BODY_CLASS, gated);
    return () => {
      document.body.classList.remove(BOOTING_BODY_CLASS);
    };
  }, [gated]);

  // 闸门开启瞬间：把焦点从被模糊的界面收回遮罩卡片（否则光标还停在聊天输入框里）
  useEffect(() => {
    if (!gated || typeof document === 'undefined') return;
    const active = document.activeElement;
    if (active instanceof HTMLElement && !cardRef.current?.contains(active)) {
      active.blur();
    }
    cardRef.current?.focus();
  }, [gated]);

  // 键盘也要堵住：capture 阶段拦下全局快捷键与输入（window 上的 capture 监听
  // 先于 WorkspaceLayout 等处的 bubble 监听，stopPropagation 即可让其收不到）
  useEffect(() => {
    if (!gated || typeof window === 'undefined') return;
    const block = (e: KeyboardEvent): void => {
      const t = e.target;
      // 遮罩内的按键放行（重试/重启按钮的 Enter/Space、日志文本选中复制）
      if (t instanceof Node && cardRef.current?.contains(t)) return;
      e.preventDefault();
      e.stopPropagation();
    };
    window.addEventListener('keydown', block, true);
    window.addEventListener('keyup', block, true);
    return () => {
      window.removeEventListener('keydown', block, true);
      window.removeEventListener('keyup', block, true);
    };
  }, [gated]);

  // 焦点兜底：Tab 仍可能把焦点移进 #root（pointer-events 不管键盘导航），聚焦即弹回
  useEffect(() => {
    if (!gated || typeof document === 'undefined') return;
    const onFocusIn = (e: FocusEvent): void => {
      const t = e.target;
      if (t instanceof Node && cardRef.current && !cardRef.current.contains(t)) {
        if (t instanceof HTMLElement) t.blur();
        cardRef.current.focus();
      }
    };
    document.addEventListener('focusin', onFocusIn);
    return () => document.removeEventListener('focusin', onFocusIn);
  }, [gated]);

  if (!gated || typeof document === 'undefined') return null;

  const failed = bootState === 'failed';

  const onRetry = (): void => {
    setBusy('retry');
    setLogOpen(false);
    void runAgentBootGate().finally(() => setBusy(null));
  };

  const onRestart = (): void => {
    setBusy('restart');
    setLogOpen(false);
    void restartAgentForGate().finally(() => setBusy(null));
  };

  const onToggleLog = (): void => {
    const next = !logOpen;
    setLogOpen(next);
    if (!next) return;
    setLogLoading(true);
    void readAgentLogTail(60)
      .then((tail) => setLogTail(tail))
      .catch((e: unknown) =>
        setLogTail(`读取日志失败：${e instanceof Error ? e.message : String(e)}`),
      )
      .finally(() => setLogLoading(false));
  };

  const btn = (color: string): CSSProperties => ({
    borderColor: color,
    color,
    backgroundColor: 'transparent',
    cursor: 'pointer',
  });

  return createPortal(
    <div
      className="fixed inset-0 z-[9999] flex items-center justify-center px-6"
      style={{ backgroundColor: 'rgba(245,245,245,0.55)' }}
    >
      <div
        ref={cardRef}
        tabIndex={-1}
        role="dialog"
        aria-modal="true"
        aria-label={failed ? 'Agent 启动异常' : 'Agent 启动中'}
        className="w-[30rem] max-w-full rounded-lg border px-8 py-6 shadow-lg outline-none"
        style={{ borderColor: '#d4d4d4', backgroundColor: '#ffffff' }}
      >
        {failed ? (
          <>
            <p className="text-ui font-semibold" style={{ color: '#cd3131' }}>
              ✗ Agent 未就绪
            </p>
            <p className="mt-2 break-all text-2xs" style={{ color: '#616161' }}>
              {bootError ?? '未知原因'}
              {bootElapsedMs !== null && <span className="ml-1.5">（等待 {(bootElapsedMs / 1000).toFixed(1)}s）</span>}
            </p>
            <p className="mt-2 text-2xs" style={{ color: '#9e9e9e' }}>
              仍在每 {BOOT_REPROBE_INTERVAL_MS / 1000}s 自动复探，Agent 起来后会自动放行；
              也可手动重试或重启 Agent。
            </p>
            <div className="mt-4 flex flex-wrap items-center gap-2 text-2xs">
              <button
                type="button"
                onClick={onRetry}
                disabled={busy !== null}
                className="rounded border px-2.5 py-1"
                style={btn(busy === 'retry' ? '#9e9e9e' : '#b25c1a')}
              >
                {busy === 'retry' ? '等待中…' : `↻ 重试（再等 ${BOOT_FIRST_TIMEOUT_S}s）`}
              </button>
              <button
                type="button"
                onClick={onRestart}
                disabled={busy !== null}
                className="rounded border px-2.5 py-1"
                style={btn(busy === 'restart' ? '#9e9e9e' : '#1f1f1f')}
              >
                {busy === 'restart' ? '重启中…' : '⟳ 重启 Agent'}
              </button>
              <button
                type="button"
                onClick={onToggleLog}
                className="rounded border px-2.5 py-1"
                style={btn('#616161')}
              >
                {logOpen ? '▴ 收起日志' : '▾ 查看日志'}
              </button>
              <button
                type="button"
                onClick={skipAgentBootGate}
                className="ml-auto rounded border px-2.5 py-1"
                style={btn('#9e9e9e')}
                title="不推荐：Agent 未就绪时业务请求仍会失败"
              >
                跳过闸门
              </button>
            </div>
            {logOpen && (
              <pre
                className="mt-3 max-h-56 overflow-auto rounded p-3 text-2xs whitespace-pre-wrap"
                style={{ backgroundColor: '#1e1e1e', color: '#d4d4d4' }}
              >
                {logLoading ? '读取中…' : (logTail ?? '')}
              </pre>
            )}
          </>
        ) : (
          <div className="flex flex-col items-center text-center" role="status" aria-live="polite">
            <div
              className="agent-boot-spin h-7 w-7 rounded-full border-2"
              style={{ borderColor: '#e8e8e8', borderTopColor: '#b25c1a' }}
              aria-hidden
            />
            <p className="mt-3 text-ui font-semibold" style={{ color: '#1f1f1f' }}>
              正在启动 Agent…
            </p>
            <p className="mt-1 text-2xs" style={{ color: '#616161' }}>
              已用时 {elapsed}s
            </p>
            <p className="mt-2 text-2xs" style={{ color: '#9e9e9e' }}>
              首次启动需加载模型与工具目录，界面已临时锁定，就绪后自动可用（通常 5–30 秒）
            </p>
          </div>
        )}
      </div>
    </div>,
    document.body,
  );
}
