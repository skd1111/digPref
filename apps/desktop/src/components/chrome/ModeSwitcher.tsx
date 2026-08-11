/**
 * ModeSwitcher —— 顶部页签切换器（Phase 2E + Phase 5 + Phase 2H）：
 *   开发模式 | 运营模式（独立页签，与开发模式并列）| 审核专家模式 | 数据专家模式
 *
 * 设计要点（用户要求"切换动作要丝滑"）：
 *   - 视觉形态：VSCode 风格页签（large 态圆角胶囊 / 紧凑态下划线）
 *   - 平滑过渡：底部高亮条用 CSS transform 滑动 (150ms ease-out)
 *   - 状态保留：uiStore.mode 通过 Zustand persist 持久化到 localStorage
 *   - 切到 operator / auditor / analyst 首次弹确认框（独立"不再提示"开关）
 *   - 旧版 'audit' 自动迁移为 'auditor'（Phase 5 改名）
 *   - Phase 2H 收尾（用户要求）：运营模式恢复为独立顶级页签（与开发模式并列），
 *     进入后全屏渲染运营工作台（业务列表 + Chat + 工作台）
 */
import { useState } from 'react';
import { useUIStore, type WorkMode } from '@/store/uiStore';
import {
  AdvancedModeConfirmDialog,
  AUDITOR_MODE_CONTENT,
  DATA_MODE_CONTENT,
  OPERATOR_MODE_CONTENT,
  type AdvancedModeDialogContent,
} from './OperatorModeConfirmDialog';

interface TabDef {
  id: WorkMode;
  label: string;
  hint: string;
}

const TABS: TabDef[] = [
  { id: 'full', label: '开发模式', hint: '4 象限：资源 + 对话 + Trace + 终端' },
  {
    id: 'operator',
    label: '运营模式',
    hint: '运营工作台：业务列表 + Chat + 工作台（Skill 承载功能点）',
  },
  {
    id: 'auditor',
    label: '审核专家模式',
    hint: '3 栏：审批工作台 + Monaco Diff 详情 + 审计与合规（金融级审批）',
  },
  {
    id: 'analyst',
    label: '数据专家模式',
    hint: '4 象限：数据源 + SQL/Python/对话 + 数据网格 + 图表（NL2SQL 智能分析）',
  },
];

/** 各专家模式的主题色（区别于默认蓝） */
const ACCENT: Partial<Record<WorkMode, string>> = {
  auditor: '#b25c1a', // 暖橙 — 审核
  analyst: '#059669', // 青绿 — 数据
};

function accentOf(id: WorkMode): string {
  return ACCENT[id] ?? '#007acc';
}

