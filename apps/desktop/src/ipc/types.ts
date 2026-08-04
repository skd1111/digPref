/**
 * Cross-language types re-exported from @eaide/shared-protocol.
 * Imported here so callers can do `import type { ... } from '@/ipc/types'`.
 */
export type {
  AgentStreamEvent,
  ApprovalRequest,
  ApprovalDecision,
  ChatMessage,
  ToolCall,
  ToolResult,
  TraceStep,
  PendingApproval,
} from '@eaide/shared-protocol';