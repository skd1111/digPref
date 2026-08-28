/**
 * SSE 流看门狗（BUGFIX #161，2026-08-27）。
 *
 * 背景：HITL 等待 / 慢任务期间 SSE 连接若静默断开，后端图任务随流被取消，
 * done/error 到不了前端，busy 锁永久不释放（实测：审批决策成孤儿、连接消失、
 * 前端永远「思考中」）。后端无图块时每 15s 发具名 heartbeat 事件、业务事件
 * 照常到达；前端只要有任一事件就刷新时间戳，超过 WATCHDOG_SILENCE_MS 静默
 * 且仍处于 busy → 判定断连，主动解锁并提示用户。
 *
 * 纯函数设计便于单测；useAgentStream 负责接线（事件时间戳 + 定时 tick）。
 */

/** 静默判定阈值：后端心跳 15s 一条，连续 8 条缺席视为断连 */
export const WATCHDOG_SILENCE_MS = 120_000;

/** 巡检周期 */
export const WATCHDOG_TICK_MS = 30_000;

let lastEventAt = Date.now();

/** 任一 SSE 事件到达时调用，刷新流存活时间戳 */
export function noteStreamEvent(ts: number = Date.now()): void {
  lastEventAt = ts;
}

/** 距最近一次流事件的静默时长 */
export function streamSilenceMs(now: number = Date.now()): number {
  return now - lastEventAt;
}

/** 是否需要释放卡死的 run（纯判定） */
export function shouldReleaseStuckRuns(opts: {
  busyTabCount: number;
  silenceMs: number;
}): boolean {
  return opts.busyTabCount > 0 && opts.silenceMs > WATCHDOG_SILENCE_MS;
}

/**
 * 巡检一拍：静默超阈且有 busy 页签 → 调 releaseAll 解锁。
 * 返回本拍是否触发释放（测试断言用）。
 */
export function runWatchdogTick(deps: {
  busyTabCount: () => number;
  releaseAll: () => void;
  now?: number;
}): boolean {
  if (
    shouldReleaseStuckRuns({
      busyTabCount: deps.busyTabCount(),
      silenceMs: streamSilenceMs(deps.now),
    })
  ) {
    deps.releaseAll();
    return true;
  }
  return false;
}
