/**
 * DataDictView —— 数据字典全屏维护视图（ActivityBar `data-dict` 横切顶级入口）。
 *
 * 2026-11-09 交互改造（用户要求）：原 SideBar 260px 窄条卡片列表不友好，改为全屏铺平、
 * 以树的形式维护（会话 SideBar 与右侧控制台在本 activity 下折叠/不挂载，见 WorkspaceLayout）：
 *
 *   ┌──────────────────────────────────────────────────────────────┐
 *   │ 📖 数据字典 (Data Dictionary)     [搜索…] [+ 新建参数]          │
 *   ├────────────────────────┬─────────────────────────────────────┤
 *   │ 分类树（分组可折叠）      │ 选中条目详情 / 新建·编辑表单           │
 *   │ ▼ 通用 (3)             │ key / 分类 / 名称 / 值 / 说明          │
 *   │   • biz_key  参数名称   │ [编辑] [删除]                        │
 *   │ ▶ 客户信息 (5)          │                                     │
 *   └────────────────────────┴─────────────────────────────────────┘
 */
import { useEffect, useMemo, useState } from 'react';
import { useDictStore } from '@/store/dictStore';
import type { DictItem } from '@/types/datadict';
import {
  BookIcon,
  ChevronDownIcon,
  ChevronRightIcon,
  PencilIcon,
  PlusIcon,
  SearchIcon,
  TrashIcon,
  WarningIcon,
} from '@/components/ui/Icons';

interface EditState {
  mode: 'create' | 'edit';
  key: string;
  category: string;
  label: string;
  value: string;
  description: string;
}

