/**
 * ChatMessage — renders a single message in the conversation stream.
 * Discriminates by role:
 *   - user      → right-aligned bubble
 *   - assistant → left-aligned, can embed CodeBlock / ApprovalCard
 *   - tool      → collapsed raw MCP payload (debug view)
 *   - system    → small grey inline notice
 */
import type { ChatMessage as ChatMessageT } from '@eaide/shared-protocol';
import { isMockText } from '@/lib/mockFilter';
import { CodeBlock } from './CodeBlock';
import { ApprovalCard } from './ApprovalCard';

interface Props {
  message: ChatMessageT;
}

export function ChatMessage({ message }: Props): JSX.Element {
  // 不显示任何 mock 占位数据（后端 mock 输出统一带 （mock） 前缀）
  if (isMockText(message.content)) {
    return <></>;
  }
  // TODO: implement full role-based rendering
  return (
    <div className="mb-3">
      <div className="text-xs text-fg-dim">{message.role}</div>
      <div className="rounded border border-border bg-bg-subtle p-2">
        {message.content}
        {message.code && <CodeBlock code={message.code} language={message.codeLang ?? 'sql'} />}
        {message.pendingApproval && <ApprovalCard approval={message.pendingApproval} />}
      </div>
    </div>
  );
}
