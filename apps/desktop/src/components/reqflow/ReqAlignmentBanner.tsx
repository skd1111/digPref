/**
 * ReqAlignmentBanner —— reqflow V1 需求对齐横幅。
 *
 * 功能点树「发起改造需求」后显示在对话区顶部：
 *   - 提示当前对齐中的功能点数量
 *   - 「生成需求卡片」：汇总本会话对话 → AI 结构化 → 落库 draft → 跳需求工作台
 *   - 「取消」：退出对齐模式
 */
import { useChatStore } from '@/store/chatStore';
import { useBiznavStore } from '@/store/biznavStore';
import { useReqcardStore } from '@/store/reqcardStore';
import { useUIStore } from '@/store/uiStore';

/** 从当前会话消息提取对话摘要（user + assistant 正文，截断 4000 字符） */
function buildConversationSummary(): string {
  const { tabs, activeTabId } = useChatStore.getState();
  const tab = tabs.find((t) => t.id === activeTabId);
  if (!tab) return '';
  const parts: string[] = [];
  for (const m of tab.messages) {
    if ((m.role === 'user' || m.role === 'assistant') && m.content) {
      parts.push(`${m.role === 'user' ? '业务人员' : 'AI'}：${m.content}`);
    }
  }
  const joined = parts.join('\n');
  return joined.length > 4000 ? joined.slice(-4000) : joined;
}

export function ReqAlignmentBanner(): JSX.Element | null {
  const alignment = useReqcardStore((s) => s.alignment);
  const generating = useReqcardStore((s) => s.generating);
  const error = useReqcardStore((s) => s.error);
  const generateAndSaveCard = useReqcardStore((s) => s.generateAndSaveCard);
  const cancelAlignment = useReqcardStore((s) => s.cancelAlignment);
  const features = useBiznavStore((s) => s.features);
  const projectName = useBiznavStore((s) => s.projectName);
  const setActivityId = useUIStore((s) => s.setActivityId);

  if (!alignment.active || alignment.featureIds.length === 0) return null;

  const names = alignment.featureIds
    .map((id) => features.find((f) => f.id === id)?.name ?? id)
    .join('、');

  const handleGenerate = async (): Promise<void> => {
    const card = await generateAndSaveCard({
      featureIds: alignment.featureIds,
      projectName,
      systemName: projectName,
      conversationSummary: buildConversationSummary(),
    });
    if (card) {
      // 成功：退出对齐 + 跳需求工作台查看/编辑新卡片
      cancelAlignment();
      useChatStore.getState().setAlignmentFeatures(null);
      setActivityId('requirements');
    }
  };

  const handleCancel = (): void => {
    cancelAlignment();
    useChatStore.getState().setAlignmentFeatures(null);
  };

  return (
    <div
      className="mb-2 flex items-center gap-2 rounded border px-3 py-1.5 text-2xs"
      style={{
        borderColor: '#007acc',
        backgroundColor: '#e8f2fb',
        color: '#0451a5',
      }}
    >
      <span className="flex-1 truncate">
        📝 需求对齐中 · {alignment.featureIds.length} 个功能点：{names}
        {error && (
          <span style={{ color: '#cd3131' }}> · ⚠ {error}</span>
        )}
      </span>
      <button
        type="button"
        onClick={() => void handleGenerate()}
        disabled={generating}
        className="rounded px-2 py-0.5 font-semibold transition-colors hover:brightness-110"
        style={{
          backgroundColor: '#0e639c',
          color: '#ffffff',
          cursor: generating ? 'wait' : 'pointer',
        }}
      >
        {generating ? '生成中…' : '生成需求卡片'}
      </button>
      <button
        type="button"
        onClick={handleCancel}
        disabled={generating}
        className="rounded px-1.5 py-0.5 transition-colors hover:bg-vscode-border"
        style={{ color: '#616161' }}
        title="退出需求对齐"
      >
        ✕
      </button>
    </div>
  );
}
