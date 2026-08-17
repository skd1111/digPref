/**
 * DataGrid —— 数据专家右栏（上）：查询结果网格（虚拟滚动）。
 *
 * V1 实现：@tanstack/react-virtual 虚拟滚动，支撑 10 万行 60fps。
 * 设计红线（design §4/§13）：严禁整表进 React DOM，仅渲染可视区域行。
 * 条件格式：负数标红（呼应 Excel 导出条件格式）。
 * 缩放（BUGFIX #105）：zoomed=true 时行高/字号放大（放大视图用）。
 */
import { useRef, useCallback } from 'react';
import { useVirtualizer } from '@tanstack/react-virtual';
import { useDataStore, cellFor } from '@/store/dataStore';

export function DataGrid({
  zoomed = false,
  onZoom,
}: {
  zoomed?: boolean;
  onZoom?: () => void;
}): JSX.Element {
  const result = useDataStore((s) => s.result);
  const running = useDataStore((s) => s.running);
  const streaming = useDataStore((s) => s.streaming);
  const parentRef = useRef<HTMLDivElement>(null);

  // 放大视图：行高 28→40、表头 30→40、字号 12px→15px（看得清）
  const rowHeight = zoomed ? 40 : 28;
  const headerHeight = zoomed ? 40 : 30;
  const cellFont = zoomed ? '15px' : undefined;

  // 行数只进元数据（列存形态下行数据不进 rows 数组）
  const rowCount = result?.rowCount ?? 0;

  const virtualizer = useVirtualizer({
    count: rowCount,
    getScrollElement: () => parentRef.current,
    estimateSize: () => rowHeight,
    overscan: 20,
  });

  const renderCell = useCallback(
    (cell: string | number, ci: number) => {
      const num = typeof cell === 'number';
      const negative = num && (cell as number) < 0;
      return (
        <td
          key={ci}
          className="whitespace-nowrap px-3 font-mono text-ui"
          style={{
            color: negative ? '#cd3131' : num ? '#b5cea8' : '#1f1f1f',
            textAlign: num ? 'right' : 'left',
            borderBottom: '1px solid #e0e0e0',
            height: rowHeight,
            lineHeight: `${rowHeight}px`,
            ...(cellFont ? { fontSize: cellFont } : {}),
          }}
        >
          {cell}
        </td>
      );
    },
    [rowHeight, cellFont],
  );

  return (
    <div className="flex h-full flex-col overflow-hidden" style={{ backgroundColor: '#ffffff' }}>
      <PanelHeader
        title="📋 数据网格"
        right={
          <div className="flex items-center gap-2">
            {result ? (
              <span className="text-2xs" style={{ color: '#616161' }}>
                {result.rowCount.toLocaleString()} 行 · {result.elapsedMs}ms
                {result.truncated ? ' · ⚠ 已截断' : ''}
              </span>
            ) : null}
            {onZoom ? (
              <button
                type="button"
                onClick={onZoom}
                className="rounded px-1.5 text-2xs transition-all hover:brightness-95"
                style={{ backgroundColor: '#ececec', color: '#333333' }}
                title={zoomed ? '退出放大' : '放大查看'}
              >
                ⛶
              </button>
            ) : null}
          </div>
        }
      />

      {running || streaming ? (
        <Centered text={streaming ? '结果集传输中（Arrow 流）…' : '执行中…'} />
      ) : !result ? (
        <Centered text="执行查询后在此展示结果" />
      ) : (
        <div className="flex flex-1 flex-col overflow-hidden">
          {/* 固定表头 */}
          <div className="flex-shrink-0 overflow-hidden" style={{ height: headerHeight }}>
            <table className="w-full border-collapse">
              <thead>
                <tr>
                  {result.columns.map((c) => (
                    <th
                      key={c}
                      className="whitespace-nowrap px-3 text-left text-2xs font-semibold"
                      style={{
                        backgroundColor: '#ececec',
                        color: '#333333',
                        borderBottom: '1px solid #d4d4d4',
                        height: headerHeight,
                        lineHeight: `${headerHeight}px`,
                        ...(zoomed ? { fontSize: '14px' } : {}),
                      }}
                    >
                      {c}
                    </th>
                  ))}
                </tr>
              </thead>
            </table>
          </div>

          {/* 虚拟滚动区域 */}
          <div ref={parentRef} className="flex-1 overflow-auto">
            <div
              style={{
                height: virtualizer.getTotalSize(),
                width: '100%',
                position: 'relative',
              }}
            >
              <table className="w-full border-collapse" style={{ position: 'absolute', top: 0, left: 0, width: '100%' }}>
                <tbody>
                  {virtualizer.getVirtualItems().map((virtualRow: { index: number; size: number; start: number }) => {
                    return (
                      <tr
                        key={virtualRow.index}
                        style={{
                          position: 'absolute',
                          top: 0,
                          left: 0,
                          width: '100%',
                          height: virtualRow.size,
                          transform: `translateY(${virtualRow.start}px)`,
                          backgroundColor: virtualRow.index % 2 ? '#f7f7f7' : '#ffffff',
                          display: 'table-row',
                        }}
                      >
                        {result.columns.map((_, ci) =>
                          renderCell(cellFor(result, virtualRow.index, ci), ci),
                        )}
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

export function PanelHeader({ title, right }: { title: string; right?: React.ReactNode }): JSX.Element {
  return (
    <div
      className="flex h-[30px] flex-shrink-0 items-center justify-between border-b px-3"
      style={{ borderColor: '#d0d0d0' }}
    >
      <span className="text-2xs font-semibold uppercase tracking-wide" style={{ color: '#333333' }}>
        {title}
      </span>
      {right}
    </div>
  );
}

function Centered({ text }: { text: string }): JSX.Element {
  return (
    <div className="flex h-full items-center justify-center text-ui" style={{ color: '#616161' }}>
      {text}
    </div>
  );
}
