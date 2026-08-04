/**
 * SkillEditorModal —— Phase 2D Skill 编辑器。
 * 800×600 Modal，Monaco YAML + Form 双 Tab（仿 Phase 2G FeatureEditorModal）。
 *
 * Hook 顺序（CRITICAL fix，仿 BUGFIX #15）：
 *   - 所有 hook 无条件在 early-return 之前
 *   - useMemo(yamlText) 在 if 之前
 *   - useEffect deps 含 editorOpen
 */
import { useEffect, useMemo, useState } from 'react';
import Editor from '@monaco-editor/react';
import { useSkillsStore } from '@/store/skillsStore';
import type { Skill } from '@/types/skill';

type TabId = 'form' | 'yaml';

// 简版 YAML serialize（V0 不用 js-yaml）
function skillToYaml(s: Skill): string {
  const lines: string[] = [];
  lines.push(`schema_version: "${s.schema_version}"`);
  lines.push(`id: "${s.id}"`);
  lines.push(`name: "${s.name}"`);
  lines.push(`description: "${s.description}"`);
  lines.push(`version: "${s.version}"`);
  lines.push(`author: "${s.author}"`);
  lines.push(`risk_level: "${s.risk_level}"`);
  lines.push(`enabled: ${s.enabled}`);
  lines.push(`trigger_keywords:`);
  for (const kw of s.trigger_keywords) lines.push(`  - "${kw}"`);
  lines.push(`mcp_servers:`);
  for (const sv of s.mcp_servers) lines.push(`  - "${sv}"`);
  lines.push(`allowed_tools:`);
  for (const t of s.allowed_tools) lines.push(`  - "${t}"`);
  lines.push(`system_prompt: |`);
  for (const line of s.system_prompt.split('\n')) {
    lines.push(`  ${line}`);
  }
  return lines.join('\n');
}

