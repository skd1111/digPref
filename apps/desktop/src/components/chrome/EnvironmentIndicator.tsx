/**
 * EnvironmentIndicator —— 顶部醒目活跃环境指示器。
 *
 * 设计目标：
 *   - **醒目**：用饱和度高的色彩 + 左侧状态圆点 + 标签，跨模式（full / operator）持续可见
 *   - **可点击**：点击展开快速切换下拉，无需跳转到 Settings 页面
 *   - **丝滑**：状态切换用 150ms CSS 过渡，颜色变化有视觉反馈
 *   - **零侵入**：仅依赖 envStore；不订阅 Zustand 的高频字段，避免 UI 抖动
 */
import { useEffect, useRef, useState } from 'react';
import { useEnvStore, envColor, ENV_COLORS } from '@/store/envStore';

export function EnvironmentIndicator({ large = false }: { large?: boolean }): JSX.Element {
  const activeEnv = useEnvStore((s) => s.activeEnv);
  const list = useEnvStore((s) => s.list);
  const loading = useEnvStore((s) => s.loading);
  const error = useEnvStore((s) => s.error);
  const refresh = useEnvStore((s) => s.refresh);
  const setActive = useEnvStore((s) => s.setActive);
  const [open, setOpen] = useState(false);
  const [switching, setSwitching] = useState(false);
  const wrapRef = useRef<HTMLDivElement>(null);

  // 首次挂载时拉取环境列表
  useEffect(() => {
    void refresh();
  }, [refresh]);

  // 点击外部关闭下拉
  useEffect(() => {
    if (!open) return;
    const onDoc = (e: MouseEvent): void => {
      if (wrapRef.current && !wrapRef.current.contains(e.target as Node)) {
        setOpen(false);
      }
    };
    document.addEventListener('mousedown', onDoc);
    return () => document.removeEventListener('mousedown', onDoc);
  }, [open]);

  const color = envColor(activeEnv);
  const isProd = activeEnv === 'prod';

  const handleSwitch = async (env: string): Promise<void> => {
    if (env === activeEnv) {
      setOpen(false);
      return;
    }
    setSwitching(true);
    try {
      await setActive(env);
      setOpen(false);
    } catch (e) {
      // envStore 已经回滚 + 设了 error；这里给个可见的失败反馈
      alert(`切换环境失败：${String(e)}`);
    } finally {
      setSwitching(false);
    }
  };

  // large=true: 用于独立 TopBar，更高更大；false: 嵌入 MenuBar，更紧凑
  const sizes = large
    ? { h: 32, px: 14, font: 13, dotSize: 10 }
    : { h: 22, px: 10, font: 11, dotSize: 8 };

  return (
    <div ref={wrapRef} className="relative">
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        title={isProd ? '⚠ 当前是生产环境，操作将作用于线上！' : `当前活跃环境：${activeEnv ?? '未设置'}`}
        className="flex items-center gap-2 rounded-md font-bold uppercase tracking-wider transition-all duration-150 hover:brightness-110"
        style={{
          height: sizes.h,
          paddingLeft: sizes.px,
          paddingRight: sizes.px,
          fontSize: sizes.font,
          backgroundColor: color.bg,
          color: color.fg,
          boxShadow: open ? `0 0 0 2px ${color.bg}60` : '0 1px 2px rgba(0,0,0,0.3)',
        }}
      >
        {/* 状态圆点 + prod 警告动画 */}
        <span
          className={isProd ? 'env-dot-prod' : ''}
          style={{
            width: sizes.dotSize,
            height: sizes.dotSize,
            borderRadius: '50%',
            backgroundColor: '#ffffff',
            boxShadow: `0 0 0 2px ${color.fg}`,
            display: 'inline-block',
          }}
        />
        <span style={{ fontFamily: 'monospace' }}>
          {activeEnv ? color.label : 'NO ENV'}
        </span>
        <span style={{ opacity: 0.7, fontSize: large ? 12 : 10 }}>▾</span>
      </button>

      {open && (
        <div
          className="env-dropdown absolute left-0 top-full z-[200] mt-1 min-w-[260px] max-w-[calc(100vw-32px)] rounded shadow-2xl"
          style={{
            backgroundColor: '#f3f3f3',
            border: '1px solid #d0d0d0',
            animation: 'envDropdownIn 120ms ease-out',
          }}
        >
          {/* 头部：标题 + 状态指示 */}
          <div
            className="flex items-center justify-between border-b px-3 py-2 text-2xs font-semibold uppercase tracking-wider"
            style={{ borderColor: '#d4d4d4', color: '#333333' }}
          >
            <span>切换活跃环境</span>
            <div className="flex items-center gap-2">
              {loading && (
                <span style={{ color: '#0451a5', fontSize: 10 }}>
                  ⟳ 加载中
                </span>
              )}
              {error && (
                <button
                  type="button"
                  onClick={(e) => {
                    e.stopPropagation();
                    void refresh();
                  }}
                  className="rounded px-1.5 py-0.5 font-bold normal-case"
                  style={{ backgroundColor: '#fbeaea', color: '#ff8888', fontSize: 10 }}
                  title="点击重试"
                >
                  ⚠ 重试
                </button>
              )}
            </div>
          </div>

          {/* list 为空且不是 loading 也没 error → 引导用户 */}
          {list.length === 0 && !loading && !error && (
            <div
              className="px-3 py-3 text-2xs"
              style={{ color: '#616161' }}
            >
              暂未从后端拉取到环境列表。点击下方任一标准 env 即可激活（后端会自动 seed）。
            </div>
          )}

          {/* 推荐：4 个标准 env 一键切换（始终可点击——后端会自动 seed） */}
          <div className="p-1">
            {(['dev', 'test', 'staging', 'prod'] as const).map((stdEnv) => {
              const meta = list.find((e) => e.environment === stdEnv);
              const isActive = activeEnv === stdEnv;
              const c = envColor(stdEnv);
              const exists = !!meta;
              const configured = meta?.configured ?? false;
              return (
                <button
                  key={stdEnv}
                  type="button"
                  onClick={() => void handleSwitch(stdEnv)}
                  // ★ 关键修复：4 个标准 env **始终可点击**（之前 `!exists` 导致首次启动全 disabled）
                  // 后端会自动 seed 标准 env，激活时如果不存在会创建
                  disabled={switching}
                  className="flex w-full items-center justify-between rounded px-2 py-1.5 text-ui transition-colors hover:bg-vscode-border disabled:opacity-60"
                  style={{ color: '#1f1f1f' }}
                >
                  <div className="flex items-center gap-2">
                    <span
                      style={{
                        width: 10,
                        height: 10,
                        borderRadius: '50%',
                        backgroundColor: c.bg,
                      }}
                    />
                    <span className="font-mono font-bold uppercase">{stdEnv}</span>
                    {meta?.label && (
                      <span className="text-2xs" style={{ color: '#616161' }}>
                        {meta.label}
                      </span>
                    )}
                  </div>
                  <div className="flex items-center gap-1.5 text-2xs" style={{ color: '#616161' }}>
                    {!exists && list.length > 0 && <span>未创建</span>}
                    {exists && !configured && <span>未配置</span>}
                    {isActive && (
                      <span
                        className="rounded px-1.5 py-0.5 font-bold"
                        style={{ backgroundColor: c.bg, color: c.fg }}
                      >
                        ACTIVE
                      </span>
                    )}
                  </div>
                </button>
              );
            })}
          </div>

          {/* 自定义环境列表（非标准的） */}
          {list.some((e) => !ENV_COLORS[e.environment]) && (
            <div className="border-t p-1" style={{ borderColor: '#d4d4d4' }}>
              {list
                .filter((e) => !ENV_COLORS[e.environment])
                .map((e) => (
                  <button
                    key={e.environment}
                    type="button"
                    onClick={() => void handleSwitch(e.environment)}
                    disabled={switching}
                    className="flex w-full items-center justify-between rounded px-2 py-1.5 text-ui transition-colors hover:bg-vscode-border disabled:opacity-40"
                    style={{ color: '#1f1f1f' }}
                  >
                    <div className="flex items-center gap-2">
                      <span
                        style={{
                          width: 10,
                          height: 10,
                          borderRadius: '50%',
                          backgroundColor: '#6e6e6e',
                        }}
                      />
                      <span className="font-mono">{e.environment}</span>
                    </div>
                    {e.active && (
                      <span
                        className="rounded px-1.5 py-0.5 text-2xs font-bold"
                        style={{ backgroundColor: '#007acc', color: '#ffffff' }}
                      >
                        ACTIVE
                      </span>
                    )}
                  </button>
                ))}
            </div>
          )}

          <div
            className="border-t px-3 py-1.5 text-2xs"
            style={{ borderColor: '#d4d4d4', color: '#616161' }}
          >
            提示：env 决定 DB / SSH / API 等连接；prod 切换需 HITL 二次确认（即将支持）。
          </div>
        </div>
      )}

      {/* 内联样式：下拉入场动画 + prod 警告闪烁 */}
      <style>{`
        @keyframes envDropdownIn {
          from { opacity: 0; transform: translateY(-4px); }
          to   { opacity: 1; transform: translateY(0); }
        }
        @keyframes envProdPulse {
          0%, 100% { box-shadow: 0 0 0 2px #f4877140; }
          50%      { box-shadow: 0 0 0 4px #f4877180; }
        }
        .env-dot-prod {
          animation: envProdPulse 1.5s ease-in-out infinite;
        }
      `}</style>
    </div>
  );
}
