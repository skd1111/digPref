/**
 * InferenceModeToggle —— Phase 4 V0 推理模式切换。
 *
 * 2026-08-05 起渲染于 设置 → 高级设置（AdvancedSettingsPanel）：
 *   ⚡ 正常 —— 简单任务端侧优先（本地模型做分类+列计划）
 *   🚀 性能 —— 全部走云端（跳过端侧，内网/云端模型直出）
 */
import { useChatStore } from '@/store/chatStore';

export function InferenceModeToggle(): JSX.Element {
  const mode = useChatStore((s) => s.inferenceMode);
  const toggle = useChatStore((s) => s.toggleInferenceMode);

  const isNormal = mode === 'normal';

  return (
    <button
      type="button"
      onClick={toggle}
      title={
        isNormal
          ? '正常模式：简单任务端侧优先（分类+列计划走本地）'
          : '性能模式：全部走云端（跳过端侧模型）'
      }
      className="flex items-center gap-1 rounded px-2 py-1 text-xs font-medium transition-colors"
      style={{
        backgroundColor: isNormal ? '#1a3a2a' : '#3a2a1a',
        color: isNormal ? '#059669' : '#e8ab5e',
        border: `1px solid ${isNormal ? '#2a5a3a' : '#5a3a2a'}`,
      }}
    >
      <span>{isNormal ? '⚡' : '🚀'}</span>
      <span className="hidden sm:inline">{isNormal ? '正常' : '性能'}</span>
    </button>
  );
}
