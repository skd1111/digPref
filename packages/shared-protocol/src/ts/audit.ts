/**
 * Audit entry shape — mirrors the SQLite row produced by both Rust and Python.
 */
export type AuditAction =
  | 'agent.run.start'
  | 'agent.run.error'
  | 'agent.approval'
  | 'agent.cancel'
  | 'approval.request'
  | 'approval.decision'
  | 'credential.set'
  | 'db.execute'
  | string;

export interface AuditEntry {
  id: number;
  action: AuditAction;
  payload: unknown;
  ts: string;          // RFC 3339
  operator?: string;
  runId?: string;
}