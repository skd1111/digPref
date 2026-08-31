/**
 * SkillsManager —— Phase 2D Skill 管理主面板。
 * 三 Tab：技能列表 + 待审草稿（Phase 19 V1 自进化蒸馏）+ 导入/导出。
 * 列表卡片网格，含搜索 + 启用/禁用。
 */
import { useMemo, useState } from 'react';
import { useSkillsStore } from '@/store/skillsStore';
import { SkillCard } from './SkillCard';
import { SkillDraftsPanel, usePendingDraftCount } from './SkillDraftsPanel';

export function SkillsManager(): JSX.Element {
  const skills = useSkillsStore((s) => s.skills);
  const selectedSkillId = useSkillsStore((s) => s.selectedSkillId);
  const selectSkill = useSkillsStore((s) => s.selectSkill);
  const openEditor = useSkillsStore((s) => s.openEditor);
  const openImportDialog = useSkillsStore((s) => s.openImportDialog);
  const deleteSkill = useSkillsStore((s) => s.deleteSkill);
  const toggleEnabled = useSkillsStore((s) => s.toggleEnabled);
  const importSkill = useSkillsStore((s) => s.importSkill);

  const [tab, setTab] = useState<'list' | 'drafts' | 'import'>('list');
  const [search, setSearch] = useState('');
  const [showNewDialog, setShowNewDialog] = useState(false);
  const [newJson, setNewJson] = useState('');
  const pendingDrafts = usePendingDraftCount();

  const filtered = useMemo(() => {
    const q = search.toLowerCase().trim();
    if (!q) return skills;
    return skills.filter(
      (s) =>
        s.name.toLowerCase().includes(q) ||
        s.id.toLowerCase().includes(q) ||
        s.description.toLowerCase().includes(q) ||
        s.tags.some((t) => t.toLowerCase().includes(q))
    );
  }, [skills, search]);

  const handleNewConfirm = (): void => {
    try {
      const data = JSON.parse(newJson);
      if (!data.id || !data.name) {
        alert('id 和 name 必填');
        return;
      }
      if (skills.some((s) => s.id === data.id)) {
        alert(`skill ${data.id} 已存在`);
        return;
      }
      importSkill({
        schema_version: '1.0',
        id: data.id,
        name: data.name,
        description: data.description ?? '',
        version: data.version ?? '1.0',
        author: data.author ?? '',
        tags: data.tags ?? [],
        risk_level: data.risk_level ?? 'low',
        enabled: data.enabled ?? true,
        trigger_keywords: data.trigger_keywords ?? [],
        mcp_servers: data.mcp_servers ?? [],
        allowed_tools: data.allowed_tools ?? [],
        role: data.role ?? 'utility',
        system_prompt: data.system_prompt ?? '',
        few_shot_examples: data.few_shot_examples ?? [],
        required_expert_team_ids: data.required_expert_team_ids ?? [],
        materials: data.materials ?? [],
        deliverables: data.deliverables ?? [],
        source_path: '',
        loaded_at: Date.now(),
        validation_errors: [],
      });
      setShowNewDialog(false);
      setNewJson('');
    } catch (e) {
      alert(`解析失败: ${e}`);
    }
  };

  return (
    <div className="flex h-full flex-col" style={{ backgroundColor: '#ffffff' }}>
      <div
        className="flex-shrink-0 border-b px-4 py-3"
        style={{ borderColor: '#d4d4d4' }}
      >
        <h2 className="mb-2 text-ui font-semibold" style={{ color: '#1f1f1f' }}>
          🧠 业务技能
        </h2>
        <div className="flex items-center gap-2">
          <input
            type="text"
            placeholder="搜索技能名 / 标签..."
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            className="flex-1 rounded border px-2 py-1 text-2xs"
            style={{ backgroundColor: '#f3f3f3', borderColor: '#d4d4d4', color: '#1f1f1f' }}
          />
          <button
            type="button"
            onClick={() => setShowNewDialog(true)}
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
            📥 导入
          </button>
        </div>
      </div>

      <div className="flex flex-shrink-0" style={{ borderBottom: '1px solid #d4d4d4' }}>
        {(
          [
            { id: 'list', label: `技能列表 (${filtered.length})` },
            {
              id: 'drafts',
              label: pendingDrafts > 0 ? `待审草稿 (${pendingDrafts})` : '待审草稿',
            },
            { id: 'import', label: '导入 / 导出' },
          ] as Array<{ id: 'list' | 'drafts' | 'import'; label: string }>
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
                borderBottom: active ? '2px solid #007acc' : '2px solid transparent',
              }}
            >
              {t.label}
            </button>
          );
        })}
      </div>

      <div className="flex-1 overflow-auto p-4">
        {tab === 'drafts' ? (
          <SkillDraftsPanel />
        ) : tab === 'list' ? (
          filtered.length === 0 ? (
            <div className="flex h-full items-center justify-center text-2xs" style={{ color: '#616161' }}>
              {search ? '无匹配技能' : '暂无技能，点 [+ 新建] 或 [📥 导入] 开始'}
            </div>
          ) : (
            <div className="grid grid-cols-1 gap-3 lg:grid-cols-2">
              {filtered.map((skill) => (
                <SkillCard
                  key={skill.id}
                  skill={skill}
                  selected={skill.id === selectedSkillId}
                  onSelect={() => selectSkill(skill.id)}
                  onEdit={() => openEditor(skill.id)}
                  onDelete={() => {
                    if (confirm(`确认删除 skill "${skill.name}"?`)) {
                      deleteSkill(skill.id);
                    }
                  }}
                  onToggleEnabled={() => toggleEnabled(skill.id)}
                />
              ))}
            </div>
          )
        ) : (
          <div className="text-ui" style={{ color: '#1f1f1f' }}>
            <h3 className="mb-2 text-2xs font-semibold uppercase tracking-wider" style={{ color: '#0b6bcb' }}>
              批量导入 / 导出
            </h3>
            <p className="mb-3 text-2xs" style={{ color: '#616161' }}>
              V0 支持单文件导入 + 全部 skills 导出。批量 zip 导入留 V1。
            </p>
            <button
              type="button"
              onClick={openImportDialog}
              className="rounded px-3 py-1.5"
              style={{ backgroundColor: '#0e639c', color: '#ffffff' }}
            >
              📥 导入单个 skill
            </button>
          </div>
        )}
      </div>

      {showNewDialog && (
        <div
          className="fixed inset-0 z-50 flex items-center justify-center"
          style={{ backgroundColor: 'rgba(0,0,0,0.5)' }}
          onClick={(e) => {
            if (e.target === e.currentTarget) setShowNewDialog(false);
          }}
        >
          <div
            className="rounded p-4 shadow-2xl"
            style={{ backgroundColor: '#ffffff', minWidth: 480 }}
          >
            <h3 className="mb-3 text-ui font-semibold" style={{ color: '#1f1f1f' }}>
              新建 Skill
            </h3>
            <p className="mb-2 text-2xs" style={{ color: '#616161' }}>
              粘贴 JSON（V0 不引 js-yaml，V1 接 YAML 解析后端）
            </p>
            <textarea
              value={newJson}
              onChange={(e) => setNewJson(e.target.value)}
              rows={10}
              placeholder='{"schema_version": "1.0", "id": "new_skill", "name": "新技能", "trigger_keywords": ["x"]}'
              className="mb-3 w-full rounded border px-2 py-1 font-mono text-2xs"
              style={{ backgroundColor: '#f3f3f3', borderColor: '#d4d4d4', color: '#1f1f1f' }}
            />
            <div className="flex justify-end gap-2">
              <button
                type="button"
                onClick={() => setShowNewDialog(false)}
                className="rounded px-3 py-1 text-ui"
                style={{ backgroundColor: '#ececec', color: '#1f1f1f' }}
              >
                取消
              </button>
              <button
                type="button"
                onClick={handleNewConfirm}
                className="rounded px-3 py-1 text-ui font-semibold"
                style={{ backgroundColor: '#0e639c', color: '#ffffff' }}
              >
                创建
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
