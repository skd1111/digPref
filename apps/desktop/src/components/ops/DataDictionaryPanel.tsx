/**
 * DataDictionaryPanel —— Phase 2H 数据字典面板。
 *
 * 公共参数单独维护（不进 Skill）：Skill 里写「查字典 key」，这里维护参数值。
 * 支持搜索 / 新建 / 编辑 / 删除；seed 条目可显式覆盖。
 */
import { useEffect, useState } from "react";
import { useDictStore } from "@/store/dictStore";
import type { DictItem } from "@/types/datadict";

interface EditState {
  mode: "create" | "edit";
  key?: string;
  category: string;
  label: string;
  value: string;
  description: string;
}

const EMPTY_EDIT: EditState = {
  mode: "create",
  category: "通用",
  label: "",
  value: "",
  description: "",
};

export function DataDictionaryPanel(): JSX.Element {
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

  const [query, setQuery] = useState("");
  const [categoryFilter, setCategoryFilter] = useState<string>("");
  const [edit, setEdit] = useState<EditState | null>(null);
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    void loadItems(categoryFilter || undefined);
    void loadCategories();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [categoryFilter]);

  const handleSave = async (): Promise<void> => {
    if (!edit || !edit.label.trim()) {
      window.alert("请填写参数名称");
      return;
    }
    setSaving(true);
    if (edit.mode === "create") {
      if (!edit.key?.trim()) {
        window.alert("请填写字典 key（唯一标识，Skill 里引用）");
        setSaving(false);
        return;
      }
      await createItem({
        key: edit.key.trim(),
        category: edit.category || "通用",
        label: edit.label.trim(),
        value: edit.value,
        description: edit.description,
      });
    } else if (edit.key) {
      await updateItem(edit.key, {
        category: edit.category,
        label: edit.label,
        value: edit.value,
        description: edit.description,
      });
    }
    setSaving(false);
    setEdit(null);
  };

  const filtered = categoryFilter
    ? items.filter((i) => i.category === categoryFilter)
    : items;

  return (
    <div
      className="flex h-full flex-col"
      style={{ backgroundColor: "#f3f3f3" }}
    >
      {/* 搜索 */}
      <div
        className="flex-shrink-0 border-b p-2"
        style={{ borderColor: "#d4d4d4" }}
      >
        <input
          type="text"
          value={query}
          onChange={(e) => {
            setQuery(e.target.value);
            void search(e.target.value);
          }}
          placeholder="🔍 搜索 key / 名称 / 值…"
          className="w-full rounded border px-2 py-1 text-2xs outline-none focus:border-[#007acc]"
          style={{
            backgroundColor: "#ffffff",
            borderColor: "#d4d4d4",
            color: "#1f1f1f",
          }}
        />
        <div className="mt-1.5 flex flex-wrap items-center gap-1">
          <button
            type="button"
            onClick={() => setCategoryFilter("")}
            className="rounded px-1.5 py-0.5 text-2xs"
            style={{
              backgroundColor: !categoryFilter ? "#0e639c" : "#e0e0e0",
              color: !categoryFilter ? "#ffffff" : "#616161",
            }}
          >
            全部
          </button>
          {categories.map((c) => (
            <button
              key={c}
              type="button"
              onClick={() => setCategoryFilter(c === categoryFilter ? "" : c)}
              className="rounded px-1.5 py-0.5 text-2xs"
              style={{
                backgroundColor: categoryFilter === c ? "#0e639c" : "#e0e0e0",
                color: categoryFilter === c ? "#ffffff" : "#616161",
              }}
            >
              {c}
            </button>
          ))}
          <button
            type="button"
            onClick={() => setEdit({ ...EMPTY_EDIT })}
            className="ml-auto rounded px-2 py-0.5 text-2xs font-semibold"
            style={{ backgroundColor: "#0e639c", color: "#ffffff" }}
            title="新建公共参数"
          >
            ＋ 新建
          </button>
        </div>
      </div>

      {error && (
        <div
          className="flex items-center gap-2 border-b px-2 py-1 text-2xs"
          style={{
            borderColor: "#d4d4d4",
            backgroundColor: "#fdecec",
            color: "#cd3131",
          }}
        >
          ⚠ {error}
          <button
            type="button"
            onClick={clearError}
            className="ml-auto underline"
          >
            关闭
          </button>
        </div>
      )}

      {/* 编辑表单 */}
      {edit && (
        <div
          className="flex-shrink-0 border-b p-2"
          style={{ borderColor: "#d4d4d4", backgroundColor: "#ffffff" }}
        >
          <div
            className="mb-1.5 text-2xs font-semibold"
            style={{ color: "#0451a5" }}
          >
            {edit.mode === "create" ? "＋ 新建公共参数" : `✏️ 编辑 ${edit.key}`}
          </div>
          <div className="grid grid-cols-2 gap-1.5">
            {edit.mode === "create" && (
              <input
                value={edit.key ?? ""}
                onChange={(e) => setEdit({ ...edit, key: e.target.value })}
                placeholder="key（唯一，Skill 引用用）"
                className="rounded border px-1.5 py-1 text-2xs"
                style={{
                  backgroundColor: "#f3f3f3",
                  borderColor: "#d4d4d4",
                  color: "#1f1f1f",
                }}
              />
            )}
            <input
              value={edit.category}
              onChange={(e) => setEdit({ ...edit, category: e.target.value })}
              placeholder="分类"
              className="rounded border px-1.5 py-1 text-2xs"
              style={{
                backgroundColor: "#f3f3f3",
                borderColor: "#d4d4d4",
                color: "#1f1f1f",
              }}
            />
            <input
              value={edit.label}
              onChange={(e) => setEdit({ ...edit, label: e.target.value })}
              placeholder="参数名称"
              className="rounded border px-1.5 py-1 text-2xs"
              style={{
                backgroundColor: "#f3f3f3",
                borderColor: "#d4d4d4",
                color: "#1f1f1f",
              }}
            />
            <input
              value={edit.value}
              onChange={(e) => setEdit({ ...edit, value: e.target.value })}
              placeholder="参数值"
              className="rounded border px-1.5 py-1 text-2xs"
              style={{
                backgroundColor: "#f3f3f3",
                borderColor: "#d4d4d4",
                color: "#1f1f1f",
              }}
            />
            <input
              value={edit.description}
              onChange={(e) =>
                setEdit({ ...edit, description: e.target.value })
              }
              placeholder="说明"
              className="rounded border px-1.5 py-1 text-2xs"
              style={{
                backgroundColor: "#f3f3f3",
                borderColor: "#d4d4d4",
                color: "#1f1f1f",
              }}
            />
          </div>
          <div className="mt-1.5 flex justify-end gap-1.5">
            <button
              type="button"
              onClick={() => setEdit(null)}
              className="rounded px-2 py-0.5 text-2xs"
              style={{ backgroundColor: "#e0e0e0", color: "#1f1f1f" }}
            >
              取消
            </button>
            <button
              type="button"
              onClick={() => void handleSave()}
              disabled={saving}
              className="rounded px-2 py-0.5 text-2xs font-semibold"
              style={{ backgroundColor: "#0e639c", color: "#ffffff" }}
            >
              {saving ? "保存中…" : "保存"}
            </button>
          </div>
        </div>
      )}

      {/* 列表 */}
      <div className="flex-1 overflow-auto p-2">
        {loading && filtered.length === 0 ? (
          <div
            className="py-6 text-center text-2xs"
            style={{ color: "#616161" }}
          >
            加载中…
          </div>
        ) : filtered.length === 0 ? (
          <div
            className="py-6 text-center text-2xs"
            style={{ color: "#616161" }}
          >
            {query ? "没有匹配的公共参数" : "暂无公共参数，点「＋ 新建」添加"}
          </div>
        ) : (
          <div className="space-y-1.5">
            {filtered.map((item) => (
              <DictItemRow
                key={item.key}
                item={item}
                onEdit={() =>
                  setEdit({
                    mode: "edit",
                    key: item.key,
                    category: item.category,
                    label: item.label,
                    value: item.value,
                    description: item.description,
                  })
                }
                onDelete={() => {
                  if (
                    window.confirm(
                      `确认删除公共参数「${item.label}」（${item.key}）？`,
                    )
                  ) {
                    void deleteItem(item.key);
                  }
                }}
              />
            ))}
          </div>
        )}
      </div>
    </div>
  );
}

