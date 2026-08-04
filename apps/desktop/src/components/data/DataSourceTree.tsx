/**
 * DataSourceTree —— 数据专家左栏：数据源 + 表结构/数据字典 + 历史分析。
 *
 * V1 升级：数据源来自「系统资产」中 type='database' 的配置（systems.yaml），
 * 不再独立维护 data_expert.db 的 data_sources 表。
 * 表结构/数据字典仍从后端 Schema 同步获取（dataSyncSchema）。
 */
import { useEffect, useState, useMemo } from 'react';
import { useAssetStore } from '@/store/assetStore';
import { useDataStore, type SourceType, type DataSource } from '@/store/dataStore';

const TYPE_BADGE: Record<string, { label: string; color: string }> = {
  mysql: { label: 'MySQL', color: '#00758f' },
  oracle: { label: 'Oracle', color: '#c74634' },
  postgres: { label: 'PG', color: '#336791' },
  sqlite: { label: 'SQLite', color: '#003b57' },
  csv: { label: 'CSV', color: '#059669' },
  excel: { label: 'Excel', color: '#217346' },
};

export function DataSourceTree(): JSX.Element {
  // 从系统资产获取数据库列表
  const assetTree = useAssetStore((s) => s.tree);
  const assetRefresh = useAssetStore((s) => s.refresh);

  const history = useDataStore((s) => s.history);
  const selectedSourceId = useDataStore((s) => s.selectedSourceId);
  const selectedTable = useDataStore((s) => s.selectedTable);
  const selectSource = useDataStore((s) => s.selectSource);
  const selectTable = useDataStore((s) => s.selectTable);
  const loadHistory = useDataStore((s) => s.loadHistory);
  const sources = useDataStore((s) => s.sources);
  const loading = useDataStore((s) => s.loading);
  const error = useDataStore((s) => s.error);
  const fetchSources = useDataStore((s) => s.fetchSources);

  // 挂载时加载系统资产 + 后端 schema
  useEffect(() => {
    assetRefresh();
    fetchSources();
  }, [assetRefresh, fetchSources]);

  // 从系统资产过滤 database 类型，合并后端 schema 信息
  const dbSources: DataSource[] = useMemo(() => {
    const dbAssets = assetTree.filter((a) => a.type === 'database');
    if (dbAssets.length === 0) {
      // 降级：如果系统资产为空，用后端 data_expert.db 的数据源
      return sources;
    }
    return dbAssets.map((a) => {
      // 尝试匹配后端已同步的 schema
      const backendSrc = sources.find((s) => s.id === a.id || s.name === a.label);
      const dbType = String(a.meta.db_type || a.meta.kind || 'mysql');
      return {
        id: a.id,
        name: a.label,
        type: (dbType as SourceType) || 'mysql',
        status: (backendSrc?.status ?? 'offline') as 'connected' | 'offline',
        tables: backendSrc?.tables ?? [],
      };
    });
  }, [assetTree, sources]);

  return (
    <div className="flex h-full flex-col overflow-hidden" style={{ backgroundColor: '#f3f3f3' }}>
      {/* 数据源 + 表结构 */}
      <Section title="📊 数据源 / 表结构">
        {loading && (
          <div className="px-3 py-2 text-2xs" style={{ color: '#616161' }}>加载中…</div>
        )}
        {error && (
          <div className="px-3 py-2 text-2xs" style={{ color: '#cd3131' }}>⚠ {error}</div>
        )}
        {!loading && dbSources.length === 0 && !error && (
          <div className="px-3 py-2 text-2xs" style={{ color: '#616161' }}>
            暂无数据库资产（请在左侧「系统资产」中新增数据库配置）
          </div>
        )}
        {dbSources.map((src) => {
          const active = src.id === selectedSourceId;
          const badge = TYPE_BADGE[src.type] ?? TYPE_BADGE.mysql;
          return (
            <div key={src.id}>
              <button
                type="button"
                onClick={() => selectSource(src.id)}
                className="flex w-full items-center gap-2 px-3 py-1.5 text-ui transition-colors"
                style={{
                  color: active ? '#ffffff' : '#333333',
                  backgroundColor: active ? '#0e639c' : 'transparent',
                }}
              >
                <span
                  aria-hidden
                  style={{
                    width: 7,
                    height: 7,
                    borderRadius: '50%',
                    backgroundColor: src.status === 'connected' ? '#059669' : '#cd3131',
                    flexShrink: 0,
                  }}
                />
                <span className="flex-1 truncate text-left">{src.name}</span>
                <span
                  className="rounded px-1 text-2xs"
                  style={{ backgroundColor: badge.color, color: '#fff', fontSize: 10 }}
                >
                  {badge.label}
                </span>
              </button>

              {/* 展开：表 + 字段字典 */}
              {active &&
                src.tables.map((tbl) => {
                  const tblActive = tbl.name === selectedTable;
                  return (
                    <div key={tbl.name}>
                      <button
                        type="button"
                        onClick={() => selectTable(tblActive ? null : tbl.name)}
                        className="flex w-full items-center gap-1 py-1 pl-7 pr-3 text-ui transition-colors hover:brightness-125"
                        style={{ color: tblActive ? '#059669' : '#0b6bcb' }}
                        title={tbl.comment}
                      >
                        <span aria-hidden style={{ fontSize: 10 }}>{tblActive ? '▾' : '▸'}</span>
                        <span className="truncate font-mono">{tbl.name}</span>
                        <span className="truncate text-2xs" style={{ color: '#6a9955' }}>
                          {tbl.comment}
                        </span>
                      </button>
                      {/* 数据字典：字段 + 中文注释 */}
                      {tblActive && (
                        <ul className="pb-1">
                          {tbl.columns.map((col) => (
                            <li
                              key={col.name}
                              className="flex items-baseline gap-2 py-0.5 pl-12 pr-3 text-2xs"
                              title={col.comment}
                            >
                              <span className="font-mono" style={{ color: '#795e26' }}>
                                {col.name}
                              </span>
                              <span style={{ color: '#0451a5' }}>{col.type}</span>
                              <span className="truncate" style={{ color: '#616161' }}>
                                {col.comment}
                              </span>
                            </li>
                          ))}
                        </ul>
                      )}
                    </div>
                  );
                })}
            </div>
          );
        })}
      </Section>

      {/* 历史分析 */}
      <Section title="💡 历史分析">
        {history.length === 0 && (
          <div className="px-3 py-2 text-2xs" style={{ color: '#616161' }}>暂无历史分析</div>
        )}
        {history.map((h) => (
          <button
            key={h.id}
            type="button"
            onClick={() => loadHistory(h.id)}
            className="flex w-full flex-col items-start px-3 py-1.5 text-left transition-colors hover:brightness-125"
            style={{ color: '#333333' }}
          >
            <span className="truncate text-ui">{h.name}</span>
            <span className="text-2xs" style={{ color: '#616161' }}>{h.createdAt}</span>
          </button>
        ))}
      </Section>
    </div>
  );
}

function Section({ title, children }: { title: string; children: React.ReactNode }): JSX.Element {
  const [open, setOpen] = useState(true);
  return (
    <div className="flex flex-col overflow-hidden border-b" style={{ borderColor: '#e0e0e0' }}>
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        className="flex h-[30px] flex-shrink-0 items-center gap-1 px-2 text-2xs font-semibold uppercase tracking-wide"
        style={{ color: '#333333' }}
      >
        <span aria-hidden style={{ fontSize: 10 }}>{open ? '▾' : '▸'}</span>
        <span>{title}</span>
      </button>
      {open && <div className="overflow-auto pb-1">{children}</div>}
    </div>
  );
}
