/**
 * FeatureDetailPanel —— Phase 2G 业务功能点详情面板（360px 右抽屉）。
 *
 * 仿 Phase 9 CollabDrawer 模式（spec §4.2）：
 *   - 当 drawerOpen && selectedFeature → 渲染 360px 内容
 *   - 当关 → width: 0 折叠（CSS transition 220ms）
 *   - 挂在 WorkspaceLayout 顶层（mode === 'operator' 才挂载）
 */
import { useCallback, useState } from 'react';
import { useBiznavStore, selectSelectedFeature } from '@/store/biznavStore';
import type { Feature, FeatureRisk } from '@/types/biznav';

// Phase 2G V0 收尾 (2026-07-28): 演示数据横幅 + sessionStorage 持久化
// 提醒用户当前 18 mock 不持久化（修改后重启清空），V1 接后端后会从所选项目自动加载
const DEMO_BANNER_KEY = 'biznav.demoBannerDismissed.v1';

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
}: {
  feature: Feature;
  onEdit: () => void;
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

      {/* 编辑按钮 */}
      <button
        type="button"
        onClick={onEdit}
        className="w-full rounded px-3 py-1.5 text-ui font-semibold transition-colors"
        style={{ backgroundColor: '#0e639c', color: '#ffffff' }}
      >
        ✏️ 编辑此功能点
      </button>
    </div>
  );
}

export function FeatureDetailPanel(): JSX.Element {
  // Phase 2G V0 收尾 (2026-07-28): demo banner 状态 + 关闭回调
  // hooks 必须在 early-return 之前无条件调用（BUGFIX #15 教训）；本组件无 early-return
  const [showDemoBanner, setShowDemoBanner] = useState<boolean>(() => {
    try {
      return sessionStorage.getItem(DEMO_BANNER_KEY) !== '1';
    } catch {
      // Tauri WebView 偶发 sessionStorage 不可用（隐私模式 / 磁盘满）
      // 优雅降级：默认显示横幅
      return true;
    }
  });
  const dismissDemoBanner = useCallback((): void => {
    try {
      sessionStorage.setItem(DEMO_BANNER_KEY, '1');
    } catch {
      // 写失败忽略，保持横幅显示
    }
    setShowDemoBanner(false);
  }, []);

  const feature = useBiznavStore(selectSelectedFeature);
  const drawerOpen = useBiznavStore((s) => s.drawerOpen);
  const closeDrawer = useBiznavStore((s) => s.closeDrawer);
  const openEditor = useBiznavStore((s) => s.openEditor);

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
          <>
            {/* Phase 2G V0 收尾 (2026-07-28): 演示数据横幅 + [×] 关闭
             * sessionStorage 而非 localStorage —— 刷新页面 / 重启进程会重现
             * 避免用户忘记这是 mock 而修改后预期落盘 */}
            {showDemoBanner && (
              <div
                className="flex flex-shrink-0 items-start gap-2 rounded m-3 p-2 text-2xs"
                style={{
                  backgroundColor: '#dcdcaa20',
                  border: '1px solid #dcdcaa',
                }}
              >
                <span style={{ color: '#795e26' }}>⚠️</span>
                <div className="flex-1" style={{ color: '#1f1f1f' }}>
                  <strong>V0 演示数据</strong>：当前显示的是 18 个 mock 功能点，
                  修改仅保存在前端内存（重启清空）。V1 接后端后会从所选项目自动加载真实功能点。
                </div>
                <button
                  type="button"
                  onClick={dismissDemoBanner}
                  className="rounded px-1 text-2xs"
                  style={{ color: '#616161' }}
                  title="关闭提示"
                >
                  ✕
                </button>
              </div>
            )}
            <FeatureBody feature={feature} onEdit={() => openEditor(feature.id)} />
          </>
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
