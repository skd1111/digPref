/**
 * FeatureEditorModal —— Phase 2G 业务功能点编辑器。
 *
 * 800×600 居中 Modal（spec §4.3）：
 *   - 顶部 2 Tab: 📝 表单 / </> YAML 源
 *   - 表单 Tab: 6 区 inline edit（基本信息 / 关联文件 / API / 表 / 业务规则 / 元数据）
 *   - YAML Tab: Monaco Editor 只读预览（[保存] disabled，V0 不实现 yamlToFeature）
 *   - 底部 [取消] [保存]（V0 编辑全走表单 Tab，YAML 仅展示）
 *
 * Hook 顺序（CRITICAL fix，仿 BUGFIX #15 教训）：
 *   - 所有 hook 必须无条件在 early-return 之前调用
 *   - useMemo(yamlText) 在 if (!editorOpen || ...) return null 之前
 *   - useEffect deps 含 editorOpen，避免同 feature 重开时残留 dirty
 */
import { useEffect, useMemo, useState } from 'react';
import Editor from '@monaco-editor/react';
import { useBiznavStore, selectEditorFeature } from '@/store/biznavStore';
import type { Feature, RelatedApi, RelatedFile, RelatedTable } from '@/types/biznav';
import { featureToYaml } from '@/lib/yamlExport';

type TabId = 'form' | 'yaml';