function DictItemRow({
  item,
  onEdit,
  onDelete,
}: {
  item: DictItem;
  onEdit: () => void;
  onDelete: () => void;
}): JSX.Element {
  return (
    <div
      className="rounded border p-2"
      style={{ backgroundColor: "#ffffff", borderColor: "#e0e0e0" }}
    >
      <div className="flex items-start gap-1">
        <div className="min-w-0 flex-1">
          <div className="flex items-center gap-1.5">
            <span
              className="font-mono text-2xs font-semibold"
              style={{ color: "#0451a5" }}
            >
              {item.key}
            </span>
            <span
              className="rounded px-1 text-[10px]"
              style={{ backgroundColor: "#ececec", color: "#616161" }}
            >
              {item.category}
            </span>
            {item.source === "seed" && (
              <span className="text-[10px]" style={{ color: "#795e26" }}>
                内置
              </span>
            )}
          </div>
          <div
            className="mt-0.5 text-2xs font-semibold"
            style={{ color: "#1f1f1f" }}
          >
            {item.label}
          </div>
          <div className="mt-0.5 text-2xs" style={{ color: "#0b6bcb" }}>
            {item.value}
          </div>
          {item.description && (
            <div className="mt-0.5 text-2xs" style={{ color: "#616161" }}>
              {item.description}
            </div>
          )}
        </div>
        <div className="flex flex-shrink-0 gap-1">
          <button
            type="button"
            onClick={onEdit}
            className="rounded px-1.5 py-0.5 text-2xs hover:bg-vscode-border"
            style={{ color: "#0451a5" }}
            title="编辑"
          >
            ✏️
          </button>
          <button
            type="button"
            onClick={onDelete}
            className="rounded px-1.5 py-0.5 text-2xs hover:bg-vscode-border"
            style={{ color: "#cd3131" }}
            title="删除"
          >
            ✕
          </button>
        </div>
      </div>
    </div>
  );
}
