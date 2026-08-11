/**
 * BusinessFeatureTree —— 业务功能点树（运营专家模式专属）。
 *
 * Phase 2G V0 改造：
 *   - 读 useBiznavStore（删 140 行 DEMO_FEATURES 静态数组）
 *   - 单击 feature 节点：toggle 展开 + openDrawer（抽屉接管详情）
 *   - 顶部右上角加 2 个 icon 按钮：[↻ 重建索引] + [✏️ 编辑器]
 *   - 选中态高亮（仿 2F CodeNavSearch 选中态 #094771 + 3px #007acc border）
 *
 * 导入工程提炼（2026-08-04）：
 *   - 空态与头部均有「导入工程」按钮：选目录 → addOpenedProject →
 *     biznavStore.importProjectAndExtract（后端 AI 提取 + 轮询）→ 自动展示
 *
 * Hook 顺序约束（BUGFIX #15 教训）：所有 hook 无条件在 early-return 之前调用。
 */
import { useEffect, useMemo, useState } from 'react';
import { open } from '@tauri-apps/plugin-dialog';
import { useEnvStore } from '@/store/envStore';
import { useCodeNavStore } from '@/store/codeNavStore';
import { useChatStore } from '@/store/chatStore';
import { useReqcardStore } from '@/store/reqcardStore';
import type { FeatureContextPayload } from '@/types/biznav';
import {
  useBiznavStore,
  selectFeaturesByCategory,
  selectStats,
} from '@/store/biznavStore';

const RISK_ICON = { high: '🔴', medium: '🟡', low: '🟢' } as const;
const RISK_COLOR = { high: '#cd3131', medium: '#795e26', low: '#059669' } as const;

/** 后台提取阶段 → 中文提示（不写死后端：降级链本地→内网→云端自动切换） */
const EXTRACT_STAGE_TEXT: Record<string, string> = {
  pending: '提取任务排队中…',
  scanning: '正在扫描工程代码…',
  extracting: 'AI 正在提炼业务功能点，可能需要几分钟…',
};

