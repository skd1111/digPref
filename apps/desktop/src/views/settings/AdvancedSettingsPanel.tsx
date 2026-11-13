/**
 * AdvancedSettingsPanel —— 高级设置（2026-08-05）。
 *
 * 原 ChatInput 发送按钮旁的两个开关迁移至此：
 *   - 推理模式（正常 ⚡ / 性能 🚀）：正常=简单任务端侧优先；性能=全部走云端 +
 *     注入完整版双模式提示词（chatStore.inferenceMode → chat 请求透传后端）
 *   - 会话自主性（交互 👤 / 自动 🤖）：自动模式下审批闸门按推荐项自动执行，
 *     无需逐次确认，直到任务完成（硬阻断 DROP/TRUNCATE 除外，任何模式都拒绝）
 *
 * 两个开关均只存 chatStore（会话级，不持久化），重启回落默认值。
 *
 * 另增「界面缩放」（2026-11-09，连续可调）：80%–200%，滑块 5% 步进 /
 * − ＋ 按钮 10% 步进，写 uiStore.uiZoom（持久化），与 View 菜单
 * Zoom In / Zoom Out / Reset Zoom 共用（全界面等比放大）。
 */
import { InferenceModeToggle } from '@/components/chat/InferenceModeToggle';
import { AutonomyToggle } from '@/components/chat/AutonomyToggle';
import { useChatStore } from '@/store/chatStore';
import { useUIStore } from '@/store/uiStore';
import {
  isZoomAtMax,
  isZoomAtMin,
  UI_ZOOM_DEFAULT,
  UI_ZOOM_MAX,
  UI_ZOOM_MIN,
  zoomInFrom,
  zoomOutFrom,
  zoomPercent,
} from '@/lib/uiZoom';

function Row({
  title,
  desc,
  children,
}: {
  title: string;
  desc: string;
  children: React.ReactNode;
}): JSX.Element {
  return (
    <div
      className="mb-3 flex items-center justify-between gap-4 rounded border px-4 py-3"
      style={{ borderColor: '#d4d4d4', backgroundColor: '#fafafa' }}
    >
      <div className="min-w-0">
        <div className="text-ui font-semibold" style={{ color: '#1f1f1f' }}>
          {title}
        </div>
        <div className="mt-0.5 text-2xs leading-relaxed" style={{ color: '#616161' }}>
          {desc}
        </div>
      </div>
      <div className="flex flex-shrink-0 items-center gap-2">{children}</div>
    </div>
  );
}

export function AdvancedSettingsPanel(): JSX.Element {
  const inferenceMode = useChatStore((s) => s.inferenceMode);
  const autonomy = useChatStore((s) => s.autonomy);
  const uiZoom = useUIStore((s) => s.uiZoom);
  const setUiZoom = useUIStore((s) => s.setUiZoom);

  return (
    <div className="mx-auto max-w-3xl">
      <h1 className="text-ui-lg font-semibold" style={{ color: '#1f1f1f' }}>
        高级设置
      </h1>
      <p className="mb-4 mt-1 text-2xs" style={{ color: '#616161' }}>
        智能体执行行为的高级开关。均为会话级设置，重启后恢复默认值。
      </p>

      <Row
        title="界面缩放"
        desc={`可连续调节 ${zoomPercent(UI_ZOOM_MIN)}–${zoomPercent(
          UI_ZOOM_MAX,
        )}（默认 ${zoomPercent(
          UI_ZOOM_DEFAULT,
        )}）；拖动滑块或点 − / ＋，选择会记住，下次启动直接生效。也可用顶部 View 菜单的 Zoom In / Zoom Out。`}
      >
        <div className="flex items-center gap-1.5">
          <button
            type="button"
            onClick={() => setUiZoom(zoomOutFrom(uiZoom))}
            disabled={isZoomAtMin(uiZoom)}
            className="btn btn-secondary btn-xs"
            title="缩小一档（-10%）"
          >
            −
          </button>
          <input
            type="range"
            min={UI_ZOOM_MIN}
            max={UI_ZOOM_MAX}
            step={0.05}
            value={uiZoom}
            onChange={(e) => setUiZoom(Number(e.target.value))}
            aria-label="界面缩放"
            className="w-36"
            style={{ accentColor: '#10a37f' }}
          />
          <button
            type="button"
            onClick={() => setUiZoom(zoomInFrom(uiZoom))}
            disabled={isZoomAtMax(uiZoom)}
            className="btn btn-secondary btn-xs"
            title="放大一档（+10%）"
          >
            ＋
          </button>
          <span
            className="w-10 text-right text-ui font-semibold"
            style={{ color: '#1f1f1f' }}
            title="当前界面缩放比例"
          >
            {zoomPercent(uiZoom)}
          </span>
        </div>
      </Row>

      <Row
        title="推理模式"
        desc={
          inferenceMode === 'normal'
            ? '当前：正常 —— 简单任务（意图分类/列计划）端侧小模型优先，失败回退 Ollama/云端。'
            : '当前：性能 —— 全部任务直走 Ollama/内网/云端模型，并注入完整版执行纪律提示词。'
        }
      >
        <InferenceModeToggle />
      </Row>

      <Row
        title="会话自主性"
        desc={
          autonomy === 'auto'
            ? '当前：自动 —— 审批闸门按推荐项自动执行（全程审计留痕），无需逐次确认，直到任务完成。DROP/TRUNCATE 等硬阻断操作任何模式都拒绝。'
            : '当前：交互 —— 中高风险操作逐步弹出审批卡片等你确认（默认，更安全）。'
        }
      >
        <AutonomyToggle />
      </Row>
    </div>
  );
}
