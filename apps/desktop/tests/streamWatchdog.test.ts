/**
 * SSE 流看门狗护栏（BUGFIX #161，2026-08-27）。
 *
 * 实测案例：HITL 审批后 SSE 连接静默断开，后端图任务随流被取消，
 * done/error 永远到不了前端 → busy 永久锁死、审批决策成孤儿。
 * 看门狗职责：流静默超阈（心跳 15s 一条，8 条缺席）且仍 busy → 主动解锁。
 */
import { describe, expect, it } from 'vitest';
import {
  WATCHDOG_SILENCE_MS,
  WATCHDOG_TICK_MS,
  noteStreamEvent,
  runWatchdogTick,
  shouldReleaseStuckRuns,
  streamSilenceMs,
} from '@/lib/streamWatchdog';

describe('SSE 流看门狗（BUGFIX #161）', () => {
  it('阈值合理：静默判定 > 心跳间隔的 4 倍，巡检周期小于静默阈值', () => {
    expect(WATCHDOG_SILENCE_MS).toBeGreaterThan(15_000 * 4);
    expect(WATCHDOG_TICK_MS).toBeLessThan(WATCHDOG_SILENCE_MS);
  });

  it('事件到达刷新存活时间戳：静默归零', () => {
    noteStreamEvent(1_000_000);
    expect(streamSilenceMs(1_000_000)).toBe(0);
    expect(streamSilenceMs(1_010_000)).toBe(10_000);
  });

  it('无 busy 页签时永不触发释放（空闲期无事件是正常的）', () => {
    noteStreamEvent(0);
    expect(
      shouldReleaseStuckRuns({ busyTabCount: 0, silenceMs: WATCHDOG_SILENCE_MS + 1 }),
    ).toBe(false);
  });

  it('busy 但静默未超阈 → 不释放（心跳仍在到达）', () => {
    expect(
      shouldReleaseStuckRuns({ busyTabCount: 1, silenceMs: WATCHDOG_SILENCE_MS - 1 }),
    ).toBe(false);
  });

  it('busy 且静默超阈 → 判定断连', () => {
    expect(
      shouldReleaseStuckRuns({ busyTabCount: 2, silenceMs: WATCHDOG_SILENCE_MS + 1 }),
    ).toBe(true);
  });

  it('巡检一拍：满足条件调 releaseAll 并返回 true，否则不动作', () => {
    noteStreamEvent(5_000_000);
    let released = 0;
    const deps = {
      busyTabCount: () => 1,
      releaseAll: () => {
        released += 1;
      },
    };
    // 静默未超阈 → 不动作
    expect(runWatchdogTick({ ...deps, now: 5_000_000 + WATCHDOG_SILENCE_MS })).toBe(false);
    expect(released).toBe(0);
    // 静默超阈 → 释放
    expect(runWatchdogTick({ ...deps, now: 5_000_000 + WATCHDOG_SILENCE_MS + 1 })).toBe(true);
    expect(released).toBe(1);
  });

  it('心跳到达可阻止释放（断连判定只认事件缺席）', () => {
    let released = 0;
    const deps = {
      busyTabCount: () => 1,
      releaseAll: () => {
        released += 1;
      },
    };
    // 模拟连续心跳到达：每次巡检前刚收到心跳
    for (let t = 0; t < 10; t += 1) {
      const now = 10_000_000 + t * 15_000;
      noteStreamEvent(now);
      expect(runWatchdogTick({ ...deps, now: now + WATCHDOG_TICK_MS - 1 })).toBe(false);
    }
    expect(released).toBe(0);
  });
});
