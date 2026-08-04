/**
 * ZoomControl —— 50% / 75% / 100% / 125% / 150% 缩放控制。
 */
import { clsx } from "clsx";

export const ZOOM_LEVELS = [50, 75, 100, 125, 150] as const;

export function ZoomControl({
  value,
  onChange,
}: {
  value: number;
  onChange: (z: number) => void;
}) {
  return (
    <div className="flex items-center gap-1" role="group" aria-label="缩放">
      {ZOOM_LEVELS.map((z) => (
        <button
          key={z}
          type="button"
          onClick={() => onChange(z)}
          aria-pressed={value === z}
          className={clsx(
            "rounded px-1.5 py-1 text-xs tabular-nums transition-colors",
            value === z
              ? "bg-sky-600 text-white"
              : "text-neutral-400 hover:bg-neutral-700 hover:text-neutral-200",
          )}
        >
          {z}%
        </button>
      ))}
    </div>
  );
}
