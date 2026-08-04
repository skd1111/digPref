/**
 * BusinessFeatureTree —— 业务功能点树（运营专家模式专属）。
 *
 * Phase 2G V0 改造：
 *   - 读 useBiznavStore（删 140 行 DEMO_FEATURES 静态数组）
 *   - 单击 feature 节点：toggle 展开 + openDrawer（抽屉接管详情）
 *   - 顶部右上角加 2 个 icon 按钮：[↻ 重建索引] + [✏️ 编辑器]
 *   - 选中态高亮（仿 2F CodeNavSearch 选中态 #094771 + 3px #007acc border）
 *
 * Hook 顺序约束（BUGFIX #15 教训）：所有 hook 无条件在 early-return 之前调用。
 */
import { useMemo, useState } from 'react';
import { useEnvStore } from '@/store/envStore';
import {
  useBiznavStore,
  selectFeaturesByCategory,
  selectStats,
} from '@/store/biznavStore';

const RISK_ICON = { high: '🔴', medium: '🟡', low: '🟢' } as const;
const RISK_COLOR = { high: '#cd3131', medium: '#795e26', low: '#059669' } as const;

export function BusinessFeatureTree(): JSX.Element {
  // ===== 所有 hook 必须无条件在 early-return 之前（BUGFIX #15 教训）=====
  const activeEnv = useEnvStore((s) => s.activeEnv);
  const features = useBiznavStore((s) => s.features);
  const selectedFeatureId = useBiznavStore((s) => s.selectedFeatureId);
  const reindex = useBiznavStore((s) => s.reindex);
  const openEditor = useBiznavStore((s) => s.openEditor);
  const openDrawer = useBiznavStore((s) => s.openDrawer);

  const grouped = useBiznavStore(selectFeaturesByCategory);
  const stats = useBiznavStore(selectStats);

  // 本地 UI 状态（不进 store）
  const [expandedCategories, setExpandedCategories] = useState<Set<string>>(
    () => new Set(Array.from(grouped.keys()))
  );
  const [expandedFeatures, setExpandedFeatures] = useState<Set<string>>(
    new Set(['order_create'])
  );

  // 重新计算展开分类：features 变化时补齐新增分类的默认展开
  const expandedCategoriesList = useMemo(() => {
    const s = new Set(expandedCategories);
    for (const k of grouped.keys()) s.add(k);
    return Array.from(s);
  }, [expandedCategories, grouped]);

  // ===== Hook 完毕，再 early-return =====
  if (features.length === 0) {
    return (
      <div
        className="flex h-full flex-col items-center justify-center p-6 text-center text-2xs"
        style={{ color: '#616161', backgroundColor: '#f3f3f3' }}
      >
        <div className="mb-2 text-3xl">🧩</div>
        <div>暂无业务功能点</div>
        <div className="mt-1">V1 接后端后可加载真实项目</div>
      </div>
    );
  }

  const toggleCategory = (c: string): void => {
    setExpandedCategories((prev) => {
      const next = new Set(prev);
      if (next.has(c)) next.delete(c);
      else next.add(c);
      return next;
    });
  };

  const toggleFeature = (id: string): void => {
    setExpandedFeatures((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
    openDrawer(id);
  };

  const handleEditClick = (): void => {
    if (selectedFeatureId) {
      openEditor(selectedFeatureId);
    }
  };

  return (
    <div
      className="business-feature-tree flex h-full flex-col"
      style={{ backgroundColor: '#f3f3f3' }}
    >
      {/* 顶部统计 + 操作按钮 */}
      <div
        className="flex-shrink-0 border-b px-3 py-2"
        style={{ borderColor: '#d4d4d4' }}
      >
        <div className="mb-2 flex items-center justify-between">
          <h3
            className="text-ui font-semibold uppercase tracking-wider"
            style={{ color: '#333333' }}
          >
            🧩 业务功能点
          </h3>
          <div className="flex items-center gap-1">
            {activeEnv && (
              <span
                className="rounded px-1.5 py-0.5 text-2xs font-mono"
                style={{ backgroundColor: '#ececec', color: '#6e6e6e' }}
              >
                {activeEnv}
              </span>
            )}
            <button
              type="button"
              onClick={reindex}
              className="rounded px-1.5 py-0.5 text-2xs transition-colors hover:bg-vscode-border"
              style={{ color: '#0451a5' }}
              title="重建索引"
            >
              ↻
            </button>
            <button
              type="button"
              onClick={handleEditClick}
              disabled={!selectedFeatureId}
              className="rounded px-1.5 py-0.5 text-2xs transition-colors"
              style={{
                color: selectedFeatureId ? '#0451a5' : '#333333',
                cursor: selectedFeatureId ? 'pointer' : 'not-allowed',
              }}
              title={selectedFeatureId ? '编辑当前选中功能点' : '请先选中一个功能点'}
            >
              ✏️
            </button>
          </div>
        </div>
        <div className="grid grid-cols-3 gap-2 text-2xs" style={{ color: '#616161' }}>
          <Stat label="分类" value={stats.categories} />
          <Stat label="功能" value={stats.features} color="#4ec9b0" />
          <Stat label="API" value={stats.apis} color="#569cd6" />
          <Stat label="表" value={stats.tables} color="#c586c0" />
          <Stat label="规则" value={stats.rules} color="#dcdcaa" />
          {stats.highRisk > 0 && (
            <Stat label="高危" value={stats.highRisk} color="#f48771" />
          )}
        </div>
      </div>

      {/* 分类树 */}
      <div className="flex-1 overflow-auto p-1">
        {expandedCategoriesList.map((category) => {
          const items = grouped.get(category) ?? [];
          const catOpen = expandedCategories.has(category);
          return (
            <div key={category} className="mb-1">
              <button
                type="button"
                onClick={() => toggleCategory(category)}
                className="flex w-full items-center gap-1.5 rounded px-2 py-1 text-ui font-semibold transition-colors hover:bg-vscode-border"
                style={{ color: '#333333' }}
              >
                <span
                  className="transition-transform duration-150"
                  style={{ transform: catOpen ? 'rotate(90deg)' : 'rotate(0deg)' }}
                >
                  ▶
                </span>
                <span>📁</span>
                <span className="flex-1 text-left">{category}</span>
                <span
                  className="rounded px-1.5 text-2xs"
                  style={{ backgroundColor: '#ffffff', color: '#6e6e6e' }}
                >
                  {items.length}
                </span>
              </button>

              {catOpen && (
                <div className="ml-3 mt-0.5 space-y-0.5">
                  {items.map((f) => {
                    const fOpen = expandedFeatures.has(f.id);
                    const selected = f.id === selectedFeatureId;
                    return (
                      <div key={f.id}>
                        <button
                          type="button"
                          onClick={() => toggleFeature(f.id)}
                          className="flex w-full items-center gap-1.5 rounded px-2 py-1 text-ui transition-colors"
                          style={{
                            backgroundColor: selected ? '#0e639c' : 'transparent',
                            borderLeft: selected
                              ? '3px solid #007acc'
                              : '3px solid transparent',
                            color: '#1f1f1f',
                          }}
                        >
                          <span
                            className="transition-transform duration-150"
                            style={{
                              transform: fOpen ? 'rotate(90deg)' : 'rotate(0deg)',
                            }}
                          >
                            ▶
                          </span>
                          <span
                            style={{ color: RISK_COLOR[f.risk_level], fontSize: 10 }}
                          >
                            {RISK_ICON[f.risk_level]}
                          </span>
                          <span className="flex-1 text-left">{f.name}</span>
                          {f.related_apis.length > 0 && (
                            <span
                              className="rounded px-1 font-mono text-2xs"
                              style={{
                                backgroundColor: '#ffffff',
                                color: '#0451a5',
                                fontSize: 9,
                              }}
                            >
                              {f.related_apis.length} API
                            </span>
                          )}
                        </button>

                        {fOpen && (
                          <div
                            className="ml-5 mt-1 space-y-1 border-l-2 pl-2"
                            style={{ borderColor: '#d4d4d4' }}
                          >
                            <p
                              className="text-2xs"
                              style={{ color: '#a0a0a0', lineHeight: 1.5 }}
                            >
                              {f.description}
                            </p>
                            {f.business_rules.length > 0 && (
                              <div>
                                <div
                                  className="mb-0.5 text-2xs font-semibold uppercase tracking-wider"
                                  style={{ color: '#795e26' }}
                                >
                                  📋 业务规则
                                </div>
                                <ul className="space-y-0.5">
                                  {f.business_rules.map((r, i) => (
                                    <li
                                      key={i}
                                      className="text-2xs"
                                      style={{ color: '#1f1f1f' }}
                                    >
                                      • {r}
                                    </li>
                                  ))}
                                </ul>
                              </div>
                            )}
                          </div>
                        )}
                      </div>
                    );
                  })}
                </div>
              )}
            </div>
          );
        })}
      </div>

      {/* 底部提示 */}
      <div
        className="flex-shrink-0 border-t px-3 py-1.5 text-2xs"
        style={{ borderColor: '#d4d4d4', color: '#616161' }}
      >
        业务功能点 · 数据来自后端
      </div>
    </div>
  );
}

function Stat({
  label,
  value,
  color = '#333333',
}: {
  label: string;
  value: number;
  color?: string;
}): JSX.Element {
  return (
    <div className="flex items-baseline gap-1">
      <span style={{ color }}>{value}</span>
      <span>{label}</span>
    </div>
  );
}
