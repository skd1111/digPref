/**
 * ReqCardDetailEditor —— reqflow V1 需求卡片详情编辑（工作台右栏 320px）。
 *
 * - 全字段编辑 + 保存（后端自动记版本）
 * - 状态切换：选项按 STATUS_TRANSITIONS 过滤（审批预留口在后端）
 * - 版本区：标题旁 v{n}，可切换历史版本只读查看，默认最新版
 * - 仅 draft 可删除
 */
import { useEffect, useState } from 'react';
import {
  FEASIBILITY_META,
  STATUS_META,
  STATUS_TRANSITIONS,
  type CardStatus,
  type ReqCard,
} from '@/types/reqcard';
import { useReqcardStore } from '@/store/reqcardStore';

const INPUT_STYLE: React.CSSProperties = {
  backgroundColor: '#ffffff',
  borderColor: '#d4d4d4',
  color: '#1f1f1f',
};

function Field({ label, children }: { label: string; children: React.ReactNode }): JSX.Element {
  return (
    <div className="mb-2">
      <div className="mb-0.5 text-2xs font-semibold" style={{ color: '#616161' }}>
        {label}
      </div>
      {children}
    </div>
  );
}

export function ReqCardDetailEditor({ card }: { card: ReqCard }): JSX.Element {
  const updateCard = useReqcardStore((s) => s.updateCard);
  const deleteCard = useReqcardStore((s) => s.deleteCard);
  const versions = useReqcardStore((s) => s.versions);
  const loadVersions = useReqcardStore((s) => s.loadVersions);
  const viewingVersion = useReqcardStore((s) => s.viewingVersion);
  const versionSnapshot = useReqcardStore((s) => s.versionSnapshot);
  const viewVersion = useReqcardStore((s) => s.viewVersion);
  const backToLatest = useReqcardStore((s) => s.backToLatest);

  // 编辑中的字段（保存才提交）
  const [draft, setDraft] = useState({
    title: card.title,
    system_name: card.system_name,
    priority: card.priority,
    feasibility: card.feasibility,
    feasibility_notes: card.feasibility_notes,
    business_value: card.business_value,
    change_points: card.change_points,
    impact: card.impact,
    external_systems: card.external_systems.join('、'),
  });

  // 卡片变化（切换选中 / 保存成功版本+1）→ 重置草稿
  useEffect(() => {
    setDraft({
      title: card.title,
      system_name: card.system_name,
      priority: card.priority,
      feasibility: card.feasibility,
      feasibility_notes: card.feasibility_notes,
      business_value: card.business_value,
      change_points: card.change_points,
      impact: card.impact,
      external_systems: card.external_systems.join('、'),
    });
    void loadVersions(card.id);
    backToLatest();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [card.id, card.version]);

  // 历史版本只读展示
  const readOnly = viewingVersion !== null;
  const shown: ReqCard = readOnly && versionSnapshot ? versionSnapshot : card;

  const set = (k: keyof typeof draft, v: string): void =>
    setDraft((d) => ({ ...d, [k]: v }));

  const handleSave = async (): Promise<void> => {
    await updateCard(card.id, {
      title: draft.title,
      system_name: draft.system_name,
      priority: draft.priority,
      feasibility: draft.feasibility,
      feasibility_notes: draft.feasibility_notes,
      business_value: draft.business_value,
      change_points: draft.change_points,
      impact: draft.impact,
      external_systems: draft.external_systems
        .split(/[、,，]/)
        .map((s) => s.trim())
        .filter(Boolean),
    });
  };

  const handleStatus = async (next: CardStatus): Promise<void> => {
    await updateCard(card.id, { status: next });
  };

  const handleDelete = async (): Promise<void> => {
    if (window.confirm(`确认删除需求卡片 ${card.id}？`)) {
      await deleteCard(card.id);
    }
  };

  const nextStatuses = STATUS_TRANSITIONS[card.status] ?? [];

  return (
    <div className="flex h-full flex-col overflow-auto p-3 text-2xs">
      {/* 编号 + 版本切换 */}
      <div className="mb-2 flex items-center gap-2">
        <span className="font-mono font-semibold" style={{ color: '#0451a5' }}>
          {shown.id}
        </span>
        <span style={{ color: STATUS_META[shown.status].color }}>
          {STATUS_META[shown.status].icon} {STATUS_META[shown.status].label}
        </span>
        <select
          value={readOnly ? String(viewingVersion) : 'latest'}
          onChange={(e) => {
            const v = e.target.value;
            if (v === 'latest') backToLatest();
            else void viewVersion(card.id, Number(v));
          }}
          className="ml-auto rounded border px-1 py-0.5 text-2xs outline-none"
          style={INPUT_STYLE}
          title="切换历史版本查看（只读），默认最新版"
        >
          <option value="latest">v{card.version}（最新）</option>
          {versions.map((v) => (
            <option key={v.version} value={String(v.version)}>
              v{v.version}
            </option>
          ))}
        </select>
      </div>

      {readOnly && (
        <div
          className="mb-2 rounded px-2 py-1"
          style={{ backgroundColor: '#fff3cd', color: '#795e26' }}
        >
          正在查看历史版本 v{viewingVersion}（只读）
          <button
            type="button"
            onClick={backToLatest}
            className="ml-2 underline"
            style={{ color: '#0451a5' }}
          >
            回到最新版
          </button>
        </div>
      )}

      <Field label="需求标题">
        <input
          value={readOnly ? shown.title : draft.title}
          disabled={readOnly}
          onChange={(e) => set('title', e.target.value)}
          className="w-full rounded border px-2 py-1 outline-none"
          style={INPUT_STYLE}
        />
      </Field>

      <div className="mb-2 grid grid-cols-2 gap-2">
        <Field label="系统名称">
          <input
            value={readOnly ? shown.system_name : draft.system_name}
            disabled={readOnly}
            onChange={(e) => set('system_name', e.target.value)}
            className="w-full rounded border px-2 py-1 outline-none"
            style={INPUT_STYLE}
          />
        </Field>
        <Field label="优先级">
          <select
            value={readOnly ? shown.priority : draft.priority}
            disabled={readOnly}
            onChange={(e) => set('priority', e.target.value)}
            className="w-full rounded border px-2 py-1 outline-none"
            style={INPUT_STYLE}
          >
            <option value="P0">P0</option>
            <option value="P1">P1</option>
            <option value="P2">P2</option>
          </select>
        </Field>
      </div>

      <Field label="可行性结论">
        <select
          value={readOnly ? shown.feasibility : draft.feasibility}
          disabled={readOnly}
          onChange={(e) => set('feasibility', e.target.value)}
          className="w-full rounded border px-2 py-1 outline-none"
          style={{
            ...INPUT_STYLE,
            color: FEASIBILITY_META[shown.feasibility]?.color ?? '#1f1f1f',
          }}
        >
          <option value="">未评估</option>
          <option value="feasible">可行</option>
          <option value="risky">有风险</option>
          <option value="infeasible">不可行</option>
        </select>
      </Field>

      <Field label="可行性说明">
        <textarea
          value={readOnly ? shown.feasibility_notes : draft.feasibility_notes}
          disabled={readOnly}
          onChange={(e) => set('feasibility_notes', e.target.value)}
          rows={2}
          className="w-full rounded border px-2 py-1 outline-none"
          style={INPUT_STYLE}
        />
      </Field>

      <Field label="业务价值">
        <textarea
          value={readOnly ? shown.business_value : draft.business_value}
          disabled={readOnly}
          onChange={(e) => set('business_value', e.target.value)}
          rows={2}
          className="w-full rounded border px-2 py-1 outline-none"
          style={INPUT_STYLE}
        />
      </Field>

      <Field label="对现有功能的改造点">
        <textarea
          value={readOnly ? shown.change_points : draft.change_points}
          disabled={readOnly}
          onChange={(e) => set('change_points', e.target.value)}
          rows={3}
          className="w-full rounded border px-2 py-1 outline-none"
          style={INPUT_STYLE}
        />
      </Field>

      <Field label="对其他功能的影响">
        <textarea
          value={readOnly ? shown.impact : draft.impact}
          disabled={readOnly}
          onChange={(e) => set('impact', e.target.value)}
          rows={2}
          className="w-full rounded border px-2 py-1 outline-none"
          style={INPUT_STYLE}
        />
      </Field>

      <Field label="涉及外部系统（顿号/逗号分隔）">
        <input
          value={readOnly ? shown.external_systems.join('、') : draft.external_systems}
          disabled={readOnly}
          onChange={(e) => set('external_systems', e.target.value)}
          className="w-full rounded border px-2 py-1 outline-none"
          style={INPUT_STYLE}
        />
      </Field>

      <Field label="关联功能点">
        <div className="font-mono" style={{ color: '#0b6bcb' }}>
          {shown.feature_ids.length > 0 ? shown.feature_ids.join('、') : '无'}
        </div>
      </Field>

      {/* 状态切换（流转规则过滤；审批预留口在后端） */}
      {!readOnly && nextStatuses.length > 0 && (
        <Field label="状态流转">
          <div className="flex flex-wrap gap-1">
            {nextStatuses.map((s) => (
              <button
                key={s}
                type="button"
                onClick={() => void handleStatus(s)}
                className="rounded border px-2 py-0.5 transition-colors hover:brightness-95"
                style={{
                  borderColor: STATUS_META[s].color,
                  color: STATUS_META[s].color,
                  backgroundColor: '#ffffff',
                }}
              >
                {STATUS_META[s].icon} → {STATUS_META[s].label}
              </button>
            ))}
          </div>
        </Field>
      )}

      {/* 操作区 */}
      {!readOnly && (
        <div className="mt-2 flex gap-2">
          <button
            type="button"
            onClick={() => void handleSave()}
            className="flex-1 rounded px-2 py-1 font-semibold transition-colors hover:brightness-110"
            style={{ backgroundColor: '#0e639c', color: '#ffffff' }}
          >
            💾 保存修改
          </button>
          {card.status === 'draft' && (
            <button
              type="button"
              onClick={() => void handleDelete()}
              className="rounded border px-2 py-1 transition-colors hover:brightness-95"
              style={{ borderColor: '#cd3131', color: '#cd3131' }}
            >
              🗑 删除
            </button>
          )}
        </div>
      )}
    </div>
  );
}
