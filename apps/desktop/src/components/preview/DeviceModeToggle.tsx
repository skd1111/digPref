/**
 * DeviceModeToggle —— 桌面 / 平板 / 手机 / 自定义 设备模式切换。
 */
import { clsx } from "clsx";

export type PreviewDeviceMode = "desktop" | "tablet" | "mobile" | "custom";

const MODES: Array<{ id: PreviewDeviceMode; label: string; title: string }> = [
  { id: "desktop", label: "🖥️", title: "桌面 (1280×800)" },
  { id: "tablet", label: "📱", title: "平板 (768×1024)" },
  { id: "mobile", label: "📲", title: "手机 (375×667)" },
  { id: "custom", label: "✂️", title: "自定义" },
];

export function DeviceModeToggle({
  value,
  onChange,
}: {
  value: PreviewDeviceMode;
  onChange: (m: PreviewDeviceMode) => void;
}) {
  return (
    <div
      className="flex items-center gap-1 rounded bg-neutral-800 p-0.5"
      role="group"
      aria-label="设备模式"
    >
      {MODES.map((m) => (
        <button
          key={m.id}
          type="button"
          title={m.title}
          aria-pressed={value === m.id}
          onClick={() => onChange(m.id)}
          className={clsx(
            "rounded px-2 py-1 text-xs transition-colors",
            value === m.id
              ? "bg-sky-600 text-white"
              : "text-neutral-400 hover:bg-neutral-700 hover:text-neutral-200",
          )}
        >
          {m.label}
        </button>
      ))}
    </div>
  );
}
