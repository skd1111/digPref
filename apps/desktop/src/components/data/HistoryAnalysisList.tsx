/**
 * HistoryAnalysisList —— 数据专家左栏第三段：历史分析列表（缺口 9）。
 *
 * 数据来源：后端 GET /data/tasks（analysis_tasks 表，经 Rust data_list_tasks）。
 * 交互：挂载时拉取；点击历史项 → 回填 SQL 到 QueryEditor 可重跑。
 */
import { useEffect } from 'react';
import { useDataStore } from '@/store/dataStore';
import { PanelHeader } from './DataGrid';

export function HistoryAnalysisList(): JSX.Element {
  const history = useDataStore((s) => s.history);
  const fetchHistory = useDataStore((s) => s.fetchHistory);
  const loadHistory = useDataStore((s) => s.loadHistory);

  useEffect(() => {
    void fetchHistory();
  }, [fetchHistory]);

  return (
    <div className="flex h-full flex-col overflow-hidden" style={{ backgroundColor: '#ffffff' }}>
      <PanelHeader
        title="💡 历史分析"
        right={
          <button
            type="button"
            onClick={() => void fetchHistory()}
            className="rounded px-1.5 text-2xs transition-all hover:brightness-90"
            style={{ color: '#0e639c' }}
            title="刷新"
          >
            ⟳
          </button>
        }
      />
      <div className="flex-1 overflow-auto">
        {history.length === 0 ? (
          <div className="flex h-full items-center justify-center text-2xs" style={{ color: '#8e8e8e' }}>
            暂无历史分析（执行查询后自动记录）
          </div>
        ) : (
          <ul>
            {history.map((h) => (
              <li key={h.id}>
                <button
                  type="button"
                  onClick={() => loadHistory(h.id)}
                  className="w-full border-b px-3 py-2 text-left transition-all hover:bg-[#f0f6fc]"
                  style={{ borderColor: '#eeeeee' }}
                  title={h.querySql}
                >
                  <div className="truncate text-ui font-medium" style={{ color: '#1f1f1f' }}>
                    {h.name}
                  </div>
                  <div className="mt-0.5 flex items-center justify-between text-2xs" style={{ color: '#8e8e8e' }}>
                    <span>{h.rowCount.toLocaleString()} 行</span>
                    <span>{h.createdAt}</span>
                  </div>
                </button>
              </li>
            ))}
          </ul>
        )}
      </div>
    </div>
  );
}
