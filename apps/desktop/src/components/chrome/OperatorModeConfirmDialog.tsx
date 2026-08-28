/**
 * AdvancedModeConfirmDialog —— 切换到"非默认"专家模式时的通用确认弹窗。
 *
 * 用 OperatorModeConfirmDialog 文件名保留向后兼容；内部抽出内容 props 支持
 *   运营专家模式（业务视角）+ 审核专家模式（金融级审批）两种 content。
 *
 * 触发条件：仅在从 full → advanced 且对应 promptDismissed=false 时弹出。
 *           切回 full 不弹（无打扰）。
 */
import { useEffect, useRef, useState } from 'react';

export interface AdvancedModeDialogContent {
  title: string;
  /** 红色（🔴）/黄色（🟡）小标记会被保留原样显示 */
  bullets: string[];
  footerHint?: string;
  /** 弹窗主题色（默认 #007acc 蓝） */
  accentColor?: string;
}

interface AdvancedModeConfirmDialogProps {
  open: boolean;
  content: AdvancedModeDialogContent;
  onConfirm: (dontShowAgain: boolean) => void;
  onCancel: () => void;
}

export function AdvancedModeConfirmDialog({
  open,
  content,
  onConfirm,
  onCancel,
}: AdvancedModeConfirmDialogProps): JSX.Element | null {
  const [dontShowAgain, setDontShowAgain] = useState(false);
  const confirmRef = useRef<HTMLButtonElement>(null);

  // 打开时自动聚焦"确认"按钮 + Esc 取消
  useEffect(() => {
    if (!open) return;
    confirmRef.current?.focus();
    const onKey = (e: KeyboardEvent): void => {
      if (e.key === 'Escape') onCancel();
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [open, onCancel]);

  if (!open) return null;

  const accent = content.accentColor ?? '#007acc';

  return (
    <div
      className="fixed inset-0 z-[200] flex items-center justify-center"
      style={{ backgroundColor: 'rgba(0,0,0,0.55)' }}
      onClick={onCancel}
    >
      <div
        className="w-[480px] rounded shadow-2xl"
        style={{
          backgroundColor: '#f3f3f3',
          border: `1px solid ${accent}`,
          animation: 'opConfirmIn 160ms ease-out',
        }}
        onClick={(e) => e.stopPropagation()}
      >
        {/* 标题栏 */}
        <div
          className="flex items-center gap-2 border-b px-4 py-2"
          style={{ borderColor: '#d4d4d4' }}
        >
          <span style={{ color: '#059669', fontSize: 16 }}>ⓘ</span>
          <h2 className="text-ui-lg font-semibold" style={{ color: '#ffffff' }}>
            {content.title}
          </h2>
        </div>

        {/* 主体 */}
        <div className="px-4 py-3 text-ui" style={{ color: '#1f1f1f', lineHeight: 1.6 }}>
          <ul className="mb-3 ml-5 list-disc space-y-1" style={{ color: '#333333' }}>
            {content.bullets.map((b, i) => (
              <li key={i}>{b}</li>
            ))}
          </ul>
          {content.footerHint && (
            <p className="text-2xs" style={{ color: '#616161' }}>
              {content.footerHint}
            </p>
          )}
        </div>

        {/* "下次不再提示" 复选框 */}
        <label
          className="flex cursor-pointer items-center gap-2 border-t px-4 py-2 text-ui"
          style={{ borderColor: '#d4d4d4', color: '#333333' }}
        >
          <input
            type="checkbox"
            checked={dontShowAgain}
            onChange={(e) => setDontShowAgain(e.target.checked)}
          />
          <span>下次不再提示</span>
        </label>

        {/* 操作按钮 */}
        <div
          className="flex justify-end gap-2 border-t px-4 py-2"
          style={{ borderColor: '#d4d4d4' }}
        >
          <button
            type="button"
            onClick={onCancel}
            className="rounded px-3 py-1.5 text-ui transition-colors hover:brightness-110"
            style={{ backgroundColor: '#ececec', color: '#333333' }}
          >
            取消
          </button>
          <button
            ref={confirmRef}
            type="button"
            onClick={() => onConfirm(dontShowAgain)}
            className="rounded px-3 py-1.5 text-ui font-semibold transition-all hover:brightness-110"
            style={{
              backgroundColor: accent,
              color: '#ffffff',
              boxShadow: '0 1px 2px rgba(0,0,0,0.4)',
            }}
          >
            确认切换
          </button>
        </div>
      </div>

      <style>{`
        @keyframes opConfirmIn {
          from { opacity: 0; transform: scale(0.96) translateY(-6px); }
          to   { opacity: 1; transform: scale(1) translateY(0); }
        }
      `}</style>
    </div>
  );
}

// ---- 兼容性默认导出：保留 OperatorModeConfirmDialog 名字（向后兼容旧 import） ----

interface OperatorModeConfirmDialogProps {
  open: boolean;
  onConfirm: (dontShowAgain: boolean) => void;
  onCancel: () => void;
}

export const OPERATOR_MODE_CONTENT: AdvancedModeDialogContent = {
  title: '切换到运营模式',
  bullets: [
    '运营模式是独立页签（与开发模式并列），全屏渲染运营工作台',
    '三栏布局：左侧业务列表（16 模块导航 + 功能点）+ 中间 Chat + 右侧工作台',
    '功能点以 Skill 承载：选中业务 → 自动注入绑定 Skill 与专家团到会话',
    '隐藏开发模式的系统资产栏 / 执行过程 / 终端，UI 更聚焦业务办理',
  ],
  footerHint: '模式选择会持久化到本地，下次启动自动恢复。',
};

export const AUDITOR_MODE_CONTENT: AdvancedModeDialogContent = {
  title: '切换到审核专家模式',
  bullets: [
    '面向金融合规审计 / 风控 / 审批负责人，UI 采用三栏布局（审批工作台 + Diff 详情 + 审计与合规）',
    '核心：Monaco Diff 审核 + 风险可视化（🔴高 / 🟡中 / 🟢低）+ Evidence Chain 证据链',
    '金融级安全：所有批准必须 MFA 二次验证（TOTP / 审批密码 / Windows Hello）',
    '审计日志采用 哈希签名链 防篡改，研发操作 → 运营执行 → 审核把关 全链路闭环',
    'Phase 3C Arthas 热更、Phase 2G 业务规则变更、Phase 4 知识库合规检查 都会推送到此工作台',
  ],
  footerHint: '审核专家模式独立三栏 UI，不复用运营专家两栏布局。模式选择会持久化到本地。',
  accentColor: '#b25c1a', // 暖橙色 — 区别于运营专家的蓝色
};

export const DATA_MODE_CONTENT: AdvancedModeDialogContent = {
  title: '切换到数据专家模式',
  bullets: [
    '面向数据分析师 / BI 工程师 / 财务运营，UI 采用四象限布局（数据源 + SQL/Python/对话编辑器 + 数据网格 + 图表）',
    '自然语言驱动：输入"对比上月各分行坏账率"，AI 自动生成 SQL（NL2SQL），一键出图表',
    '🔒 只读铁律：数据专家模式禁 UPDATE/DELETE/DROP，仅允许 SELECT 查询',
    '导出 Excel/PDF/CSV 会经过 PII 脱敏 + 数字水印 + 导出审计（合规可溯源）',
    '复杂清洗走受限 Python 沙箱（白名单 pandas/numpy，禁系统/网络调用）',
  ],
  footerHint: '数据专家模式独立四象限 UI。模式选择会持久化到本地，下次启动自动恢复。',
  accentColor: '#059669', // 青绿色 — 数据/分析主题，区别于蓝(开发)与橙(审核)
};

/**
 * 向后兼容的简单包装：旧代码 `import { OperatorModeConfirmDialog }` 仍可用。
 * 新代码建议直接用 `AdvancedModeConfirmDialog + OPERATOR_MODE_CONTENT`。
 */
export function OperatorModeConfirmDialog({
  open,
  onConfirm,
  onCancel,
}: OperatorModeConfirmDialogProps): JSX.Element {
  return (
    <AdvancedModeConfirmDialog
      open={open}
      content={OPERATOR_MODE_CONTENT}
      onConfirm={onConfirm}
      onCancel={onCancel}
    />
  );
}