export function SkillEditorModal(): JSX.Element | null {
  // ===== 所有 hook 必须在 early-return 之前 =====
  const editorOpen = useSkillsStore((s) => s.editorOpen);
  const closeEditor = useSkillsStore((s) => s.closeEditor);
  const selectedSkillId = useSkillsStore((s) => s.selectedSkillId);
  const saveSkill = useSkillsStore((s) => s.saveSkill);
  // M2 fix: stable selector 避免每次 render 创建新函数
  const skill = useSkillsStore((s) =>
    selectedSkillId ? s.skills.find((sk) => sk.id === selectedSkillId) ?? null : null
  );

  const [tab, setTab] = useState<TabId>('form');
  const [draft, setDraft] = useState<Skill | null>(null);
  const [dirty, setDirty] = useState(false);

  useEffect(() => {
    if (editorOpen && skill) {
      setDraft({ ...skill });
      setDirty(false);
      setTab('form');
    } else if (!editorOpen) {
      setDraft(null);
      setDirty(false);
    }
  }, [editorOpen, skill?.id]);

  const yamlText = useMemo(
    () => (draft ? skillToYaml(draft) : ''),
    [draft]
  );

  // ===== early-return =====
  if (!editorOpen || !skill || !draft) return null;

  const setField = <K extends keyof Skill>(key: K, value: Skill[K]): void => {
    setDraft({ ...draft, [key]: value });
    setDirty(true);
  };

  const handleSave = (): void => {
    saveSkill(skill.id, draft);
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
        className="flex flex-col overflow-hidden rounded shadow-2xl"
        style={{ width: 800, height: 600, backgroundColor: '#ffffff' }}
        onClick={(e) => e.stopPropagation()}
      >
        <div
          className="flex flex-shrink-0 items-center justify-between border-b px-4 py-2"
          style={{ borderColor: '#d4d4d4', backgroundColor: '#f3f3f3' }}
        >
          <h3 className="text-ui font-semibold" style={{ color: '#1f1f1f' }}>
            ✏️ 编辑 Skill：{skill.name}
          </h3>
          <button
            type="button"
            onClick={closeEditor}
            className="rounded px-2 py-0.5 text-2xs"
            style={{ color: '#616161' }}
          >
            ✕
          </button>
        </div>

        <div
          className="flex flex-shrink-0"
          style={{ borderBottom: '1px solid #d4d4d4', backgroundColor: '#ececec' }}
        >
          {(
            [
              { id: 'form', label: '📝 表单' },
              { id: 'yaml', label: '</> YAML 源' },
            ] as Array<{ id: TabId; label: string }>
          ).map((t) => {
            const active = t.id === tab;
            return (
              <button
                key={t.id}
                type="button"
                onClick={() => setTab(t.id)}
                className="px-4 py-1.5 text-ui transition-colors"
                style={{
                  color: active ? '#0451a5' : '#616161',
                  backgroundColor: active ? '#ffffff' : 'transparent',
                  borderBottom: active ? '2px solid #007acc' : '2px solid transparent',
                  fontWeight: active ? 600 : 400,
                }}
              >
                {t.label}
              </button>
            );
          })}
          {tab === 'yaml' && (
            <span className="ml-2 self-center text-2xs" style={{ color: '#795e26' }}>
              ⚠️ V0 YAML Tab 只读，请切回表单 Tab 编辑
            </span>
          )}
        </div>

        {tab === 'form' ? (
          <div className="flex-1 overflow-auto p-4" style={{ backgroundColor: '#ffffff' }}>
            <h4 className="mb-2 text-2xs font-semibold uppercase tracking-wider" style={{ color: '#0b6bcb' }}>
              基本信息
            </h4>
            <div className="mb-3 space-y-2">
              <div>
                <label className="mb-0.5 block text-2xs" style={{ color: '#616161' }}>ID</label>
                <input
                  value={draft.id}
                  disabled
                  className="w-full rounded border px-2 py-1 text-ui font-mono"
                  style={{ backgroundColor: '#ffffff', borderColor: '#d4d4d4', color: '#616161' }}
                />
              </div>
              <div>
                <label className="mb-0.5 block text-2xs" style={{ color: '#616161' }}>名称</label>
                <input
                  value={draft.name}
                  onChange={(e) => setField('name', e.target.value)}
                  className="w-full rounded border px-2 py-1 text-ui"
                  style={{ backgroundColor: '#f3f3f3', borderColor: '#d4d4d4', color: '#1f1f1f' }}
                />
              </div>
              <div>
                <label className="mb-0.5 block text-2xs" style={{ color: '#616161' }}>描述</label>
                <textarea
                  value={draft.description}
                  onChange={(e) => setField('description', e.target.value)}
                  rows={2}
                  className="w-full rounded border px-2 py-1 text-ui"
                  style={{ backgroundColor: '#f3f3f3', borderColor: '#d4d4d4', color: '#1f1f1f' }}
                />
              </div>
              <div>
                <label className="mb-0.5 block text-2xs" style={{ color: '#616161' }}>风险等级</label>
                <select
                  value={draft.risk_level}
                  onChange={(e) => setField('risk_level', e.target.value as Skill['risk_level'])}
                  className="rounded border px-2 py-1 text-ui"
                  style={{ backgroundColor: '#f3f3f3', borderColor: '#d4d4d4', color: '#1f1f1f' }}
                >
                  <option value="low">🟢 低</option>
                  <option value="medium">🟡 中</option>
                  <option value="high">🔴 高</option>
                </select>
              </div>
            </div>

            <h4 className="mb-2 text-2xs font-semibold uppercase tracking-wider" style={{ color: '#0b6bcb' }}>
              触发关键词
            </h4>
            <div className="mb-3">
              {draft.trigger_keywords.map((kw, i) => (
                <div key={i} className="mb-1 flex items-center gap-1">
                  <input
                    value={kw}
                    onChange={(e) => {
                      const arr = [...draft.trigger_keywords];
                      arr[i] = e.target.value;
                      setField('trigger_keywords', arr);
                    }}
                    className="flex-1 rounded border px-2 py-0.5 text-2xs"
                    style={{ backgroundColor: '#f3f3f3', borderColor: '#d4d4d4', color: '#1f1f1f' }}
                  />
                  <button
                    type="button"
                    onClick={() => setField('trigger_keywords', draft.trigger_keywords.filter((_, idx) => idx !== i))}
                    className="rounded px-1 text-2xs"
                    style={{ color: '#cd3131' }}
                  >
                    ✕
                  </button>
                </div>
              ))}
              <button
                type="button"
                onClick={() => setField('trigger_keywords', [...draft.trigger_keywords, ''])}
                className="rounded px-2 py-0.5 text-2xs"
                style={{ backgroundColor: '#0e639c20', color: '#0451a5', border: '1px dashed #0e639c' }}
              >
                + 增加关键词
              </button>
            </div>

            <h4 className="mb-2 text-2xs font-semibold uppercase tracking-wider" style={{ color: '#0b6bcb' }}>
              System Prompt
            </h4>
            <textarea
              value={draft.system_prompt}
              onChange={(e) => setField('system_prompt', e.target.value)}
              rows={6}
              className="w-full rounded border px-2 py-1 text-2xs"
              style={{ backgroundColor: '#f3f3f3', borderColor: '#d4d4d4', color: '#1f1f1f' }}
            />
          </div>
        ) : (
          <div className="flex-1 overflow-hidden">
            <Editor
              value={yamlText}
              language="yaml"
              theme="vs-light"
              options={{
                readOnly: true,
                minimap: { enabled: false },
                fontSize: 12,
                scrollBeyondLastLine: false,
                wordWrap: 'on',
              }}
            />
          </div>
        )}

        <div
          className="flex flex-shrink-0 items-center justify-end gap-2 border-t px-4 py-2"
          style={{ borderColor: '#d4d4d4', backgroundColor: '#f3f3f3' }}
        >
          {dirty && (
            <span className="mr-auto text-2xs" style={{ color: '#795e26' }}>
              ● 有未保存修改
            </span>
          )}
          <button
            type="button"
            onClick={closeEditor}
            className="rounded px-3 py-1 text-ui"
            style={{ backgroundColor: '#ececec', color: '#1f1f1f' }}
          >
            取消
          </button>
          <button
            type="button"
            onClick={handleSave}
            disabled={!dirty || tab === 'yaml'}
            className="rounded px-3 py-1 text-ui font-semibold"
            style={{
              backgroundColor: !dirty || tab === 'yaml' ? '#ececec' : '#0e639c',
              color: !dirty || tab === 'yaml' ? '#616161' : '#ffffff',
              cursor: !dirty || tab === 'yaml' ? 'not-allowed' : 'pointer',
            }}
          >
            保存
          </button>
        </div>
      </div>
    </div>
  );
}
