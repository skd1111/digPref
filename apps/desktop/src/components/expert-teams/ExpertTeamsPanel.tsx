/**
 * ExpertTeamsPanel —— 设置页「专家团」维护界面。
 *
 * 专家团是系统一等资产（不以 Skill 形式存在）：团 + 成员两级结构化，
 * 后端 %APPDATA%\eaide\expert_teams\*.yaml 为真源（CRUD + 资产包导入导出）。
 * 资产包格式（2026-08-10）：zip = team.yaml（提示词）+ templates/（交付物文档模板）。
 * 布局/交互风格与 SkillsManager 保持一致。
 */
import { useEffect, useMemo, useState } from 'react';
import { useExpertTeamStore } from '@/store/expertTeamStore';
import { ipc } from '@/ipc/invoke';
import type { ExpertMember, ExpertTeam } from '@/types/expertTeam';

function blankMember(): ExpertMember {
  return { name: '', role: '', responsibilities: [], focus_points: [], outputs: [], prompt: '' };
}

function blankTeam(): ExpertTeam {
  return {
    schema_version: '1.0',
    id: `team_${Date.now().toString(36)}`,
    name: '新专家团',
    description: '',
    applicable_scenarios: [],
    trigger_keywords: [],
    enabled: true,
    members: [],
    report_template: '',
  };
}

