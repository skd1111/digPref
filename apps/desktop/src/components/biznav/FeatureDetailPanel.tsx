/**
 * FeatureDetailPanel —— Phase 2G 业务功能点详情面板（360px 右抽屉）。
 *
 * 仿 Phase 9 CollabDrawer 模式（spec §4.2）：
 *   - 当 drawerOpen && selectedFeature → 渲染 360px 内容
 *   - 当关 → width: 0 折叠（CSS transition 220ms）
 *   - 挂在 WorkspaceLayout 顶层（mode === 'operator' 才挂载）
 */
import { useEffect, useState } from 'react';
import { useBiznavStore, selectSelectedFeature } from '@/store/biznavStore';
import { useChatStore } from '@/store/chatStore';
import { useReqcardStore } from '@/store/reqcardStore';
import { useUIStore } from '@/store/uiStore';
import { ipc } from '@/ipc/invoke';
import { STATUS_META, type CardStatus } from '@/types/reqcard';
import type { Feature, FeatureRisk } from '@/types/biznav';

const RISK_ICON: Record<FeatureRisk, string> = {
  high: '🔴',
  medium: '🟡',
  low: '🟢',
};
const RISK_COLOR: Record<FeatureRisk, string> = {
  high: '#cd3131',
  medium: '#795e26',
  low: '#059669',
};
const METHOD_COLOR: Record<string, { bg: string; fg: string }> = {
  GET: { bg: '#4ec9b022', fg: '#059669' },
  POST: { bg: '#007acc22', fg: '#007acc' },
  PUT: { bg: '#c586c022', fg: '#c586c0' },
  DELETE: { bg: '#f4877122', fg: '#cd3131' },
  PATCH: { bg: '#dcdcaa22', fg: '#795e26' },
};

function SectionTitle({
  icon,
  label,
  color,
}: {
  icon: string;
  label: string;
  color: string;
}): JSX.Element {
  return (
    <div
      className="mb-1.5 text-2xs font-semibold uppercase tracking-wider"
      style={{ color }}
    >
      {icon} {label}
    </div>
  );
}

