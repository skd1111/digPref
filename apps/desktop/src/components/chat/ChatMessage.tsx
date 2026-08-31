/**
 * ChatMessage — renders a single message in the conversation stream.
 *
 * 尺寸约束（2026-08-04）：
 *   - user / assistant 消息框各占 chat 区横向宽度的 2/5（由父组件传入 px 上限）
 *   - 用户消息超过 10 行折叠：悬停显示全文浮层，点击展开 / 收起
 *   - 智能体消息不折叠：2/5 宽度内全量展示
 */
import { useEffect, useRef, useState, type CSSProperties } from 'react';
import type { ChatMessage as ChatMessageT } from '@eaide/shared-protocol';
import { isMockText } from '@/lib/mockFilter';
import { stripClarifyBlock } from '@/lib/clarify';
import { ipc } from '@/ipc/invoke';
import { useCodeNavStore } from '@/store/codeNavStore';
import { useUIStore } from '@/store/uiStore';
import { CodeBlock } from './CodeBlock';
import { ApprovalCard } from './ApprovalCard';
import { Markdown } from './Markdown';
import { AiSearchIndicator } from './AiStatus';
import { TaskCleanupCard } from './TaskCleanupCard';
import { FeedbackButtons } from './FeedbackButtons';

interface Props {
  message: ChatMessageT;
  /** chat 区横向宽度的 2/5（px）；未提供时不做宽度限制 */
  maxWidth?: number | undefined;
  /** 流式输出中（仅最后一条 assistant 消息为 true）→ 末尾显示闪烁光标 */
  streaming?: boolean | undefined;
}

/** 用户消息折叠时的最大行数 */
const COLLAPSE_LINES = 10;

function useContentOverflow(
  ref: { current: HTMLDivElement | null },
  key: string,
): boolean {
  const [overflow, setOverflow] = useState(false);
  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    const check = (): void => {
      setOverflow(el.scrollHeight > el.clientHeight + 1);
    };
    check();
    const ro = new ResizeObserver(check);
    ro.observe(el);
    return () => ro.disconnect();
  }, [key, ref]);
  return overflow;
}

export function ChatMessage({ message, maxWidth, streaming }: Props): JSX.Element {
  // 不显示任何 mock 占位数据（后端 mock 输出统一带 （mock） 前缀）
  if (isMockText(message.content)) {
    return <></>;
  }
  // 流异常终止的系统消息：红调卡片 + 「重试」按钮（2026-08-07）
  if (message.role === 'system' && message.kind === 'error') {
    return <ErrorBubble message={message} />;
  }
  // 搜索/检索类工具调用：aicss 风格搜索卡片（2026-08-10）
  if (message.role === 'system' && message.kind === 'search') {
    return (
      <AiSearchIndicator
        query={message.content}
        done={message.status !== 'running'}
      />
    );
  }
  // 任务结束汇总的改动文件清单（2026-08-19）：可点击，点击在 Monaco 打开
  if (message.role === 'system' && message.kind === 'changed_files') {
    return <ChangedFilesCard message={message} />;
  }
  // 任务进度待办卡（BUGFIX #150）：不再占对话区版面，改由 CenterChatFlow
  // 悬浮在会话页签下方常驻展示；消息仍留在 store 供横幅读取与历史归档。
  if (message.role === 'system' && message.kind === 'todo') {
    return <></>;
  }
  // 交付后验收清理卡（2026-08-26）：询问是否清理任务目录内除产物外的文件
  if (message.role === 'system' && message.kind === 'task_cleanup_confirm') {
    return <TaskCleanupCard message={message} />;
  }
  return (
    <div className="mb-3">
      <div className="mb-0.5 text-2xs" style={{ color: '#9ca3af' }}>
        {message.role === 'user' ? '你' : message.role}
      </div>
      {message.role === 'user' ? (
        <UserBubble message={message} maxWidth={maxWidth} />
      ) : (
        <AssistantBubble message={message} maxWidth={maxWidth} streaming={streaming} />
      )}
    </div>
  );
}

/**
 * 悬停复制按钮（2026-08-07）：气泡右上角，hover 气泡时浮现。
 * 复制纯文本内容（不含 Markdown 标记之外的渲染装饰）。
 */
