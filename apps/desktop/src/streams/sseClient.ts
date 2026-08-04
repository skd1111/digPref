/**
 * sseClient —— 浏览器端原生 EventSource 辅助工具。
 *
 * 用于在 Tauri shell 外部直接测试 FastAPI SSE 端点（开发/调试用）。
 * 生产环境通过 Rust SSE 桥代理（见 agentStream.ts）。
 *
 * 借鉴 Codex CLI 的 SSE 处理：同时注册命名事件和通用 onmessage 兜底。
 */
export interface SseMessage {
  event: string;
  data: unknown;
}

/**
 * 打开 SSE 连接，返回取消订阅函数。
 *
 * 同时注册命名事件监听器和通用 onmessage 处理器。
 * 未命名的 SSE 事件（服务端未设置 event: 字段）由 onmessage 兜底捕获。
 */
export function openSse(url: string, onMessage: (m: SseMessage) => void): () => void {
  const es = new EventSource(url);

  const handler = (e: MessageEvent): void => {
    let parsed: unknown = e.data;
    try {
      parsed = JSON.parse(e.data);
    } catch {
      /* 保持原始字符串 */
    }
    onMessage({ event: e.type, data: parsed });
  };

  // 注册所有已知的命名事件类型
  const namedEvents = [
    'message',
    'tool_call',
    'tool_result',
    'trace',
    'approval',
    'log',
    'done',
    'error',
  ];
  for (const evt of namedEvents) {
    es.addEventListener(evt, handler);
  }

  // 通用 onmessage 兜底：捕获未设置 event: 字段的 SSE 消息
  es.onmessage = handler;

  // 连接错误处理
  es.onerror = () => {
    onMessage({ event: 'error', data: { message: 'SSE 连接错误' } });
  };

  return () => es.close();
}