export function ModeSwitcher({ large = false }: { large?: boolean }): JSX.Element {
  const mode = useUIStore((s) => s.mode);
  const setMode = useUIStore((s) => s.setMode);
  const operatorDismissed = useUIStore((s) => s.operatorModePromptDismissed);
  const auditorDismissed = useUIStore((s) => s.auditorModePromptDismissed);
  const analystDismissed = useUIStore((s) => s.analystModePromptDismissed);
  const pendingAuditCount = useUIStore((s) => s.pendingAuditCount);

  // 同时只可能弹一个 dialog（切到 operator / auditor / analyst 之一）
  const [pendingAdvanced, setPendingAdvanced] = useState<null | 'operator' | 'auditor' | 'analyst'>(
    null,
  );

  /**
   * 切换逻辑：
   *   - 切到 'full' → 直接切（无打扰）
   *   - 切到 advanced 模式且没勾过对应"不再提示" → 弹对应确认框
   *   - 切到 advanced 模式且已勾过 → 直接切
   */
  const handleTabClick = (target: WorkMode): void => {
    if (target === mode) return;
    if (target === 'full') {
      setMode(target);
      return;
    }
    if (target === 'operator' && !operatorDismissed) {
      setPendingAdvanced('operator');
      return;
    }
    if (target === 'auditor' && !auditorDismissed) {
      setPendingAdvanced('auditor');
      return;
    }
    if (target === 'analyst' && !analystDismissed) {
      setPendingAdvanced('analyst');
      return;
    }
    setMode(target);
  };

  const handleConfirm = (dontShowAgain: boolean): void => {
    if (!pendingAdvanced) return;
    if (dontShowAgain) {
      const st = useUIStore.getState();
      if (pendingAdvanced === 'operator') st.setOperatorPromptDismissed(true);
      else if (pendingAdvanced === 'auditor') st.setAuditorPromptDismissed(true);
      else st.setAnalystPromptDismissed(true);
    }
    setMode(pendingAdvanced);
    setPendingAdvanced(null);
  };

  const handleCancel = (): void => setPendingAdvanced(null);

  const dialogContent: AdvancedModeDialogContent | null =
    pendingAdvanced === 'operator'
      ? OPERATOR_MODE_CONTENT
      : pendingAdvanced === 'auditor'
        ? AUDITOR_MODE_CONTENT
        : pendingAdvanced === 'analyst'
          ? DATA_MODE_CONTENT
          : null;

  const tabList = (
    <div
      role="tablist"
      aria-label="工作模式"
      className={large ? 'flex items-center rounded-md p-1' : 'flex h-full items-stretch'}
      style={
        large
          ? {
              backgroundColor: '#ffffff',
              border: '1px solid #d4d4d4',
              boxShadow: 'inset 0 1px 0 rgba(0,0,0,0.3)',
            }
          : { borderLeft: '1px solid #d0d0d0' }
      }
    >
      {TABS.map((t) => {
        const active = mode === t.id;
        return (
          <button
            key={t.id}
            role="tab"
            aria-selected={active}
            title={t.hint}
            type="button"
            onClick={() => handleTabClick(t.id)}
            className={
              large
                ? 'rounded px-4 py-1.5 text-ui font-semibold transition-all duration-150'
                : 'relative h-full px-3 text-ui font-semibold transition-colors duration-150'
            }
            style={
              large
                ? {
                    color: active ? '#ffffff' : '#6e6e6e',
                    backgroundColor: active
                      ? t.id === 'auditor'
                        ? '#b25c1a' // 审核专家用暖橙色（区别于开发/运营的蓝）
                        : t.id === 'analyst'
                          ? '#059669' // 数据专家用青绿色
                          : '#007acc'
                      : 'transparent',
                    boxShadow: active ? '0 1px 3px rgba(0,0,0,0.4)' : 'none',
                  }
                : {
                    color: active ? '#1f1f1f' : '#6e6e6e',
                    backgroundColor: active ? '#ffffff' : 'transparent',
                  }
            }
            onMouseEnter={(e) => {
              if (!active) e.currentTarget.style.color = '#1f1f1f';
            }}
            onMouseLeave={(e) => {
              if (!active) e.currentTarget.style.color = '#6e6e6e';
            }}
          >
            {t.label}
            {/* 审核专家 tab 的待审批 Badge（Phase 5 联动） */}
            {t.id === 'auditor' && pendingAuditCount > 0 && (
              <span
                className="ml-1.5 inline-flex items-center rounded-full px-1.5 text-2xs font-bold"
                style={{
                  backgroundColor: '#cd3131',
                  color: '#ffffff',
                  fontSize: 10,
                  minWidth: 18,
                  height: 16,
                  lineHeight: '16px',
                }}
                title={`${pendingAuditCount} 条待审批`}
              >
                {pendingAuditCount > 99 ? '99+' : pendingAuditCount}
              </span>
            )}
            {!large && (
              <span
                aria-hidden
                className="absolute bottom-0 left-0 right-0 transition-transform duration-150 ease-out"
                style={{
                  height: 2,
                  backgroundColor: accentOf(t.id),
                  transform: active ? 'scaleX(1)' : 'scaleX(0)',
                  transformOrigin: 'center',
                }}
              />
            )}
          </button>
        );
      })}
    </div>
  );

  return (
    <>
      {tabList}
      {dialogContent && (
        <AdvancedModeConfirmDialog
          open
          content={dialogContent}
          onConfirm={handleConfirm}
          onCancel={handleCancel}
        />
      )}
    </>
  );
}
