/**
 * KbImportDialog —— 知识库导入确认弹窗（2026-11-06）。
 *
 * 导入文件后**不直接**做分块 + 向量化：先把待导入清单摊给用户看
 * （文件名 / 文件分类 / 导入时间），允许逐条改分类、逐条删除，
 * 点「确认导入」后才真正入库（复制源文件 → 解析 → 分块 → 向量化）。
 */
import { useEffect } from 'react';
import { CloseIcon } from '@/components/ui/Icons';
import type { DictOption } from '@/hooks/useDictOptions';

/** 待导入条目（尚未入库，仅在确认弹窗内流转）。 */
export interface PendingImport {
  /** 稳定唯一键 */
  key: string;
  /** 本地绝对路径 */
  path: string;
  /** 文件名（展示用） */
  name: string;
  /** 分类：'__auto__' 自动识别 / '' 未分类 / 字典分类 value */
  category: string;
  /** 导入时间（毫秒时间戳） */
  addedAt: number;
  /** 非空时作为该逻辑资料的新版本导入 */
  baseDocId?: string;
  /** 分类判定来源：llm=模型判定；heuristic=文件名启发式兜底；未判定时为 undefined */
  detected?: 'llm' | 'heuristic' | undefined;
  /** 预判失败原因（行内提示，可重试） */
  error?: string | undefined;
}

/** 毫秒时间戳 → 本地时间（到秒）。 */
function fmtClock(ms: number): string {
  if (!ms) return '—';
  const dt = new Date(ms);
  const p = (x: number): string => String(x).padStart(2, '0');
  return `${dt.getFullYear()}-${p(dt.getMonth() + 1)}-${p(dt.getDate())} ${p(dt.getHours())}:${p(dt.getMinutes())}:${p(dt.getSeconds())}`;
}