export function DataDictView(): JSX.Element {
  const items = useDictStore((s) => s.items);
  const categories = useDictStore((s) => s.categories);
  const loading = useDictStore((s) => s.loading);
  const error = useDictStore((s) => s.error);
  const search = useDictStore((s) => s.search);
  const loadItems = useDictStore((s) => s.loadItems);
  const loadCategories = useDictStore((s) => s.loadCategories);
  const createItem = useDictStore((s) => s.createItem);
  const updateItem = useDictStore((s) => s.updateItem);
  const deleteItem = useDictStore((s) => s.deleteItem);
  const clearError = useDictStore((s) => s.clearError);

  const [query, setQuery] = useState('');
  const [selectedKey, setSelectedKey] = useState<string | null>(null);
  const [edit, setEdit] = useState<EditState | null>(null);
  const [saving, setSaving] = useState(false);
  const [collapsed, setCollapsed] = useState<Set<string>>(new Set());

  useEffect(() => {
    void loadItems();
    void loadCategories();
  }, [loadCategories, loadItems]);

  // 分类树：按 category 分组；分组顺序沿用 store categories，搜索命中的新分类按字母序补后
  const groups = useMemo(() => {
    const map = new Map<string, DictItem[]>();
    for (const item of items) {
      const list = map.get(item.category);
      if (list) list.push(item);
      else map.set(item.category, [item]);
    }
    const ordered = [
      ...categories.filter((c) => map.has(c)),
      ...[...map.keys()]
        .filter((c) => !categories.includes(c))
        .sort((a, b) => a.localeCompare(b, 'zh')),
    ];
    return ordered.map((category) => ({ category, items: map.get(category) ?? [] }));
  }, [items, categories]);

  const selected = selectedKey
    ? (items.find((i) => i.key === selectedKey) ?? null)
    : null;

  const toggleCollapse = (category: string): void => {
    setCollapsed((prev) => {
      const next = new Set(prev);
      if (next.has(category)) next.delete(category);
      else next.add(category);
      return next;
    });
  };

  const openCreate = (): void => {
    setSelectedKey(null);
    setEdit({
      mode: 'create',
      key: '',
      category: selected?.category ?? '通用',
      label: '',
      value: '',
      description: '',
    });
  };

  const openEdit = (item: DictItem): void => {
    setSelectedKey(item.key);
    setEdit({
      mode: 'edit',
      key: item.key,
      category: item.category,
      label: item.label,
      value: item.value,
      description: item.description,
    });
  };

  const handleSave = async (): Promise<void> => {
    if (!edit) return;
    if (!edit.label.trim()) {
      window.alert('请填写参数名称');
      return;
    }
    if (edit.mode === 'create' && !edit.key.trim()) {
      window.alert('请填写字典 key（唯一标识，Skill 里引用）');
      return;
    }
    setSaving(true);
    if (edit.mode === 'create') {
      const created = await createItem({
        key: edit.key.trim(),
        category: edit.category || '通用',
        label: edit.label.trim(),
        value: edit.value,
        description: edit.description,
      });
      if (created) setSelectedKey(created.key);
    } else {
      await updateItem(edit.key, {
        category: edit.category || '通用',
        label: edit.label,
        value: edit.value,
        description: edit.description,
      });
      // 分类可能变更：刷新分类列表（树分组顺序）与列表
      await loadCategories();
      await loadItems();
      setSelectedKey(edit.key);
    }
    setSaving(false);
    setEdit(null);
  };

  const handleDelete = async (item: DictItem): Promise<void> => {
    if (!window.confirm(`确认删除公共参数「${item.label}」（${item.key}）？`)) return;
    const ok = await deleteItem(item.key);
    if (ok) {
      if (selectedKey === item.key) setSelectedKey(null);
      setEdit(null);
      void loadCategories();
    }
  };

  return (
    <div className="flex h-full flex-col" style={{ backgroundColor: '#ffffff' }}>
      {/* Header：标题 + 搜索 + 新建 */}
      <div
        className="flex h-[42px] flex-shrink-0 items-center gap-2 border-b px-3"
        style={{ borderColor: '#e0e0e0', backgroundColor: '#f3f3f3' }}
      >
        <span
          className="flex items-center gap-1.5 text-ui font-semibold"
          style={{ color: '#333333' }}
        >
          <BookIcon size={15} />
          数据字典
          <span className="font-normal" style={{ color: '#616161' }}>
            Data Dictionary
          </span>
        </span>
        <div className="relative ml-auto w-64">
          <span
            className="pointer-events-none absolute left-2 top-1/2 flex -translate-y-1/2 text-fg-muted"
            aria-hidden="true"
          >
            <SearchIcon size={13} />
          </span>
          <input
            type="text"
            value={query}
            onChange={(e) => {
              setQuery(e.target.value);
              void search(e.target.value);
            }}
            placeholder="搜索 key / 名称 / 值…"
            className="w-full rounded border py-1 pl-7 pr-2 text-2xs outline-none focus:border-[#007acc]"
            style={{
              backgroundColor: '#ffffff',
              borderColor: '#d4d4d4',
              color: '#1f1f1f',
            }}
          />
        </div>
        <button
          type="button"
          onClick={openCreate}
          className="btn btn-primary btn-sm"
          title="新建公共参数"
        >
          <span className="flex items-center gap-1">
            <PlusIcon size={13} />
            新建参数
          </span>
        </button>
      </div>

      {error && (
        <div
          className="flex flex-shrink-0 items-center gap-2 border-b px-3 py-1 text-2xs"
          style={{
            borderColor: '#d4d4d4',
            backgroundColor: '#fdecec',
            color: '#cd3131',
          }}
        >
          <span className="flex items-center gap-1" aria-hidden="true">
            <WarningIcon size={12} />
          </span>
          {error}
          <button type="button" onClick={clearError} className="ml-auto underline">
            关闭
          </button>
        </div>
      )}

      <div className="flex min-h-0 flex-1">
        {/* 左：分类树（分组可折叠） */}
        <div
          className="flex w-[280px] flex-shrink-0 flex-col overflow-y-auto border-r py-1.5"
          style={{ borderColor: '#e0e0e0', backgroundColor: '#f9f9f9' }}
        >
          {loading && items.length === 0 ? (
            <div className="py-6 text-center text-2xs" style={{ color: '#616161' }}>
              加载中…
            </div>
          ) : groups.length === 0 ? (
            <div className="px-4 py-6 text-center text-2xs" style={{ color: '#616161' }}>
              {query ? '没有匹配的公共参数' : '暂无公共参数，点右上「新建参数」添加'}
            </div>
          ) : (
            groups.map((g) => {
              const isCollapsed = collapsed.has(g.category);
              return (
                <div key={g.category}>
                  <button
                    type="button"
                    onClick={() => toggleCollapse(g.category)}
                    className="flex w-full items-center gap-1 px-2 py-1.5 text-2xs font-semibold transition-colors hover:bg-[#ececec]"
                    style={{ color: '#333333' }}
                    title={isCollapsed ? '展开' : '折叠'}
                  >
                    <span className="flex items-center" aria-hidden="true">
                      {isCollapsed ? (
                        <ChevronRightIcon size={12} />
                      ) : (
                        <ChevronDownIcon size={12} />
                      )}
                    </span>
                    <span className="truncate">{g.category}</span>
                    <span className="ml-auto font-normal" style={{ color: '#616161' }}>
                      ({g.items.length})
                    </span>
                  </button>
                  {!isCollapsed &&
                    g.items.map((item) => {
                      const isSelected = item.key === selectedKey && !edit;
                      return (
                        <button
                          key={item.key}
                          type="button"
                          onClick={() => {
                            setSelectedKey(item.key);
                            setEdit(null);
                          }}
                          className="flex w-full items-center gap-1.5 border-l-2 py-1 pl-6 pr-2 text-left transition-colors hover:bg-[#ececec]"
                          style={{
                            borderColor: isSelected ? '#007acc' : 'transparent',
                            backgroundColor: isSelected ? '#e7f0fa' : 'transparent',
                            color: '#1f1f1f',
                          }}
                          title={`${item.key} — ${item.label}`}
                        >
                          <span
                            className="truncate font-mono text-2xs"
                            style={{ color: '#0451a5' }}
                          >
                            {item.key}
                          </span>
                          <span className="truncate text-2xs" style={{ color: '#616161' }}>
                            {item.label}
                          </span>
                          {item.source === 'seed' && (
                            <span
                              className="ml-auto flex-shrink-0 text-[10px]"
                              style={{ color: '#795e26' }}
                            >
                              内置
                            </span>
                          )}
                        </button>
                      );
                    })}
                </div>
              );
            })
          )}
        </div>

        {/* 右：详情 / 编辑表单 */}
        <div className="flex min-w-0 flex-1 flex-col">
          {edit ? (
            <div className="flex-1 overflow-auto p-4">
              <div
                className="mb-3 flex items-center gap-1.5 text-ui font-semibold"
                style={{ color: '#0451a5' }}
              >
                {edit.mode === 'create' ? <PlusIcon size={14} /> : <PencilIcon size={14} />}
                {edit.mode === 'create' ? '新建公共参数' : `编辑 ${edit.key}`}
              </div>
              <div className="flex max-w-[560px] flex-col gap-2.5">
                <label className="flex flex-col gap-1 text-2xs" style={{ color: '#333333' }}>
                  字典 key（唯一标识，Skill 里引用）
                  <input
                    value={edit.key}
                    disabled={edit.mode === 'edit'}
                    onChange={(e) => setEdit({ ...edit, key: e.target.value })}
                    placeholder="如 customer_level"
                    className="rounded border px-2 py-1.5 text-2xs disabled:opacity-60"
                    style={{
                      backgroundColor: '#f3f3f3',
                      borderColor: '#d4d4d4',
                      color: '#1f1f1f',
                    }}
                  />
                </label>
                <label className="flex flex-col gap-1 text-2xs" style={{ color: '#333333' }}>
                  分类
                  <input
                    value={edit.category}
                    onChange={(e) => setEdit({ ...edit, category: e.target.value })}
                    placeholder="如 通用 / 客户信息"
                    className="rounded border px-2 py-1.5 text-2xs"
                    style={{
                      backgroundColor: '#f3f3f3',
                      borderColor: '#d4d4d4',
                      color: '#1f1f1f',
                    }}
                  />
                </label>
                <label className="flex flex-col gap-1 text-2xs" style={{ color: '#333333' }}>
                  参数名称 *
                  <input
                    value={edit.label}
                    onChange={(e) => setEdit({ ...edit, label: e.target.value })}
                    placeholder="业务名称"
                    className="rounded border px-2 py-1.5 text-2xs"
                    style={{
                      backgroundColor: '#f3f3f3',
                      borderColor: '#d4d4d4',
                      color: '#1f1f1f',
                    }}
                  />
                </label>
                <label className="flex flex-col gap-1 text-2xs" style={{ color: '#333333' }}>
                  参数值
                  <input
                    value={edit.value}
                    onChange={(e) => setEdit({ ...edit, value: e.target.value })}
                    className="rounded border px-2 py-1.5 font-mono text-2xs"
                    style={{
                      backgroundColor: '#f3f3f3',
                      borderColor: '#d4d4d4',
                      color: '#1f1f1f',
                    }}
                  />
                </label>
                <label className="flex flex-col gap-1 text-2xs" style={{ color: '#333333' }}>
                  说明
                  <textarea
                    value={edit.description}
                    onChange={(e) => setEdit({ ...edit, description: e.target.value })}
                    rows={3}
                    className="resize-y rounded border px-2 py-1.5 text-2xs"
                    style={{
                      backgroundColor: '#f3f3f3',
                      borderColor: '#d4d4d4',
                      color: '#1f1f1f',
                    }}
                  />
                </label>
                <div className="mt-2 flex justify-end gap-2">
                  <button
                    type="button"
                    onClick={() => setEdit(null)}
                    className="btn btn-secondary btn-sm"
                  >
                    取消
                  </button>
                  <button
                    type="button"
                    onClick={() => void handleSave()}
                    disabled={saving}
                    className="btn btn-primary btn-sm"
                  >
                    {saving ? '保存中…' : '保存'}
                  </button>
                </div>
              </div>
            </div>
          ) : selected ? (
            <div className="flex-1 overflow-auto p-4">
              <div className="mb-1 flex items-center gap-2">
                <span
                  className="select-text font-mono text-base font-semibold"
                  style={{ color: '#0451a5' }}
                >
                  {selected.key}
                </span>
                <span
                  className="rounded px-1.5 py-0.5 text-[10px]"
                  style={{ backgroundColor: '#ececec', color: '#616161' }}
                >
                  {selected.category}
                </span>
                {selected.source === 'seed' && (
                  <span
                    className="rounded px-1.5 py-0.5 text-[10px]"
                    style={{ backgroundColor: '#f6ecd9', color: '#795e26' }}
                  >
                    内置 seed
                  </span>
                )}
              </div>
              <div className="mb-4 text-2xs" style={{ color: '#616161' }}>
                更新于 {selected.updated_at > 0 ? new Date(selected.updated_at * 1000).toLocaleString() : '—'}
                {selected.updated_by ? `（${selected.updated_by}）` : ''}
              </div>
              <div className="flex max-w-[560px] flex-col gap-2.5">
                <DetailField label="参数名称" value={selected.label} />
                <DetailField label="参数值" value={selected.value} mono />
                <DetailField label="说明" value={selected.description || '—'} />
              </div>
              <div className="mt-4 flex gap-2">
                <button
                  type="button"
                  onClick={() => openEdit(selected)}
                  className="btn btn-secondary btn-sm"
                >
                  <span className="flex items-center gap-1">
                    <PencilIcon size={13} />
                    编辑
                  </span>
                </button>
                <button
                  type="button"
                  onClick={() => void handleDelete(selected)}
                  className="btn btn-danger btn-sm"
                >
                  <span className="flex items-center gap-1">
                    <TrashIcon size={13} />
                    删除
                  </span>
                </button>
              </div>
            </div>
          ) : (
            <div
              className="flex flex-1 flex-col items-center justify-center p-6 text-center"
              style={{ color: '#616161' }}
            >
              <BookIcon size={28} />
              <div className="mt-2 text-2xs">
                在左侧选择公共参数查看详情，或点「新建参数」添加
              </div>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}

/** 详情区只读字段（允许选中复制） */
function DetailField({
  label,
  value,
  mono,
}: {
  label: string;
  value: string;
  mono?: boolean;
}): JSX.Element {
  return (
    <div className="flex flex-col gap-0.5">
      <span className="text-2xs font-semibold" style={{ color: '#616161' }}>
        {label}
      </span>
      <div
        className={`select-text rounded border px-2 py-1.5 text-2xs${mono ? ' font-mono' : ''}`}
        style={{ backgroundColor: '#f9f9f9', borderColor: '#e0e0e0', color: '#1f1f1f' }}
      >
        {value || '—'}
      </div>
    </div>
  );
}