export function FeatureEditorModal(): JSX.Element | null {
  // ===== 所有 hook 必须无条件在 early-return 之前（CRITICAL fix，仿 BUGFIX #15）=====
  const editorOpen = useBiznavStore((s) => s.editorOpen);
  const closeEditor = useBiznavStore((s) => s.closeEditor);
  const updateFeature = useBiznavStore((s) => s.updateFeature);
  const feature = useBiznavStore(selectEditorFeature);

  const [tab, setTab] = useState<TabId>('form');
  const [draft, setDraft] = useState<Feature | null>(null);
  const [dirty, setDirty] = useState(false);
  const [showCloseConfirm, setShowCloseConfirm] = useState(false);

  // CRITICAL #2 fix: deps must include editorOpen
  // 关闭 modal 时也要重置 draft，避免同 feature 重开时残留 dirty 状态
  useEffect(() => {
    if (editorOpen && feature) {
      setDraft({ ...feature });
      setDirty(false);
      setTab('form');
    } else if (!editorOpen) {
      setDraft(null);
      setDirty(false);
      setShowCloseConfirm(false);
    }
  }, [editorOpen, feature?.id]);

  // CRITICAL #1 fix: useMemo 必须在 early-return 之前（无条件调用）
  const yamlText = useMemo(
    () => (draft ? featureToYaml(draft) : ''),
    [draft]
  );

  // ===== Hook 完毕，再 early-return =====
  if (!editorOpen || !feature || !draft) return null;

  const setField = <K extends keyof Feature>(key: K, value: Feature[K]): void => {
    setDraft({ ...draft, [key]: value });
    setDirty(true);
  };

  const handleClose = (): void => {
    if (dirty) {
      setShowCloseConfirm(true);
    } else {
      closeEditor();
    }
  };

  const handleSave = (): void => {
    updateFeature(feature.id, draft);
    setDirty(false);
    closeEditor();
  };

  const handleDiscard = (): void => {
    setShowCloseConfirm(false);
    setDirty(false);
    closeEditor();
  };

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center"
      style={{ backgroundColor: 'rgba(0,0,0,0.5)' }}
      onClick={(e) => {
        if (e.target === e.currentTarget) handleClose();
      }}
    >
      <div
        className="flex flex-col overflow-hidden rounded shadow-2xl"
        style={{ width: 800, height: 600, backgroundColor: '#ffffff' }}
        onClick={(e) => e.stopPropagation()}
      >
        {/* Header */}
        <div
          className="flex flex-shrink-0 items-center justify-between border-b px-4 py-2"
          style={{ borderColor: '#d4d4d4', backgroundColor: '#f3f3f3' }}
        >
          <div>
            <h3 className="text-ui font-semibold" style={{ color: '#1f1f1f' }}>
              ✏️ 编辑功能点：{feature.name}
            </h3>
            <div className="text-2xs" style={{ color: '#616161' }}>
              {feature.id} · v{feature.version}
            </div>
          </div>
          <button
            type="button"
            onClick={handleClose}
            className="rounded px-2 py-0.5 text-2xs transition-colors hover:bg-vscode-border"
            style={{ color: '#616161' }}
            title="关闭"
            aria-label="关闭"
          >
            ✕
          </button>
        </div>

        {/* Tabs */}
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

        {/* Body */}
        {tab === 'form' ? (
          <FormTab draft={draft} setField={setField} />
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
                fontFamily: '"Cascadia Code", "JetBrains Mono", Consolas, monospace',
              }}
            />
          </div>
        )}

        {/* Footer */}
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
            onClick={handleClose}
            className="rounded px-3 py-1 text-ui transition-colors"
            style={{ backgroundColor: '#ececec', color: '#1f1f1f' }}
          >
            取消
          </button>
          <button
            type="button"
            onClick={handleSave}
            disabled={!dirty || tab === 'yaml'}
            className="rounded px-3 py-1 text-ui font-semibold transition-colors"
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

      {/* 未保存关闭确认 */}
      {showCloseConfirm && (
        <div
          className="absolute inset-0 flex items-center justify-center"
          style={{ backgroundColor: 'rgba(0,0,0,0.7)' }}
        >
          <div
            className="rounded p-4 shadow-2xl"
            style={{
              backgroundColor: '#f3f3f3',
              border: '1px solid #f48771',
              minWidth: 360,
            }}
          >
            <div className="mb-3 text-ui" style={{ color: '#1f1f1f' }}>
              ⚠️ 当前有未保存的修改
            </div>
            <div className="mb-3 text-2xs" style={{ color: '#a0a0a0' }}>
              关闭后所有修改将丢失。
            </div>
            <div className="flex justify-end gap-2">
              <button
                type="button"
                onClick={() => setShowCloseConfirm(false)}
                className="rounded px-3 py-1 text-ui"
                style={{ backgroundColor: '#ececec', color: '#1f1f1f' }}
              >
                继续编辑
              </button>
              <button
                type="button"
                onClick={handleDiscard}
                className="rounded px-3 py-1 text-ui font-semibold"
                style={{ backgroundColor: '#cd3131', color: '#0e0e0e' }}
              >
                放弃修改
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

// ---------- FormTab 子组件 ----------

interface FormTabProps {
  draft: Feature;
  setField: <K extends keyof Feature>(key: K, value: Feature[K]) => void;
}

function FormTab({ draft, setField }: FormTabProps): JSX.Element {
  return (
    <div className="flex-1 overflow-auto p-4" style={{ backgroundColor: '#ffffff' }}>
      {/* 基本信息 */}
      <Section title="基本信息">
        <Field label="名称（中文）">
          <input
            value={draft.name}
            onChange={(e) => setField('name', e.target.value)}
            className="w-full rounded border px-2 py-1 text-ui"
            style={{ backgroundColor: '#f3f3f3', borderColor: '#d4d4d4', color: '#1f1f1f' }}
          />
        </Field>
        <Field label="描述">
          <textarea
            value={draft.description}
            onChange={(e) => setField('description', e.target.value)}
            rows={2}
            className="w-full rounded border px-2 py-1 text-ui"
            style={{ backgroundColor: '#f3f3f3', borderColor: '#d4d4d4', color: '#1f1f1f' }}
          />
        </Field>
        <Field label="分类">
          <input
            value={draft.category}
            onChange={(e) => setField('category', e.target.value)}
            className="w-full rounded border px-2 py-1 text-ui"
            style={{ backgroundColor: '#f3f3f3', borderColor: '#d4d4d4', color: '#1f1f1f' }}
          />
        </Field>
        <Field label="风险等级">
          <select
            value={draft.risk_level}
            onChange={(e) =>
              setField('risk_level', e.target.value as Feature['risk_level'])
            }
            className="rounded border px-2 py-1 text-ui"
            style={{ backgroundColor: '#f3f3f3', borderColor: '#d4d4d4', color: '#1f1f1f' }}
          >
            <option value="high">🔴 高</option>
            <option value="medium">🟡 中</option>
            <option value="low">🟢 低</option>
          </select>
        </Field>
      </Section>

      {/* 关联文件 */}
      <Section title="关联文件">
        <ArrayEditor<RelatedFile>
          items={draft.related_files}
          setItems={(next) => setField('related_files', next)}
          renderItem={(item, idx, update) => (
            <div key={idx} className="mb-1 flex items-center gap-1">
              <input
                value={item.path}
                onChange={(e) => update({ ...item, path: e.target.value })}
                placeholder="文件路径"
                className="flex-1 rounded border px-2 py-0.5 font-mono text-2xs"
                style={{
                  backgroundColor: '#f3f3f3',
                  borderColor: '#d4d4d4',
                  color: '#0b6bcb',
                }}
              />
              <input
                value={item.role}
                onChange={(e) => update({ ...item, role: e.target.value })}
                placeholder="角色"
                className="w-24 rounded border px-2 py-0.5 text-2xs"
                style={{
                  backgroundColor: '#f3f3f3',
                  borderColor: '#d4d4d4',
                  color: '#1f1f1f',
                }}
              />
              <button
                type="button"
                onClick={() =>
                  setField(
                    'related_files',
                    draft.related_files.filter((_, i) => i !== idx)
                  )
                }
                className="rounded px-1 text-2xs"
                style={{ color: '#cd3131' }}
              >
                ✕
              </button>
            </div>
          )}
          onAdd={() =>
            setField('related_files', [...draft.related_files, { path: '', role: '' }])
          }
          addLabel="+ 增加文件"
        />
      </Section>

      {/* 关联 API */}
      <Section title="关联 API">
        <ArrayEditor<RelatedApi>
          items={draft.related_apis}
          setItems={(next) => setField('related_apis', next)}
          renderItem={(item, idx, update) => (
            <div key={idx} className="mb-1 flex items-center gap-1">
              <select
                value={item.method}
                onChange={(e) =>
                  update({ ...item, method: e.target.value as RelatedApi['method'] })
                }
                className="w-20 rounded border px-1 py-0.5 text-2xs"
                style={{
                  backgroundColor: '#f3f3f3',
                  borderColor: '#d4d4d4',
                  color: '#1f1f1f',
                }}
              >
                {['GET', 'POST', 'PUT', 'DELETE', 'PATCH'].map((m) => (
                  <option key={m} value={m}>
                    {m}
                  </option>
                ))}
              </select>
              <input
                value={item.path}
                onChange={(e) => update({ ...item, path: e.target.value })}
                placeholder="/api/v1/xxx"
                className="flex-1 rounded border px-2 py-0.5 font-mono text-2xs"
                style={{
                  backgroundColor: '#f3f3f3',
                  borderColor: '#d4d4d4',
                  color: '#0b6bcb',
                }}
              />
              <input
                value={item.description}
                onChange={(e) => update({ ...item, description: e.target.value })}
                placeholder="描述"
                className="w-32 rounded border px-2 py-0.5 text-2xs"
                style={{
                  backgroundColor: '#f3f3f3',
                  borderColor: '#d4d4d4',
                  color: '#1f1f1f',
                }}
              />
              <button
                type="button"
                onClick={() =>
                  setField(
                    'related_apis',
                    draft.related_apis.filter((_, i) => i !== idx)
                  )
                }
                className="rounded px-1 text-2xs"
                style={{ color: '#cd3131' }}
              >
                ✕
              </button>
            </div>
          )}
          onAdd={() =>
            setField('related_apis', [
              ...draft.related_apis,
              { method: 'GET', path: '', description: '' },
            ])
          }
          addLabel="+ 增加 API"
        />
      </Section>

      {/* 关联表 */}
      <Section title="关联表">
        <ArrayEditor<RelatedTable>
          items={draft.related_tables}
          setItems={(next) => setField('related_tables', next)}
          renderItem={(item, idx, update) => (
            <div key={idx} className="mb-1 flex items-center gap-1">
              <input
                value={item.name}
                onChange={(e) => update({ ...item, name: e.target.value })}
                placeholder="t_xxx"
                className="w-32 rounded border px-2 py-0.5 font-mono text-2xs"
                style={{
                  backgroundColor: '#f3f3f3',
                  borderColor: '#d4d4d4',
                  color: '#0b6bcb',
                }}
              />
              <input
                value={item.description}
                onChange={(e) => update({ ...item, description: e.target.value })}
                placeholder="描述"
                className="flex-1 rounded border px-2 py-0.5 text-2xs"
                style={{
                  backgroundColor: '#f3f3f3',
                  borderColor: '#d4d4d4',
                  color: '#1f1f1f',
                }}
              />
              <button
                type="button"
                onClick={() =>
                  setField(
                    'related_tables',
                    draft.related_tables.filter((_, i) => i !== idx)
                  )
                }
                className="rounded px-1 text-2xs"
                style={{ color: '#cd3131' }}
              >
                ✕
              </button>
            </div>
          )}
          onAdd={() =>
            setField('related_tables', [
              ...draft.related_tables,
              { name: '', description: '' },
            ])
          }
          addLabel="+ 增加表"
        />
      </Section>

      {/* 业务规则 */}
      <Section title="业务规则">
        <ArrayEditor<string>
          items={draft.business_rules}
          setItems={(next) => setField('business_rules', next)}
          renderItem={(item, idx, update) => (
            <div key={idx} className="mb-1 flex items-center gap-1">
              <input
                value={item}
                onChange={(e) => update(e.target.value)}
                className="flex-1 rounded border px-2 py-0.5 text-2xs"
                style={{
                  backgroundColor: '#f3f3f3',
                  borderColor: '#d4d4d4',
                  color: '#1f1f1f',
                }}
              />
              <button
                type="button"
                onClick={() =>
                  setField(
                    'business_rules',
                    draft.business_rules.filter((_, i) => i !== idx)
                  )
                }
                className="rounded px-1 text-2xs"
                style={{ color: '#cd3131' }}
              >
                ✕
              </button>
            </div>
          )}
          onAdd={() => setField('business_rules', [...draft.business_rules, ''])}
          addLabel="+ 增加规则"
        />
      </Section>
    </div>
  );
}

function Section({
  title,
  children,
}: {
  title: string;
  children: React.ReactNode;
}): JSX.Element {
  return (
    <div className="mb-4">
      <h4
        className="mb-2 text-2xs font-semibold uppercase tracking-wider"
        style={{ color: '#0b6bcb' }}
      >
        {title}
      </h4>
      <div className="space-y-2">{children}</div>
    </div>
  );
}

function Field({
  label,
  children,
}: {
  label: string;
  children: React.ReactNode;
}): JSX.Element {
  return (
    <div>
      <label className="mb-0.5 block text-2xs" style={{ color: '#616161' }}>
        {label}
      </label>
      {children}
    </div>
  );
}

interface ArrayEditorProps<T> {
  items: T[];
  setItems: (next: T[]) => void;
  renderItem: (item: T, idx: number, update: (next: T) => void) => React.ReactNode;
  onAdd: () => void;
  addLabel: string;
}

function ArrayEditor<T>({
  items,
  setItems,
  renderItem,
  onAdd,
  addLabel,
}: ArrayEditorProps<T>): JSX.Element {
  return (
    <div>
      {items.map((item, idx) => (
        <div key={idx}>
          {renderItem(item, idx, (next) => {
            const arr = [...items];
            arr[idx] = next;
            setItems(arr);
          })}
        </div>
      ))}
      <button
        type="button"
        onClick={onAdd}
        className="mt-1 rounded px-2 py-0.5 text-2xs transition-colors"
        style={{
          backgroundColor: '#0e639c20',
          color: '#0451a5',
          border: '1px dashed #0e639c',
        }}
      >
        {addLabel}
      </button>
    </div>
  );
}
