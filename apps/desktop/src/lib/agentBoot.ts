/**
 * Agent 启动闸门（boot gate）逻辑 —— 判定「EAIDE 窗口已开但 Agent 还没就绪」的
 * 这段空窗期，并把结论写进 uiStore.agentBootState 供 AgentReadyGate 遮罩消费。
 *
 * 背景：Tauri 开窗比 Python Agent(:8765) 冷启动快数秒（打包态要解压依赖 +
 * 加载 onnx 模型，实测可达 30s+）。这段时间界面已可点，但任何业务请求都会
 * 拿到 `error sending request`（各 store 只能各自重试兜底），用户以为坏了就
 * 到处乱点，反而制造更多失败请求与错误横幅。
 *
 * 策略：就绪前整屏模糊 + 禁交互（见 components/chrome/AgentReadyGate.tsx 与
 * globals.css 的 body.agent-booting 规则），就绪瞬间自动放行。
 *
 * 与 hooks/useAgentHealth.ts 的分工：后者是 5s 一轮的状态栏健康轮询（探测粒度
 * 3s），用于长期连通性展示；这里是启动期的一次性阻塞等待（Rust 侧 500ms 一轮
 * 探测，/health 一通就立刻返回），就绪延迟远低于轮询，两者互不替代。
 */
import { ipc } from '@/ipc/invoke';
import { useUIStore } from '@/store/uiStore';
import { useChatStore } from '@/store/chatStore';

/**
 * 首轮阻塞等待上限（秒）。
 * 取 30s：与 Rust `agent_manager` spawn 后的健康检查预算（60 轮 × 500ms）对齐 ——
 * 到这时后端自己也已经记了「健康检查超时 (30s)」，遮罩转失败态正好能对上日志。
 * 超时不代表终止——转 'failed' 态给出重试/重启/日志/跳过四个出口，同时后台继续复探。
 */
export const BOOT_FIRST_TIMEOUT_S = 30;

/** 首轮失败后的后台复探间隔（毫秒）：Agent 起来后自动放行，无需用户点按钮 */
export const BOOT_REPROBE_INTERVAL_MS = 3_000;

/** 后台复探单次探测上限（秒）—— 与 useAgentHealth 一致，避免长阻塞 */
const REPROBE_TIMEOUT_S = 3;

/** 首轮等待的在飞 Promise（去重：重试按钮连点不会并发多个等待） */
let inflight: Promise<void> | null = null;

/** 后台复探定时器（就绪或重新进入首轮等待时清掉） */
let reprobeTimer: ReturnType<typeof setInterval> | null = null;

function stopReprobe(): void {
  if (reprobeTimer !== null) {
    clearInterval(reprobeTimer);
    reprobeTimer = null;
  }
}

/**
 * 停掉后台复探。闸门组件卸载时必须调一次（否则失败态留下的 3s 定时器会
 * 一直打 IPC）；就绪放行时内部已自动调过。
 */
export function stopAgentBootReprobe(): void {
  stopReprobe();
}

function markReady(elapsedMs: number | null): void {
  stopReprobe();
  const ui = useUIStore.getState();
  ui.setAgentBoot({ state: 'ready', elapsedMs });
  // 状态栏立刻转「Agent: 就绪」，不等 useAgentHealth 的下一轮（最长 5s）
  if (!useChatStore.getState().busy) ui.setAgentStatus('ready');
}

function markFailed(error: string, elapsedMs: number | null): void {
  useUIStore.getState().setAgentBoot({ state: 'failed', error, elapsedMs });
  startReprobe();
}

async function reprobe(): Promise<void> {
  try {
    const r = await ipc.agentWaitReady(REPROBE_TIMEOUT_S);
    if (r.ready) markReady(r.elapsed_ms ?? null);
  } catch {
    // 静默：下一轮再试（Agent 可能正在重启中）
  }
}

function startReprobe(): void {
  if (reprobeTimer !== null) return;
  reprobeTimer = setInterval(() => void reprobe(), BOOT_REPROBE_INTERVAL_MS);
}

/**
 * 启动闸门主流程：阻塞等 /health 2xx（最多 timeoutS 秒），就绪放行，
 * 超时/异常转 'failed' 并开启后台复探。并发调用共享同一轮等待。
 *
 * 由 AgentReadyGate 挂载时触发；失败态遮罩的「重试」按钮也调它。
 */
export function runAgentBootGate(timeoutS: number = BOOT_FIRST_TIMEOUT_S): Promise<void> {
  if (inflight) return inflight;
  stopReprobe();
  useUIStore.getState().setAgentBoot({ state: 'booting' });
  const p = (async (): Promise<void> => {
    try {
      const r = await ipc.agentWaitReady(timeoutS);
      if (r.ready) markReady(r.elapsed_ms ?? null);
      else markFailed(r.error ?? `Agent 未在 ${timeoutS}s 内就绪`, r.elapsed_ms ?? null);
    } catch (e) {
      // invoke 本身抛错（IPC 不可用 / 命令未注册）：同样转失败态，
      // 由遮罩给出重启与「跳过闸门」出口，绝不把界面永久锁死。
      markFailed(e instanceof Error ? e.message : String(e), null);
    } finally {
      inflight = null;
    }
  })();
  inflight = p;
  return p;
}

/**
 * 失败态遮罩的「重启 Agent」：杀掉 :8765 占用者让 Tauri 重新 spawn，
 * 再走一轮完整等待。重启命令本身失败也要继续等（旧进程可能已自行恢复）。
 */
export async function restartAgentForGate(): Promise<void> {
  stopReprobe();
  useUIStore.getState().setAgentBoot({ state: 'booting' });
  try {
    await ipc.agentRestartNow();
  } catch {
    // 忽略：下面的等待会给出真实结论
  }
  await runAgentBootGate();
}

/**
 * 失败态遮罩的「跳过闸门」：强制放行（IPC 不可用等非 Tauri 环境的逃生口）。
 * 同时停掉后台复探——用户已选择不要闸门，别再每 3s 打一次探测。
 */
export function skipAgentBootGate(): void {
  stopReprobe();
  useUIStore.getState().setAgentBoot({ state: 'ready' });
}

/** 失败态遮罩的「查看日志」：读 eaide.log 末尾若干行，帮用户自助定位 */
export async function readAgentLogTail(lines = 60): Promise<string> {
  const r = await ipc.agentReadLog(lines);
  if (!r.ok) return `读取日志失败（${r.path}）：${r.hint ?? '未知原因'}`;
  return r.tail.trim() === '' ? `（日志为空：${r.path}）` : r.tail;
}

/** 测试用：清掉模块级定时器与在飞 Promise（生产代码不调用） */
export function __resetAgentBootForTest(): void {
  stopReprobe();
  inflight = null;
}