export function BusinessFeatureTree(): JSX.Element {
  // ===== 所有 hook 必须无条件在 early-return 之前（BUGFIX #15 教训）=====
  const activeEnv = useEnvStore((s) => s.activeEnv);
  const features = useBiznavStore((s) => s.features);
  const selectedFeatureId = useBiznavStore((s) => s.selectedFeatureId);
  const reindex = useBiznavStore((s) => s.reindex);
  const openEditor = useBiznavStore((s) => s.openEditor);
  const openDrawer = useBiznavStore((s) => s.openDrawer);
  const loadFeatures = useBiznavStore((s) => s.loadFeatures);
  const importProjectAndExtract = useBiznavStore((s) => s.importProjectAndExtract);
  const extracting = useBiznavStore((s) => s.extracting);
  const extractStage = useBiznavStore((s) => s.extractStage);
  const extractError = useBiznavStore((s) => s.extractError);
  const extractProgress = useBiznavStore((s) => s.extractProgress);
  const openedProjects = useCodeNavStore((s) => s.openedProjects);

  // 挂载时 + open/关闭工程变化时自动拉取后端功能点（不传 project_name → 跨项目全量，
  // 避免因提取时 project_name 与工程目录名不一致而查不到）。此前仅靠 SSE 事件触发，
  // 而 BIZNAV_EXTRACTION_DONE 事件 V1.3 才真发 → 列表始终为空。
  useEffect(() => {
    void loadFeatures();
  }, [loadFeatures, openedProjects]);

  /** 导入工程 → AI 提炼功能点：选目录 → 加入已打开工程 → 触发后端提取 */
  const handleImportProject = async (): Promise<void> => {
    if (extracting) return;
    let selected: string | string[] | null = null;
    try {
      selected = await open({
        multiple: false,
        directory: true,
        title: '选择工程文件夹（AI 提炼业务功能点）',
      });
    } catch (e) {
      // eslint-disable-next-line no-console
      console.error('[BusinessFeatureTree] folder dialog failed:', e);
      window.alert(`打开文件夹对话框失败：${String(e)}\n请确认在 Tauri 桌面端运行（非浏览器）。`);
      return;
    }
    if (!selected) return; // 用户取消
    const folder = Array.isArray(selected) ? selected[0] : selected;
    if (!folder) return;
    // 同步加入左侧工程文件树（去重由 store 内部保证）
    useCodeNavStore.getState().addOpenedProject(folder);
    void importProjectAndExtract(folder);
  };

  // ===== reqflow V1：多选功能点发起改造需求 =====
  const [selectedForReq, setSelectedForReq] = useState<Set<string>>(new Set());

  const toggleReqSelect = (id: string): void => {
    setSelectedForReq((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  };

  /** 发起改造需求：多功能点上下文写入对话 + 进入对齐模式 */
  const handleStartAlignment = (): void => {
    const ids = Array.from(selectedForReq);
    if (ids.length === 0) return;
    const payloads: FeatureContextPayload[] = ids
      .map((id) => features.find((f) => f.id === id))
      .filter((f): f is NonNullable<typeof f> => Boolean(f))
      .map((f) => ({
        feature_id: f.id,
        feature_name: f.name,
        feature_description: f.description,
        ...(f.skill_id != null ? { skill_id: f.skill_id } : {}),
        related_files: f.related_files,
        related_apis: f.related_apis,
        related_tables: f.related_tables,
        business_rules: f.business_rules,
        source: f.source,
      }));
    useChatStore.getState().setAlignmentFeatures(payloads);
    useReqcardStore.getState().startAlignment(ids, useBiznavStore.getState().projectName);
    setSelectedForReq(new Set());
  };

  const groupedRaw = useBiznavStore(selectFeaturesByCategory);

  // 功能点搜索：按 名称 / 分类 / 描述 过滤（不区分大小写）
  const [searchQuery, setSearchQuery] = useState('');

  const grouped = useMemo(() => {
    const q = searchQuery.trim().toLowerCase();
    if (!q) return groupedRaw;
    const m = new Map<string, typeof features>();
    for (const [cat, items] of groupedRaw) {
      const filtered = items.filter(
        (f) =>
          f.name.toLowerCase().includes(q) ||
          f.category.toLowerCase().includes(q) ||
          f.description.toLowerCase().includes(q)
      );
      if (filtered.length > 0) m.set(cat, filtered);
    }
    return m;
  }, [groupedRaw, searchQuery, features]);

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
  if (features.length === 0 && !extracting) {
    return (
      <div
        className="flex h-full flex-col items-center justify-center p-6 text-center text-2xs"
        style={{ color: '#616161', backgroundColor: '#f3f3f3' }}
      >
        <div className="mb-2 text-3xl">🧩</div>
        <div>暂无业务功能点</div>
        <div className="mt-1">导入工程后由 AI 自动提炼，或导入 YAML</div>
        {extractError && (
          <div className="mt-2 max-w-[220px] break-all" style={{ color: '#cd3131' }}>
            ⚠ {extractError}
          </div>
        )}
        <button
          type="button"
          onClick={() => void handleImportProject()}
          className="mt-4 rounded px-3 py-1.5 text-ui font-semibold transition-colors hover:brightness-110"
          style={{ backgroundColor: '#0e639c', color: '#ffffff' }}
          title="选择工程文件夹，AI 自动提炼业务功能点"
        >
          📁 导入工程提炼功能点
        </button>
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
              onClick={() => void handleImportProject()}
              disabled={extracting}
              className="rounded px-1.5 py-0.5 text-2xs transition-colors hover:bg-vscode-border"
              style={{
                color: extracting ? '#a0a0a0' : '#0451a5',
                cursor: extracting ? 'wait' : 'pointer',
              }}
              title="导入工程，AI 提炼业务功能点"
            >
              📁
            </button>
            {/* reqflow V1：勾选功能点后发起改造需求 */}
            <button
              type="button"
              onClick={handleStartAlignment}
              disabled={selectedForReq.size === 0}
              className="rounded px-1.5 py-0.5 text-2xs transition-colors hover:bg-vscode-border"
              style={{
                color: selectedForReq.size > 0 ? '#0451a5' : '#a0a0a0',
                cursor: selectedForReq.size > 0 ? 'pointer' : 'not-allowed',
              }}
              title={
                selectedForReq.size > 0
                  ? `对已勾选的 ${selectedForReq.size} 个功能点发起改造需求`
                  : '先勾选功能点（列表左侧 ☐），再发起改造需求'
              }
            >
              📝{selectedForReq.size > 0 ? ` ${selectedForReq.size}` : ''}
            </button>
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

        {/* 功能点搜索 */}
        <div className="relative mt-2">
          <input
            type="text"
            value={searchQuery}
            onChange={(e) => setSearchQuery(e.target.value)}
            placeholder="🔍 搜索功能点…"
            className="w-full rounded border px-2 py-1 text-2xs outline-none focus:border-[#007acc]"
            style={{
              backgroundColor: '#ffffff',
              borderColor: '#d4d4d4',
              color: '#1f1f1f',
            }}
          />
          {searchQuery && (
            <button
              type="button"
              onClick={() => setSearchQuery('')}
              className="absolute right-1 top-1/2 -translate-y-1/2 rounded px-1 text-2xs hover:bg-vscode-border"
              style={{ color: '#616161' }}
              title="清除搜索"
            >
              ✕
            </button>
          )}
        </div>
      </div>

      {/* AI 提取进度 / 失败提示横幅（含进度条） */}
      {(extracting || extractError) && (
        <div
          className="flex-shrink-0 border-b px-3 py-1.5 text-2xs"
          style={{
            borderColor: '#d4d4d4',
            backgroundColor: '#f8f8f8',
            color: extracting ? '#0451a5' : '#cd3131',
          }}
        >
          <div>
            {extracting
              ? `⏳ ${EXTRACT_STAGE_TEXT[extractStage ?? 'pending'] ?? extractStage}${
                  extractProgress !== null ? ` ${extractProgress}%` : ''
                }`
              : `⚠ ${extractError}`}
          </div>
          {extracting && (
            <div
              className="mt-1 h-1 w-full overflow-hidden rounded"
              style={{ backgroundColor: '#e0e0e0' }}
            >
              <div
                className={`h-full transition-all duration-700 ease-out${
                  extractProgress === null ? ' animate-pulse' : ''
                }`}
                style={{
                  backgroundColor: '#007acc',
                  // pending/scanning 阶段无百分比 → 细条呼吸动画提示进行中
                  width: `${extractProgress ?? 4}%`,
                }}
              />
            </div>
          )}
        </div>
      )}

      {/* 分类树 */}
      <div className="flex-1 overflow-auto p-1">
        {expandedCategoriesList.length === 0 && searchQuery.trim() && (
          <div
            className="px-2 py-4 text-center text-2xs"
            style={{ color: '#616161' }}
          >
            没有匹配「{searchQuery.trim()}」的功能点
          </div>
        )}
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
                          {/* reqflow V1 多选勾选框（发起改造需求用） */}
                          <span
                            role="checkbox"
                            aria-checked={selectedForReq.has(f.id)}
                            aria-label={`勾选 ${f.name} 发起改造需求`}
                            onClick={(e) => {
                              e.stopPropagation();
                              toggleReqSelect(f.id);
                            }}
                            className="cursor-pointer select-none"
                            style={{
                              color: selectedForReq.has(f.id)
                                ? '#007acc'
                                : '#a0a0a0',
                              fontSize: 11,
                            }}
                          >
                            {selectedForReq.has(f.id) ? '☑' : '☐'}
                          </span>
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
