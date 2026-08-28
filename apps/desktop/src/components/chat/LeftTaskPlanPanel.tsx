/**
 * LeftTaskPlanPanel —— 左侧「任务计划」面板（2026-08-28）。
 *
 * 任务进度待办卡迁居左侧：与资源管理器并列，SideBar 头部可切换。
 * 数据源：当前激活对话页签最新一条 kind='todo' 消息（BUGFIX #169 后
 * todo 消息按 run 归属写入对应页签，切会话看的是各会话自己的计划）。
 *
 * 无计划时显示空态提示（不渲染 mock / 占位数据）。
 */
import { useChatStore } from '@/store/chatStore';
import { TodoCard } from '@/components/chat/TodoCard';

export function LeftTaskPlanPanel(): JSX.Element {
  // selector 返回字符串（稳定引用），避免每次渲染产生新对象触发重渲染
  const todoJson = useChatStore((s) => {
    const tab = s.tabs.find((t) => t.id === s.activeTabId);
    if (!tab) return '';
    for (let i = tab.messages.length - 1; i >= 0; i--) {
      const m = tab.messages[i];
      if (m.role === 'system' && m.kind === 'todo') return m.content ?? '';
    }
    return '';
  });

  if (!todoJson) {
    return (
      <div
        className="flex h-full flex-col items-center justify-center p-6 text-center"
        style={{ color: '#6e6e6e' }}
      >
        <div className="mb-2 text-2xl" aria-hidden="true">
          📋
        </div>
        <div className="mb-1 text-ui font-semibold" style={{ color: '#333333' }}>
          暂无任务计划
        </div>
        <div className="text-2xs">
          在对话中开始任务后，模型生成的任务计划会实时显示在这里
        </div>
      </div>
    );
  }

  return (
    <div className="h-full overflow-auto p-2">
      <TodoCard itemsJson={todoJson} />
    </div>
  );
}