function FeatureBody({
  feature,
  onEdit,
  onStartReq,
}: {
  feature: Feature;
  onEdit: () => void;
  onStartReq: () => void;
}): JSX.Element {
  return (
    <div className="flex-1 overflow-auto p-3" style={{ backgroundColor: '#f3f3f3' }}>
      {/* 标题 */}
      <div className="mb-3">
        <div className="mb-1 flex items-center gap-2">
          <span style={{ color: RISK_COLOR[feature.risk_level], fontSize: 12 }}>
            {RISK_ICON[feature.risk_level]}
          </span>
          <h3
            className="font-mono text-ui font-semibold"
            style={{ color: '#1f1f1f' }}
          >
            {feature.name}
          </h3>
        </div>
        <p className="text-2xs" style={{ color: '#a0a0a0', lineHeight: 1.5 }}>
          {feature.description}
        </p>
      </div>

      {/* 业务规则 */}
      {feature.business_rules.length > 0 && (
        <div className="mb-3">
          <SectionTitle icon="📋" label="业务规则" color="#dcdcaa" />
          <ul className="space-y-1">
            {feature.business_rules.map((r, i) => (
              <li key={i} className="text-2xs" style={{ color: '#1f1f1f' }}>
                • {r}
              </li>
            ))}
          </ul>
        </div>
      )}

      {/* 关联 API */}
      {feature.related_apis.length > 0 && (
        <div className="mb-3">
          <SectionTitle icon="🔌" label="关联 API" color="#569cd6" />
          {feature.related_apis.map((a, i) => {
            const c = METHOD_COLOR[a.method] ?? { bg: '#ececec', fg: '#1f1f1f' };
            return (
              <div key={i} className="mb-1 font-mono text-2xs">
                <span
                  className="mr-1 rounded px-1"
                  style={{
                    backgroundColor: c.bg,
                    color: c.fg,
                    fontSize: 10,
                    fontWeight: 600,
                  }}
                >
                  {a.method}
                </span>
                <span style={{ color: '#0b6bcb' }}>{a.path}</span>
                <div className="ml-1 mt-0.5 text-2xs" style={{ color: '#616161' }}>
                  {a.description}
                </div>
              </div>
            );
          })}
        </div>
      )}

      {/* 关联表 */}
      {feature.related_tables.length > 0 && (
        <div className="mb-3">
          <SectionTitle icon="🗄" label="关联表" color="#c586c0" />
          {feature.related_tables.map((t, i) => (
            <div key={i} className="mb-1 text-2xs">
              <span className="font-mono" style={{ color: '#0b6bcb' }}>
                {t.name}
              </span>
              <span style={{ color: '#616161' }}> · {t.description}</span>
            </div>
          ))}
        </div>
      )}

      {/* 关联文件 */}
      {feature.related_files.length > 0 && (
        <div className="mb-3">
          <SectionTitle icon="📂" label="关联文件" color="#616161" />
          {feature.related_files.map((file, i) => (
            <div
              key={i}
              className="truncate font-mono text-2xs"
              style={{ color: '#0b6bcb' }}
              title={`${file.path} · ${file.role}`}
            >
              {file.path}
              <span style={{ color: '#6a6a6a' }}> · {file.role}</span>
            </div>
          ))}
        </div>
      )}

      {/* 元数据 */}
      <div className="mb-3 rounded p-2 text-2xs" style={{ backgroundColor: '#ffffff' }}>
        <div style={{ color: '#616161' }}>
          分类：<span style={{ color: '#1f1f1f' }}>{feature.category}</span>
        </div>
        <div style={{ color: '#616161' }}>
          来源：<span style={{ color: '#1f1f1f' }}>{feature.source}</span> · v{feature.version}
        </div>
        <div style={{ color: '#616161' }}>
          更新：<span style={{ color: '#1f1f1f' }}>
            {new Date(feature.updated_at).toLocaleDateString()}
          </span>
        </div>
      </div>

      {/* 编辑 / 发起改造需求 按钮 */}
      <div className="flex gap-2">
        <button
          type="button"
          onClick={onEdit}
          className="flex-1 rounded px-3 py-1.5 text-ui font-semibold transition-colors"
          style={{ backgroundColor: '#0e639c', color: '#ffffff' }}
        >
          ✏️ 编辑此功能点
        </button>
        <button
          type="button"
          onClick={onStartReq}
          className="flex-1 rounded px-3 py-1.5 text-ui font-semibold transition-colors"
          style={{
            backgroundColor: '#ffffff',
            color: '#0451a5',
            border: '1px solid #007acc',
          }}
          title="基于此功能点与 AI 对齐改造需求，生成需求卡片"
        >
          📝 发起改造需求
        </button>
      </div>
    </div>
  );
}