export function ExpertTeamsPanel(): JSX.Element {
  const teams = useExpertTeamStore((s) => s.teams);
  const loadTeams = useExpertTeamStore((s) => s.loadTeams);
  const deleteTeam = useExpertTeamStore((s) => s.deleteTeam);
  const toggleEnabled = useExpertTeamStore((s) => s.toggleEnabled);
  const openEditor = useExpertTeamStore((s) => s.openEditor);
  const openImportDialog = useExpertTeamStore((s) => s.openImportDialog);
  const [search, setSearch] = useState('');

  useEffect(() => {
    void loadTeams();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const filtered = useMemo(() => {
    const q = search.toLowerCase().trim();
    if (!q) return teams;
    return teams.filter(
      (t) =>
        t.name.toLowerCase().includes(q) ||
        t.id.toLowerCase().includes(q) ||
        t.description.toLowerCase().includes(q) ||
        t.applicable_scenarios.some((s) => s.toLowerCase().includes(q)),
    );
  }, [teams, search]);

  const handleNew = (): void => {
    const team = blankTeam();
    // 先放本地再开编辑器；保存时才落盘后端（saveTeam upsert）
    useExpertTeamStore.setState((s) => ({ teams: [...s.teams, team] }));
    openEditor(team.id);
  };

  const handleExportPackage = async (teamId: string): Promise<void> => {
    try {
      const r = await ipc.expertTeamsExportPackage(teamId);
      const bytes = Uint8Array.from(atob(r.content_base64), (c) => c.charCodeAt(0));
      const blob = new Blob([bytes], { type: 'application/zip' });
      const url = URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = url;
      a.download = r.file_name || `${teamId}.zip`;
      a.click();
      URL.revokeObjectURL(url);
    } catch (e) {
      window.alert(`资产包导出失败：${e instanceof Error ? e.message : String(e)}`);
    }
  };

  return (
    <div className="flex h-full flex-col" style={{ backgroundColor: '#ffffff' }}>
      <div className="flex-shrink-0 border-b px-4 py-3" style={{ borderColor: '#d4d4d4' }}>
        <h2 className="mb-2 text-ui font-semibold" style={{ color: '#1f1f1f' }}>
          👥 专家团（系统重要资产）
        </h2>
        <div className="flex items-center gap-2">
          <input
            type="text"
            placeholder="搜索团名 / 场景 / id…"
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            className="flex-1 rounded border px-2 py-1 text-2xs"
            style={{ backgroundColor: '#f3f3f3', borderColor: '#d4d4d4', color: '#1f1f1f' }}
          />
          <button
            type="button"
            onClick={handleNew}
            className="rounded px-2 py-1 text-2xs"
            style={{ backgroundColor: '#0e639c', color: '#ffffff' }}
          >
            + 新建
          </button>
          <button
            type="button"
            onClick={openImportDialog}
            className="rounded px-2 py-1 text-2xs"
            style={{ backgroundColor: '#ececec', color: '#1f1f1f' }}
          >
            📥 导入资产包
          </button>
        </div>
        <div className="mt-1.5 text-[10px]" style={{ color: '#9ca3af' }}>
          专家团是系统重要资产，以资产包（zip = 提示词 team.yaml + 交付物文档模板 templates/）形式导入/导出；
          运营工作台点击业务时按 Skill 预设 / AI 分析自动选择并注入上下文。
        </div>
      </div>

      <div className="flex-1 overflow-auto p-3">
        {filtered.length === 0 ? (
          <div className="py-10 text-center text-2xs" style={{ color: '#616161' }}>
            暂无专家团。点「+ 新建」创建，或「📥 导入资产包」导入种子包
            （docs/expert-team-seeds/due-diligence-team.zip）。
          </div>
        ) : (
          <div className="space-y-2">
            {filtered.map((t) => (
              <div
                key={t.id}
                className="rounded border p-3"
                style={{ borderColor: '#e0e0e0', backgroundColor: '#ffffff', opacity: t.enabled ? 1 : 0.55 }}
              >
                <div className="flex items-center gap-2">
                  <span className="text-ui font-semibold" style={{ color: '#1f1f1f' }}>
                    {t.name}
                  </span>
                  <span className="font-mono text-[10px]" style={{ color: '#0451a5' }}>
                    {t.id}
                  </span>
                  <span className="rounded px-1.5 text-[10px]" style={{ backgroundColor: '#f3f3f3', color: '#616161' }}>
                    {t.members.length} 名专家
                  </span>
                  <label className="ml-auto flex items-center gap-1 text-2xs" style={{ color: '#616161' }}>
                    <input
                      type="checkbox"
                      checked={t.enabled}
                      onChange={() => toggleEnabled(t.id)}
                    />
                    启用
                  </label>
                  <button
                    type="button"
                    onClick={() => openEditor(t.id)}
                    className="rounded border px-2 py-0.5 text-2xs"
                    style={{ borderColor: '#007acc', color: '#0451a5', backgroundColor: '#ffffff' }}
                  >
                    ✏️ 编辑
                  </button>
                  <button
                    type="button"
                    onClick={() => void handleExportPackage(t.id)}
                    className="rounded border px-2 py-0.5 text-2xs"
                    style={{ borderColor: '#d4d4d4', color: '#616161', backgroundColor: '#ffffff' }}
                    title="导出资产包 zip（提示词 + 交付物模板）"
                  >
                    📦 资产包
                  </button>
                  <button
                    type="button"
                    onClick={() => {
                      if (window.confirm(`确认删除专家团「${t.name}」？`)) deleteTeam(t.id);
                    }}
                    className="rounded border px-2 py-0.5 text-2xs"
                    style={{ borderColor: '#f48771', color: '#cd3131', backgroundColor: '#ffffff' }}
                  >
                    删除
                  </button>
                </div>
                {t.description && (
                  <div className="mt-1 line-clamp-2 text-2xs" style={{ color: '#616161' }}>
                    {t.description}
                  </div>
                )}
                {t.applicable_scenarios.length > 0 && (
                  <div className="mt-1 flex flex-wrap gap-1">
                    {t.applicable_scenarios.map((s) => (
                      <span
                        key={s}
                        className="rounded-full border px-1.5 text-[10px]"
                        style={{ borderColor: '#e0e0e0', color: '#616161' }}
                      >
                        {s}
                      </span>
                    ))}
                  </div>
                )}
              </div>
            ))}
          </div>
        )}
      </div>

      <ExpertTeamEditorModal />
      <ExpertTeamImportDialog />
    </div>
  );
}

// ---------------------------------------------------------------------------
// 编辑器弹窗（团信息 + 成员增删改）
// ---------------------------------------------------------------------------

function ListInput({
  label,
  values,
  onChange,
}: {
  label: string;
  values: string[];
  onChange: (v: string[]) => void;
}): JSX.Element {
  return (
    <div>
      <label className="mb-0.5 block text-2xs" style={{ color: '#616161' }}>
        {label}（逗号分隔）
      </label>
      <input
        value={values.join('，')}
        onChange={(e) =>
          onChange(
            e.target.value
              .split(/[,，;；]/)
              .map((x) => x.trim())
              .filter(Boolean),
          )
        }
        className="w-full rounded border px-2 py-1 text-2xs"
        style={{ backgroundColor: '#f3f3f3', borderColor: '#d4d4d4', color: '#1f1f1f' }}
      />
    </div>
  );
}

function ExpertTeamEditorModal(): JSX.Element | null {
  const editorOpen = useExpertTeamStore((s) => s.editorOpen);
  const editingTeamId = useExpertTeamStore((s) => s.editingTeamId);
  const teams = useExpertTeamStore((s) => s.teams);
  const closeEditor = useExpertTeamStore((s) => s.closeEditor);
  const saveTeam = useExpertTeamStore((s) => s.saveTeam);

  const team = teams.find((t) => t.id === editingTeamId) ?? null;
  const [draft, setDraft] = useState<ExpertTeam | null>(null);
  const [dirty, setDirty] = useState(false);
  const [isNew, setIsNew] = useState(false);

  useEffect(() => {
    if (editorOpen && team) {
      setDraft(JSON.parse(JSON.stringify(team)) as ExpertTeam);
      // source_path 为空且后端无此文件 → 视为新建（id 可编辑）
      setIsNew(!team.source_path);
      setDirty(false);
    } else if (!editorOpen) {
      setDraft(null);
      setDirty(false);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [editorOpen, editingTeamId]);

  if (!editorOpen || !draft) return null;

  const setField = <K extends keyof ExpertTeam>(key: K, value: ExpertTeam[K]): void => {
    setDraft({ ...draft, [key]: value });
    setDirty(true);
  };

  const setMember = (i: number, patch: Partial<ExpertMember>): void => {
    const members = draft.members.map((m, idx) => (idx === i ? { ...m, ...patch } : m));
    setField('members', members);
  };

  const handleSave = (): void => {
    if (!draft.name.trim() || !draft.id.trim()) {
      window.alert('id 和名称必填');
      return;
    }
    if (!/^[a-z][a-z0-9_]{2,63}$/.test(draft.id)) {
      window.alert('id 需为小写字母开头的 snake_case（3-64 位）');
      return;
    }
    if (draft.members.some((m) => !m.name.trim() || !m.role.trim())) {
      window.alert('每个专家的名称与角色定位必填');
      return;
    }
    saveTeam(draft.id, draft);
  };

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center"
      style={{ backgroundColor: 'rgba(0,0,0,0.5)' }}
      onClick={(e) => {
        if (e.target === e.currentTarget) closeEditor();
      }}
    >
      <div
        className="flex flex-col rounded shadow-2xl"
        style={{ backgroundColor: '#ffffff', border: '1px solid #d4d4d4', width: 820, maxHeight: '86vh' }}
        onClick={(e) => e.stopPropagation()}
      >
        <div
          className="flex items-center justify-between border-b px-4 py-2"
          style={{ borderColor: '#d4d4d4', backgroundColor: '#f3f3f3' }}
        >
          <span className="text-ui font-semibold" style={{ color: '#1f1f1f' }}>
            👥 编辑专家团{dirty ? ' · 有未保存修改' : ''}
          </span>
          <button
            type="button"
            onClick={closeEditor}
            className="rounded px-2 py-0.5 text-2xs hover:bg-vscode-border"
            style={{ color: '#616161' }}
          >
            ✕
          </button>
        </div>

        <div className="flex-1 overflow-auto p-4">
          {/* 团基本信息 */}
          <h4 className="mb-2 text-2xs font-semibold uppercase tracking-wider" style={{ color: '#0b6bcb' }}>
            团信息
          </h4>
          <div className="mb-3 grid grid-cols-2 gap-2">
            <div>
              <label className="mb-0.5 block text-2xs" style={{ color: '#616161' }}>
                ID（snake_case，保存后不可改）
              </label>
              <input
                value={draft.id}
                disabled={!isNew}
                onChange={(e) => setField('id', e.target.value)}
                className="w-full rounded border px-2 py-1 text-ui font-mono"
                style={{ backgroundColor: isNew ? '#f3f3f3' : '#ffffff', borderColor: '#d4d4d4', color: isNew ? '#1f1f1f' : '#616161' }}
              />
            </div>
            <div>
              <label className="mb-0.5 block text-2xs" style={{ color: '#616161' }}>
                名称
              </label>
              <input
                value={draft.name}
                onChange={(e) => setField('name', e.target.value)}
                className="w-full rounded border px-2 py-1 text-ui"
                style={{ backgroundColor: '#f3f3f3', borderColor: '#d4d4d4', color: '#1f1f1f' }}
              />
            </div>
          </div>
          <div className="mb-2">
            <label className="mb-0.5 block text-2xs" style={{ color: '#616161' }}>
              描述
            </label>
            <textarea
              value={draft.description}
              onChange={(e) => setField('description', e.target.value)}
              rows={2}
              className="w-full rounded border px-2 py-1 text-2xs"
              style={{ backgroundColor: '#f3f3f3', borderColor: '#d4d4d4', color: '#1f1f1f' }}
            />
          </div>
          <div className="mb-3 grid grid-cols-2 gap-2">
            <ListInput
              label="适用场景"
              values={draft.applicable_scenarios}
              onChange={(v) => setField('applicable_scenarios', v)}
            />
            <ListInput
              label="触发关键词（AI 推荐兑底匹配用）"
              values={draft.trigger_keywords}
              onChange={(v) => setField('trigger_keywords', v)}
            />
          </div>

          {/* 成员列表 */}
          <div className="mb-2 flex items-center">
            <h4 className="text-2xs font-semibold uppercase tracking-wider" style={{ color: '#0b6bcb' }}>
              专家成员（{draft.members.length}）
            </h4>
            <button
              type="button"
              onClick={() => setField('members', [...draft.members, blankMember()])}
              className="ml-auto rounded px-2 py-0.5 text-2xs"
              style={{ backgroundColor: '#0e639c20', color: '#0451a5', border: '1px dashed #0e639c' }}
            >
              + 增加专家
            </button>
          </div>
          <div className="space-y-2">
            {draft.members.map((m, i) => (
              <div key={i} className="rounded border p-2" style={{ borderColor: '#e0e0e0', backgroundColor: '#fafafa' }}>
                <div className="mb-1.5 flex items-center gap-1.5">
                  <input
                    value={m.name}
                    onChange={(e) => setMember(i, { name: e.target.value })}
                    placeholder="专家名称（如：财务分析专家）"
                    className="w-48 rounded border px-2 py-0.5 text-2xs font-semibold"
                    style={{ backgroundColor: '#ffffff', borderColor: '#d4d4d4', color: '#1f1f1f' }}
                  />
                  <input
                    value={m.role}
                    onChange={(e) => setMember(i, { role: e.target.value })}
                    placeholder="角色定位（一句话）"
                    className="flex-1 rounded border px-2 py-0.5 text-2xs"
                    style={{ backgroundColor: '#ffffff', borderColor: '#d4d4d4', color: '#1f1f1f' }}
                  />
                  <button
                    type="button"
                    onClick={() => setField('members', draft.members.filter((_, idx) => idx !== i))}
                    className="rounded px-1.5 text-2xs"
                    style={{ color: '#cd3131' }}
                    title="移除该专家"
                  >
                    ✕
                  </button>
                </div>
                <div className="grid grid-cols-3 gap-1.5">
                  <ListInput label="主要职责" values={m.responsibilities} onChange={(v) => setMember(i, { responsibilities: v })} />
                  <ListInput label="关注点" values={m.focus_points} onChange={(v) => setMember(i, { focus_points: v })} />
                  <ListInput label="典型输出" values={m.outputs} onChange={(v) => setMember(i, { outputs: v })} />
                </div>
                <div className="mt-1.5">
                  <label className="mb-0.5 block text-2xs" style={{ color: '#616161' }}>
                    独立 Prompt（注入会话时随成员一起提供）
                  </label>
                  <textarea
                    value={m.prompt}
                    onChange={(e) => setMember(i, { prompt: e.target.value })}
                    rows={3}
                    className="w-full rounded border px-2 py-1 text-2xs"
                    style={{ backgroundColor: '#ffffff', borderColor: '#d4d4d4', color: '#1f1f1f' }}
                  />
                </div>
              </div>
            ))}
            {draft.members.length === 0 && (
              <div className="rounded border border-dashed p-3 text-center text-2xs" style={{ borderColor: '#d4d4d4', color: '#9ca3af' }}>
                还没有专家成员，点「+ 增加专家」添加
              </div>
            )}
          </div>
        </div>

        <div
          className="flex flex-shrink-0 items-center justify-end gap-2 border-t px-4 py-2"
          style={{ borderColor: '#d4d4d4', backgroundColor: '#f3f3f3' }}
        >
          <button
            type="button"
            onClick={closeEditor}
            className="rounded px-3 py-1 text-2xs"
            style={{ backgroundColor: '#e0e0e0', color: '#1f1f1f' }}
          >
            取消
          </button>
          <button
            type="button"
            onClick={handleSave}
            disabled={!dirty && !isNew}
            className="rounded px-3 py-1 text-2xs font-semibold"
            style={{ backgroundColor: '#0e639c', color: '#ffffff', opacity: dirty || isNew ? 1 : 0.5 }}
          >
            保存
          </button>
        </div>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// 导入对话框（资产包 zip 优先；兼容旧 YAML 文本，2026-08-10）
// ---------------------------------------------------------------------------

function ExpertTeamImportDialog(): JSX.Element | null {
  const open = useExpertTeamStore((s) => s.importDialogOpen);
  const close = useExpertTeamStore((s) => s.closeImportDialog);
  const importTeamYamlText = useExpertTeamStore((s) => s.importTeamYamlText);
  const importTeamPackage = useExpertTeamStore((s) => s.importTeamPackage);
  const [content, setContent] = useState('');
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    if (open) {
      setContent('');
      setError(null);
      setBusy(false);
    }
  }, [open]);

  if (!open) return null;

  const handleFile = async (e: React.ChangeEvent<HTMLInputElement>): Promise<void> => {
    const file = e.target.files?.[0];
    e.target.value = '';
    if (!file) return;
    setError(null);
    if (/\.zip$/i.test(file.name)) {
      // 资产包：直接交后端解包（提示词 + 模板一次到位）
      setBusy(true);
      const res = await importTeamPackage(file);
      setBusy(false);
      if (!res.ok) setError(res.error ?? '导入失败');
      return;
    }
    setContent(await file.text());
  };

  const handleConfirm = async (): Promise<void> => {
    if (!content.trim()) return;
    setBusy(true);
    const res = await importTeamYamlText(content);
    setBusy(false);
    if (!res.ok) {
      setError(res.error ?? '导入失败');
    }
  };

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center"
      style={{ backgroundColor: 'rgba(0,0,0,0.5)' }}
      onClick={(e) => {
        if (e.target === e.currentTarget) close();
      }}
    >
      <div
        className="rounded p-4 shadow-2xl"
        style={{ backgroundColor: '#ffffff', minWidth: 520, maxWidth: 760 }}
        onClick={(e) => e.stopPropagation()}
      >
        <h3 className="mb-2 text-ui font-semibold" style={{ color: '#1f1f1f' }}>
          📥 导入专家团资产包
        </h3>
        <p className="mb-2 text-2xs" style={{ color: '#616161' }}>
          资产包为 zip：根目录 team.yaml（提示词）+ templates/（交付物文档模板）。
          选择 .zip 直接导入；同名专家团已存在时返回 409。
        </p>
        <input
          type="file"
          accept=".zip,.yaml,.yml"
          onChange={(e) => void handleFile(e)}
          className="mb-2 w-full text-ui"
          style={{ color: '#1f1f1f' }}
        />
        <textarea
          value={content}
          onChange={(e) => setContent(e.target.value)}
          rows={8}
          placeholder="兼容旧格式：也可以直接粘贴单个 YAML 定义（无模板）…"
          className="mb-2 w-full rounded border px-2 py-1 font-mono text-2xs"
          style={{ backgroundColor: '#f3f3f3', borderColor: '#d4d4d4', color: '#1f1f1f' }}
        />
        {error && (
          <div
            className="mb-2 rounded p-2 text-2xs"
            style={{ backgroundColor: '#f4877120', border: '1px solid #f48771', color: '#cd3131' }}
          >
            {error}
          </div>
        )}
        <div className="flex justify-end gap-2">
          <button
            type="button"
            onClick={close}
            className="rounded px-3 py-1 text-2xs"
            style={{ backgroundColor: '#e0e0e0', color: '#1f1f1f' }}
          >
            取消
          </button>
          <button
            type="button"
            onClick={() => void handleConfirm()}
            disabled={busy || !content.trim()}
            className="rounded px-3 py-1 text-2xs font-semibold"
            style={{ backgroundColor: '#0e639c', color: '#ffffff', opacity: busy || !content.trim() ? 0.5 : 1 }}
          >
            {busy ? '导入中…' : '导入 YAML 文本'}
          </button>
        </div>
      </div>
    </div>
  );
}
