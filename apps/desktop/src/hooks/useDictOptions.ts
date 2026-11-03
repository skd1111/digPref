/**
 * useDictOptions —— 从数据字典按 category 读取下拉选项（实时生效）。
 *
 * 数据字典 dict_items 的 description 字段支持 JSON 扩展元数据：
 *   - 成功 JSON.parse → 合并到选项的 meta 对象（icon / color / defaultPort 等）
 *   - 解析失败 → 作为普通说明文本留在 description
 *
 * 降级：IPC 失败或返回空 → options 为空数组，调用方回退内置兜底常量。
 */
import { useCallback, useEffect, useState } from 'react';
import { ipc } from '@/ipc/invoke';

export interface DictOption {
  /** 内部代码（key） */
  key: string;
  /** 前端显示名 */
  label: string;
  /** 实际值（通常同 key） */
  value: string;
  /** 纯文本说明（description 非 JSON 时） */
  description: string;
  /** JSON 扩展元数据（description 为合法 JSON 时解析结果） */
  meta?: Record<string, unknown>;
}

function parseMeta(description: string): { description: string; meta?: Record<string, unknown> } {
  const trimmed = description.trim();
  if (trimmed.startsWith('{')) {
    try {
      const obj = JSON.parse(trimmed);
      if (obj && typeof obj === 'object' && !Array.isArray(obj)) {
        return { description: '', meta: obj as Record<string, unknown> };
      }
    } catch {
      /* not valid JSON → keep as plain text */
    }
  }
  return { description };
}

function toOption(raw: Record<string, unknown>): DictOption {
  const desc = String(raw.description ?? '');
  const { description, meta } = parseMeta(desc);
  const opt: DictOption = {
    key: String(raw.key ?? ''),
    label: String(raw.label ?? ''),
    value: String(raw.value ?? raw.key ?? ''),
    description,
  };
  if (meta) opt.meta = meta;
  return opt;
}

export interface UseDictOptionsResult {
  options: DictOption[];
  loading: boolean;
  /** 重新从数据字典读取（数据字典编辑后调用） */
  refresh: () => void;
}

export function useDictOptions(dictCategory: string): UseDictOptionsResult {
  const [options, setOptions] = useState<DictOption[]>([]);
  const [loading, setLoading] = useState(false);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const r = await ipc.dictListItems(dictCategory);
      setOptions((r.items ?? []).map(toOption));
    } catch {
      /* IPC 失败 → 空数组，调用方降级到内置兜底 */
      setOptions([]);
    } finally {
      setLoading(false);
    }
  }, [dictCategory]);

  useEffect(() => {
    void load();
  }, [load]);

  return { options, loading, refresh: load };
}
