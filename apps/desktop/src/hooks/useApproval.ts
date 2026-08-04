/**
 * useApproval — hook that wires an ApprovalCard button to the IPC.
 */
import { ipc } from '@/ipc/invoke';

export function useApproval(approvalId: string) {
  return {
    approve: () => ipc.approve(approvalId, 'approve'),
    reject: () => ipc.approve(approvalId, 'reject'),
  };
}