export function KbImportDialog({
  items,
  categories,
  busy,
  mode,
  classifying,
  hint,
  onRetry,
  onChangeCategory,
  onRemove,
  onCancel,
  onConfirm,
}: {
  items: PendingImport[];
  categories: DictOption[];
  /** 入库进行中（逐条上传 + 后台向量化排队） */
  busy: boolean;
  /** 导入前分类预判进行中 */
  classifying?: boolean;
  /** 预判提示（未开启 / 部分失败等） */
  hint?: string;
  /** 重试分类（仅失败项会重新判定） */
  onRetry?: () => void;
  /** new = 新增资料；version = 更新某份资料的新版本 */
  mode: 'new' | 'version';
  onChangeCategory: (key: string, category: string) => void;
  onRemove: (key: string) => void;
  onCancel: () => void;
  onConfirm: () => void;
}): JSX.Element {
  useEffect(() => {
    const onKey = (e: KeyboardEvent): void => {
      if (e.key === 'Escape' && !busy) onCancel();
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [onCancel, busy]);

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center"
      style={{ backgroundColor: 'rgba(0,0,0,0.45)' }}
      onClick={() => {
        if (!busy) onCancel();
      }}
      role="dialog"
      aria-modal="true"
      aria-label={mode === 'version' ? '确认导入新版本' : '确认导入参考资料'}
    >
      <div
        className="flex max-h-[84vh] w-[720px] max-w-[92vw] flex-col rounded-lg shadow-2xl"
        style={{ backgroundColor: '#ffffff' }}
        onClick={(e) => e.stopPropagation()}
      >
        {/* 头部 */}
        <div
          className="flex flex-shrink-0 items-start gap-2 border-b px-5 py-3"
          style={{ borderColor: '#e0e0e0' }}
        >
          <div className="min-w-0 flex-1">
            <div className="text-ui font-semibold" style={{ color: '#1f1f1f' }}>
              {mode === 'version' ? '确认导入新版本' : '确认导入参考资料'}
            </div>
            <div className="mt-0.5 text-2xs" style={{ color: '#616161' }}>
              确认前不会做任何解析与向量化；确认后才复制源文件入库并做分块 + 向量化。
            </div>
          </div>
          <button
            type="button"
            onClick={onCancel}
            disabled={busy}
            className="btn btn-ghost btn-icon"
            aria-label="关闭导入确认"
          >
            <CloseIcon size={13} />
          </button>
        </div>

        {/* 待导入清单 */}
        <div className="min-h-0 flex-1 overflow-auto px-5 py-3">
          {items.length === 0 ? (
            <div className="py-8 text-center text-2xs" style={{ color: '#616161' }}>
              待导入清单已清空，请关闭弹窗重新选择文件。
            </div>
          ) : (
            <table className="w-full border-collapse text-2xs">
              <thead>
                <tr style={{ color: '#616161', borderBottom: '1px solid #e0e0e0' }}>
                  <th className="px-2 py-1.5 text-left font-medium">文件</th>
                  <th className="px-2 py-1.5 text-left font-medium">分类</th>
                  <th className="px-2 py-1.5 text-left font-medium">导入时间</th>
                  <th className="px-2 py-1.5 text-right font-medium">操作</th>
                </tr>
              </thead>
              <tbody>
                {items.map((it) => (
                  <tr key={it.key} style={{ borderBottom: '1px solid #f0f0f0' }}>
                    <td className="px-2 py-1.5" style={{ color: '#1f1f1f' }}>
                      <span className="font-semibold" title={it.path}>
                        {it.name}
                      </span>
                      {it.baseDocId && (
                        <span
                          className="ml-1.5 rounded px-1.5 py-0.5"
                          style={{ backgroundColor: '#eef3f8', color: '#0e639c' }}
                          title="作为该资料的新版本导入（就绪后自动生效，旧版本保留）"
                        >
                          新版本
                        </span>
                      )}
                    </td>
                    <td className="px-2 py-1.5">
                      <div className="flex items-center gap-1.5">
                        <select
                          value={it.category}
                          onChange={(e) => onChangeCategory(it.key, e.target.value)}
                          className="rounded border px-1.5 py-0.5 text-2xs outline-none"
                          style={{
                            borderColor: '#d4d4d4',
                            backgroundColor: '#ffffff',
                            color: '#333333',
                          }}
                          title="导入时打的分类标签（可在入库前手动调整）"
                        >
                          <option value="__auto__">自动识别</option>
                          <option value="">未分类</option>
                          {categories.map((c) => (
                            <option key={c.key} value={c.value}>
                              {c.label}
                            </option>
                          ))}
                        </select>
                        {classifying && it.category === '__auto__' && !it.error && (
                          <span className="text-2xs" style={{ color: '#0e639c' }}>
                            识别中…
                          </span>
                        )}
                        {!classifying && it.detected === 'heuristic' && (
                          <span
                            className="rounded px-1 py-0.5"
                            style={{ backgroundColor: '#f3f4f6', color: '#616161' }}
                            title="模型不可用/限流时按文件名与关键词推断的分类（可手动修改）"
                          >
                            建议分类
                          </span>
                        )}
                        {it.error && (
                          <span style={{ color: '#b25c1a' }} title={it.error}>
                            {it.error}
                          </span>
                        )}
                      </div>
                    </td>
                    <td className="px-2 py-1.5 whitespace-nowrap" style={{ color: '#616161' }}>
                      {fmtClock(it.addedAt)}
                    </td>
                    <td className="px-2 py-1.5 text-right whitespace-nowrap">
                      <button
                        type="button"
                        onClick={() => onRemove(it.key)}
                        disabled={busy}
                        className="btn btn-danger btn-xs"
                        title="从待导入清单移除该文件"
                      >
                        删除
                      </button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </div>

        {/* 底部 */}
        <div
          className="flex flex-shrink-0 items-center gap-2 border-t px-5 py-2.5"
          style={{ borderColor: '#e0e0e0' }}
        >
          <span className="min-w-0 flex-1 text-2xs" style={{ color: '#9a9a9a' }}>
            {classifying
              ? (hint ?? '正在自动识别分类…（识别完成后可直接确认导入）')
              : (hint ?? `共 ${items.length} 个文件待导入 · 分类留「自动识别」时按内容自动归类`)}
          </span>
          <button
            type="button"
            onClick={onRetry}
            disabled={busy || classifying || !onRetry}
            className="btn btn-secondary btn-sm"
            title="对未判定出分类的文件重新自动分类"
          >
            重试分类
          </button>
          <button type="button" onClick={onCancel} disabled={busy} className="btn btn-secondary btn-sm">
            取消
          </button>
          <button
            type="button"
            onClick={onConfirm}
            disabled={busy || items.length === 0}
            className="btn btn-primary btn-sm"
            title="确认后才入库并做分块 + 向量化"
          >
            {busy ? '导入中…' : `确认导入${items.length > 0 ? ` (${items.length})` : ''}`}
          </button>
        </div>
      </div>
    </div>
  );
}
