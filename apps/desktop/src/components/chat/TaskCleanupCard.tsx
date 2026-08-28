/**
 * TaskCleanupCard —— 交付后验收清理卡（2026-08-26）。
 *
 * 任务级工作目录规则：一个聊天页签 = 一个任务文件夹（工作空间/tasks/ 下），
 * 运行期间产生的文件都落在里面。交付完成（done 且本轮有产物）时由
 * useAgentStream 追入本卡：展示产物与中间文件，询问用户是否清理
 * 除产物之外的中间文件。
 *
 * 决策结果写回消息（status + category）持久化，组件卸载/重挂不丢状态
 * （同 ChangedFilesCard 走 store 而非组件内 ref 的先例）。
 */
import { useEffect, useState } from 'react';
import type { ChatMessage as ChatMessageT } from '@eaide/shared-protocol';
import { ipc } from '@/ipc/invoke';
import { useChatStore } from '@/store/chatStore';

interface CardPayload {
  taskId: string;
  taskDir: string;
}

function parsePayload(content: string): CardPayload | null {
  try {
    const p: unknown = JSON.parse(content);
    if (
      typeof p === 'object' && p !== null &&
      typeof (p as CardPayload).taskId === 'string' &&
      typeof (p as CardPayload).taskDir === 'string'
    ) {
      return p as CardPayload;
    }
  } catch {
    /* content 非法 → 不渲染（不阻断对话流） */
  }
  return null;
}

/** 取文件名（跨平台分隔符） */
function baseName(p: string): string {
  return p.split(/[\\/]/).filter(Boolean).pop() || p;
}

export function TaskCleanupCard({ message }: { message: ChatMessageT }): JSX.Element {
  const payload = parsePayload(message.content);
  const [artifacts, setArtifacts] = useState<string[]>([]);
  const [intermediates, setIntermediates] = useState<string[]>([]);
  const [loaded, setLoaded] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const resolved = message.status === 'ok';
  const keptAll = resolved && message.category === 'kept';

  // 拉取任务目录清单（产物/中间文件）；已决策的卡片不再请求
  useEffect(() => {
    if (!payload || resolved) return;
    let alive = true;
    void ipc
      .taskFilesGet(payload.taskId)
      .then((r) => {
        if (!alive) return;
        setArtifacts(r.artifacts);
        setIntermediates(r.intermediates);
        setLoaded(true);
      })
      .catch((e) => {
        if (!alive) return;
        setError(String(e));
        setLoaded(true);
      });
    return () => {
      alive = false;
    };
  }, [payload?.taskId, resolved, payload]);

  if (!payload) return <></>;

  const cleanup = async (): Promise<void> => {
    if (busy) return;
    setBusy(true);
    setError(null);
    try {
      const r = await ipc.taskCleanup(payload.taskId, artifacts);
      useChatStore.getState().update(message.id, {
        status: 'ok',
        category: `cleaned:${r.deleted.length}`,
      });
    } catch (e) {
      setError(`清理失败：${String(e)}`);
    } finally {
      setBusy(false);
    }
  };

  const keepAll = (): void => {
    useChatStore.getState().update(message.id, { status: 'ok', category: 'kept' });
  };

  const openFile = async (path: string): Promise<void> => {
    try {
      await ipc.openWithDefault(path);
    } catch (e) {
      setError(`打开失败：${String(e)}`);
    }
  };

  // 已决策：只留一行结果文案（不占对话区版面）
  if (resolved) {
    const cleanedMatch = /^cleaned:(\d+)$/.exec(message.category ?? '');
    const text = keptAll
      ? '✅ 已保留本任务的全部文件（未清理）'
      : cleanedMatch
        ? `✅ 已清理 ${cleanedMatch[1]} 个中间文件，产物已保留`
        : '✅ 已处理';
    return (
      <div className="mb-3">
        <div
          className="rounded-lg border px-2 py-1.5 text-2xs"
          style={{ backgroundColor: '#f0fdf4', borderColor: '#bbf7d0', color: '#166534' }}
        >
          {text}
        </div>
      </div>
    );
  }

  return (
    <div className="mb-3">
      <div
        className="rounded-lg border p-2"
        style={{ backgroundColor: '#f8fafc', borderColor: '#dbe4ee' }}
      >
        <div className="mb-1.5 text-2xs font-semibold" style={{ color: '#334155' }}>
          📦 任务已交付 · 是否清理中间文件？
        </div>
        <div className="mb-1.5 text-[10px]" style={{ color: '#64748b' }}>
          任务目录：
          <button
            type="button"
            onClick={() => void ipc.revealInExplorer(payload.taskDir).catch(() => undefined)}
            title="在资源管理器中打开任务目录"
            className="font-mono underline-offset-2 hover:underline"
            style={{ color: '#0451a5' }}
          >
            {payload.taskDir}
          </button>
        </div>
        {artifacts.length > 0 && (
          <ul className="mb-1.5 space-y-0.5">
            {artifacts.map((f) => (
              <li key={f}>
                <button
                  type="button"
                  onClick={() => void openFile(f)}
                  title={`${f}\n点击用默认程序打开`}
                  className="flex w-full items-center gap-1.5 rounded px-1.5 py-1 text-left text-2xs transition-colors hover:bg-[#e8f0fe]"
                  style={{ color: '#1f2937' }}
                >
                  <span className="flex-shrink-0">🎁</span>
                  <span className="font-mono font-semibold" style={{ color: '#0451a5' }}>
                    {baseName(f)}
                  </span>
                  <span className="text-[10px]" style={{ color: '#94a3b8' }}>
                    产物 · 点击打开
                  </span>
                </button>
              </li>
            ))}
          </ul>
        )}
        <div className="mb-1.5 text-[10px]" style={{ color: '#64748b' }}>
          {loaded
            ? intermediates.length > 0
              ? `另有 ${intermediates.length} 个中间文件（临时产物/脚本等），清理时会被删除`
              : '没有发现中间文件，无需清理'
            : '正在统计任务文件…'}
        </div>
        {error && (
          <div className="mb-1.5 text-[10px]" style={{ color: '#dc2626' }}>
            {error}
          </div>
        )}
        <div className="flex gap-2">
          <button
            type="button"
            onClick={() => void cleanup()}
            disabled={busy || !loaded || intermediates.length === 0}
            className="rounded border px-2 py-1 text-2xs transition-colors hover:opacity-80 disabled:cursor-not-allowed disabled:opacity-40"
            style={{ borderColor: '#0b6bcb', color: '#0b6bcb', backgroundColor: '#ffffff' }}
          >
            {busy ? '⏳ 清理中…' : '🧹 清理中间文件（保留产物）'}
          </button>
          <button
            type="button"
            onClick={keepAll}
            disabled={busy || !loaded}
            className="rounded border px-2 py-1 text-2xs transition-colors hover:opacity-80 disabled:cursor-not-allowed disabled:opacity-40"
            style={{ borderColor: '#e2e8f0', color: '#475569', backgroundColor: '#ffffff' }}
          >
            全部保留
          </button>
        </div>
      </div>
    </div>
  );
}