function CopyButton({ text }: { text: string }): JSX.Element {
  const [copied, setCopied] = useState(false);
  const copy = (): void => {
    void navigator.clipboard.writeText(text).then(() => {
      setCopied(true);
      setTimeout(() => setCopied(false), 1500);
    });
  };
  return (
    <button
      type="button"
      onClick={copy}
      title="复制内容"
      className="absolute right-1 top-1 rounded border px-1.5 py-0.5 text-[10px] opacity-0 transition-opacity group-hover:opacity-100"
      style={{
        backgroundColor: copied ? '#10a37f' : '#ffffff',
        borderColor: copied ? '#10a37f' : '#e7e5e4',
        color: copied ? '#ffffff' : '#6b7280',
      }}
    >
      {copied ? '✓' : '⧉'}
    </button>
  );
}

function UserBubble({
  message,
  maxWidth,
}: {
  message: ChatMessageT;
  maxWidth?: number | undefined;
}): JSX.Element {
  const contentRef = useRef<HTMLDivElement>(null);
  const [expanded, setExpanded] = useState(false);
  const overflowing = useContentOverflow(contentRef, `${message.id}-${message.content}`);
  const widthStyle: CSSProperties =
    maxWidth && maxWidth > 0 ? { maxWidth } : {};

  const collapsedStyle: CSSProperties = expanded
    ? { ...widthStyle }
    : {
        ...widthStyle,
        overflow: 'hidden',
        display: '-webkit-box',
        WebkitLineClamp: COLLAPSE_LINES,
        WebkitBoxOrient: 'vertical',
      };

  return (
    <div className="flex justify-end">
      <div className="group relative max-w-[85%]">
        <div
          ref={contentRef}
          onClick={() => setExpanded((v) => !v)}
          title={overflowing && !expanded ? '点击展开 / 悬停查看全部' : undefined}
          className="whitespace-pre-wrap break-words rounded-2xl p-2.5 text-ui"
          style={{ ...collapsedStyle, backgroundColor: '#eef4f2', color: '#202124' }}
        >
          {message.content}
          {message.code && <CodeBlock code={message.code} language={message.codeLang ?? 'sql'} />}
          {message.pendingApproval && <ApprovalCard approval={message.pendingApproval} />}
        </div>
        <CopyButton text={message.content} />
        {/* 悬停浮层：展示折叠掉的全部内容（不拦截点击，点击仍走展开逻辑） */}
        {overflowing && !expanded && (
          <div
            className="pointer-events-none absolute inset-0 z-20 hidden overflow-auto whitespace-pre-wrap break-words rounded border p-2 text-ui shadow-xl group-hover:block"
            style={{ ...widthStyle, maxHeight: '50vh', backgroundColor: '#ffffff', borderColor: '#e7e5e4', color: '#202124' }}
          >
            {message.content}
          </div>
        )}
        {overflowing && !expanded && (
          <div className="mt-0.5 text-right text-[10px]" style={{ color: '#9ca3af' }}>
            内容较长 · 悬停查看全部，点击展开
          </div>
        )}
      </div>
    </div>
  );
}

function AssistantBubble({
  message,
  maxWidth,
  streaming,
}: {
  message: ChatMessageT;
  maxWidth?: number | undefined;
  streaming?: boolean | undefined;
}): JSX.Element {
  const widthStyle: CSSProperties =
    maxWidth && maxWidth > 0 ? { maxWidth } : {};
  // 选项式追问（2026-08-05）：clarify 围栏块由输入框上方卡片渲染，
  // 正文不展示原始 JSON
  const displayContent = stripClarifyBlock(message.content);

  return (
    <div className="flex">
      <div className="group relative max-w-[85%]">
        {/* aicss Text Response 风格（2026-08-10）：助手回复去框化，纯 prose 排版 */}
        <div
          className="whitespace-pre-wrap break-words py-0.5 text-ui"
          style={{ ...widthStyle, color: '#202124' }}
        >
          {/* 助手回复走轻量 Markdown 渲染（标题/列表/代码/表格），样式见 globals.css .md-body */}
          <Markdown text={displayContent} />
          {/* 流式输出中：末尾闪烁光标（打字机感，2026-08-07） */}
          {streaming && <span className="stream-cursor" aria-hidden="true" />}
          {message.code && <CodeBlock code={message.code} language={message.codeLang ?? 'sql'} />}
          {message.pendingApproval && <ApprovalCard approval={message.pendingApproval} />}
        </div>
        {!streaming && <CopyButton text={displayContent} />}
        {/* Phase 19 V0 自进化：终答 👍/👎 反馈（非流式且有内容才挂载） */}
        {!streaming && displayContent.trim() !== '' && (
          <FeedbackButtons messageId={message.id} sessionId={message.runId ?? ''} />
        )}
      </div>
    </div>
  );
}

