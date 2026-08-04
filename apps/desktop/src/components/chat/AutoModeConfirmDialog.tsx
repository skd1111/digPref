/**
 * AutoModeConfirmDialog —— Phase 18 自动模式风险确认弹窗。
 *
 * 会话内首次开启自动模式时弹出；确认后写 AUTO_MODE_ENABLED 审计。
 * 文案三条为合规硬要求，不可省略（spec §4.3）。
 */
interface Props {
  onConfirm: () => void;
  onCancel: () => void;
}

export function AutoModeConfirmDialog({ onConfirm, onCancel }: Props): JSX.Element {
  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center"
      style={{ backgroundColor: 'rgba(0,0,0,0.45)' }}
      role="dialog"
      aria-modal="true"
    >
      <div
        className="w-[440px] rounded-lg border p-5 shadow-xl"
        style={{ backgroundColor: '#ffffff', borderColor: '#d1d5db' }}
      >
        <h2 className="mb-2 text-base font-semibold" style={{ color: '#1f1f1f' }}>
          开启自动模式
        </h2>
        <p className="mb-3 text-xs" style={{ color: '#616161' }}>
          自动模式下，智能体将按推荐选项自主继续执行。请确认以下风险：
        </p>
        <ul
          className="mb-4 list-disc space-y-1.5 pl-5 text-xs"
          style={{ color: '#1f1f1f' }}
        >
          <li>
            <b>high/critical 风险操作也将按智能体推荐项自动执行</b>
            （不再逐一弹窗等待审批）；
          </li>
          <li>数据库硬阻断操作（如 DROP/TRUNCATE 等不可逆操作）除外，永远需要人工处理；</li>
          <li>所有自动决策都会记录在审计日志中，可随时追溯。</li>
        </ul>
        <p className="mb-4 text-2xs" style={{ color: '#616161' }}>
          本授权仅对当前会话有效；关闭应用或手动关闭开关后失效。
        </p>
        <div className="flex justify-end gap-2">
          <button
            type="button"
            onClick={onCancel}
            className="rounded border px-3 py-1.5 text-xs"
            style={{ borderColor: '#d1d5db', color: '#1f1f1f' }}
          >
            取消
          </button>
          <button
            type="button"
            onClick={onConfirm}
            className="rounded px-3 py-1.5 text-xs font-semibold text-white"
            style={{ backgroundColor: '#b45309' }}
          >
            我已了解风险，开启自动模式
          </button>
        </div>
      </div>
    </div>
  );
}