export function FeatureDetailPanel(): JSX.Element {
  const feature = useBiznavStore(selectSelectedFeature);
  const drawerOpen = useBiznavStore((s) => s.drawerOpen);
  const closeDrawer = useBiznavStore((s) => s.closeDrawer);
  const openEditor = useBiznavStore((s) => s.openEditor);

  // reqflow V1：该功能点关联的需求卡片（后端按 feature_id 过滤）
  const [relatedCards, setRelatedCards] = useState<
    { id: string; title: string; status: CardStatus; batch_id: string }[]
  >([]);
  useEffect(() => {
    if (!feature) {
      setRelatedCards([]);
      return;
    }
    let cancelled = false;
    ipc
      .reqflowListCards({ featureId: feature.id })
      .then((r) => {
        if (cancelled) return;
        setRelatedCards(
          (r.cards ?? []).map((c) => ({
            id: String(c.id),
            title: String(c.title),
            status: c.status as CardStatus,
            batch_id: String(c.batch_id),
          })),
        );
      })
      .catch(() => {
        if (!cancelled) setRelatedCards([]);
      });
    return () => {
      cancelled = true;
    };
  }, [feature?.id]);

  /** 点击关联卡片 → 切到对应批次并选中，跳需求工作台 */
  const handleOpenCard = async (
    c: { id: string; batch_id: string },
  ): Promise<void> => {
    const store = useReqcardStore.getState();
    await store.selectBatch(c.batch_id);
    useReqcardStore.setState({ selectedCardId: c.id });
    useUIStore.getState().setActivityId('requirements');
  };

  /** reqflow V1：单功能点发起改造需求（上下文写入对话 + 进入对齐模式） */
  const handleStartReq = (): void => {
    if (!feature) return;
    useChatStore.getState().setAlignmentFeatures([
      {
        feature_id: feature.id,
        feature_name: feature.name,
        feature_description: feature.description,
        ...(feature.skill_id != null ? { skill_id: feature.skill_id } : {}),
        related_files: feature.related_files,
        related_apis: feature.related_apis,
        related_tables: feature.related_tables,
        business_rules: feature.business_rules,
        source: feature.source,
      },
    ]);
    useReqcardStore.getState().startAlignment(
      [feature.id],
      useBiznavStore.getState().projectName,
    );
  };

  return (
    <div
      className="flex-shrink-0 overflow-hidden transition-[width] duration-220"
      style={{ width: drawerOpen ? 360 : 0 }}
    >
      <div
        className="flex h-full flex-col border-l"
        style={{
          width: 360,
          borderColor: '#d4d4d4',
          backgroundColor: '#f3f3f3',
        }}
      >
        <div
          className="flex flex-shrink-0 items-center justify-between border-b px-3 py-2"
          style={{ borderColor: '#d4d4d4' }}
        >
          <span
            className="text-2xs font-semibold uppercase tracking-wider"
            style={{ color: '#333333' }}
          >
            🧩 功能点详情
          </span>
          <button
            type="button"
            onClick={closeDrawer}
            className="rounded px-1.5 transition-colors hover:bg-vscode-border"
            style={{ color: '#616161' }}
            title="关闭"
            aria-label="关闭"
          >
            ✕
          </button>
        </div>

        {feature ? (
          <div className="flex min-h-0 flex-1 flex-col">
            <FeatureBody
              feature={feature}
              onEdit={() => openEditor(feature.id)}
              onStartReq={handleStartReq}
            />
            {/* reqflow V1：该功能点关联的需求卡片 */}
            {relatedCards.length > 0 && (
              <div
                className="flex-shrink-0 border-t p-3"
                style={{ borderColor: '#d4d4d4' }}
              >
                <div
                  className="mb-1 text-2xs font-semibold uppercase tracking-wider"
                  style={{ color: '#0451a5' }}
                >
                  📋 关联需求卡片
                </div>
                {relatedCards.map((c) => (
                  <button
                    key={c.id}
                    type="button"
                    onClick={() => void handleOpenCard(c)}
                    className="mb-1 flex w-full items-center gap-1 rounded border px-2 py-1 text-left text-2xs transition-colors hover:brightness-95"
                    style={{
                      backgroundColor: '#ffffff',
                      borderColor: '#e0e0e0',
                    }}
                    title="在需求工作台打开"
                  >
                    <span className="font-mono" style={{ color: '#0451a5' }}>
                      {c.id}
                    </span>
                    <span className="flex-1 truncate" style={{ color: '#1f1f1f' }}>
                      {c.title}
                    </span>
                    <span style={{ color: STATUS_META[c.status].color }}>
                      {STATUS_META[c.status].icon} {STATUS_META[c.status].label}
                    </span>
                  </button>
                ))}
              </div>
            )}
          </div>
        ) : (
          <div
            className="flex h-full flex-col items-center justify-center p-6 text-center text-2xs"
            style={{ color: '#616161' }}
          >
            ← 从左侧选择业务功能点
          </div>
        )}
      </div>
    </div>
  );
}