/** 重试事件名：ChatInput 监听后重发最后一条用户消息 */
export const CHAT_RETRY_EVENT = 'eaide-chat-retry';

/** 快捷提问事件名（2026-08-07）：欢迎页点击示例卡 → ChatInput 直接发送（detail = 文本） */
export const CHAT_SEND_EVENT = 'eaide-chat-send';

/**
 * 错误消息气泡：红调边框卡片 + 重试按钮（2026-08-07）。
 * 重试通过 window CustomEvent 通知 ChatInput（发送管道在那里），
 * 避免把整个发送链路提到消息层。
 */
function ErrorBubble({ message }: { message: ChatMessageT }): JSX.Element {
  return (
    <div className="mb-3">
      <div
        className="flex items-center gap-3 rounded border px-3 py-2"
        style={{ borderColor: '#fca5a5', backgroundColor: '#fef2f2' }}
      >
        <span className="text-ui" style={{ color: '#dc2626' }}>
          ⚠ {message.content}
        </span>
        <button
          type="button"
          onClick={() => window.dispatchEvent(new CustomEvent(CHAT_RETRY_EVENT))}
          className="ml-auto flex-shrink-0 rounded border px-3 py-0.5 text-2xs font-semibold transition-colors"
          style={{ borderColor: '#dc2626', color: '#dc2626', backgroundColor: '#ffffff' }}
        >
          ↻ 重试
        </button>
      </div>
    </div>
  );
}

/** 跨平台取文件名（兼容 Windows 反斜杠） */
function baseName(p: string): string {
  return p.split(/[\\/]/).filter(Boolean).pop() || p;
}

/** 跨平台取父目录显示串（去掉末尾分隔符） */
function dirName(p: string): string {
  const base = baseName(p);
  const idx = p.lastIndexOf(base);
  return idx > 0 ? p.slice(0, idx).replace(/[\\/]+$/, '') : '';
}

/**
 * 改动文件汇总卡片（2026-08-19）：任务结束时由 useAgentStream 汇总本轮
 * write_file / edit_file 成功路径生成（content = 路径 JSON 数组）。
 * 每条可点击 → 读文件内容并在 Monaco 打开（同 ProjectFileTree 单击文件链路）。
 */
function ChangedFilesCard({ message }: { message: ChatMessageT }): JSX.Element {
  let files: string[] = [];
  try {
    const parsed: unknown = JSON.parse(message.content);
    if (Array.isArray(parsed)) {
      files = parsed.filter((f): f is string => typeof f === 'string' && f.trim() !== '');
    }
  } catch {
    /* content 非法 → 渲染空卡片（不阻断对话流） */
  }

  const openFile = async (path: string): Promise<void> => {
    try {
      const content = await ipc.readTextFile(path);
      useCodeNavStore.getState().openFileInEditor({ path, content });
      if (!useUIStore.getState().editorSplit) {
        useUIStore.getState().setEditorSplit('vertical');
      }
    } catch (e) {
      // eslint-disable-next-line no-console
      console.warn('[ChangedFilesCard] open file failed:', path, e);
      window.alert(`打开文件失败：${path}\n${String(e)}`);
    }
  };

  if (files.length === 0) return <></>;

  return (
    <div className="mb-3">
      <div
        className="rounded-lg border p-2"
        style={{ backgroundColor: '#f8fafc', borderColor: '#dbe4ee' }}
      >
        <div className="mb-1.5 text-2xs font-semibold" style={{ color: '#334155' }}>
          📝 本次任务改动的文件（{files.length}）· 点击打开
        </div>
        <ul className="space-y-0.5">
          {files.map((f) => (
            <li key={f}>
              <button
                type="button"
                onClick={() => void openFile(f)}
                title={f}
                className="flex w-full items-center gap-1.5 rounded px-1.5 py-1 text-left text-2xs transition-colors hover:bg-[#e8f0fe]"
                style={{ color: '#1f2937' }}
              >
                <span className="flex-shrink-0">📄</span>
                <span className="font-mono font-semibold" style={{ color: '#0451a5' }}>
                  {baseName(f)}
                </span>
                <span className="truncate font-mono text-[10px]" style={{ color: '#94a3b8' }}>
                  {dirName(f)}
                </span>
              </button>
            </li>
          ))}
        </ul>
      </div>
    </div>
  );
}
