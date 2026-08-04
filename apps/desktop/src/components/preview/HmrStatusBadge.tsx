/**
 * HmrStatusBadge —— 顶部状态徽章：● HMR connected（绿）/ ○ disconnected（黄）/
 * ✕ Build error（红）。
 */
import { clsx } from "clsx";

export type HmrStatus =
  "connected" | "disconnected" | "reconnecting" | "unknown";

const LABEL: Record<HmrStatus, string> = {
  connected: "HMR connected",
  disconnected: "HMR disconnected",
  reconnecting: "HMR reconnecting",
  unknown: "HMR unknown",
};

export function HmrStatusBadge({ status }: { status: HmrStatus }) {
  const dot =
    status === "connected"
      ? "bg-emerald-500"
      : status === "disconnected" || status === "reconnecting"
        ? "bg-amber-400"
        : "bg-neutral-500";
  return (
    <span
      className={clsx(
        "inline-flex items-center gap-1.5 rounded-full px-2 py-0.5 text-[11px] font-medium",
        status === "connected"
          ? "bg-emerald-500/15 text-emerald-300"
          : status === "disconnected" || status === "reconnecting"
            ? "bg-amber-500/15 text-amber-300"
            : "bg-neutral-700/60 text-neutral-400",
      )}
      data-testid="hmr-status-badge"
    >
      <span className={clsx("h-1.5 w-1.5 rounded-full", dot)} />
      {LABEL[status]}
    </span>
  );
}
