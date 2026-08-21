/**
 * ExportBar —— 数据专家右栏（下）：导出与分享。
 *
 * V1 真接：调用 ipc.dataExport / ipc.dataSaveTemplate，后端执行 PII 脱敏 +
 * 数字水印（操作人/时间/IP）+ 导出审计（字段/行数/文件 MD5/接收人）。
 * 路径选择（2026-08-18）：导出前弹系统 save 对话框让用户选择保存位置，
 * 选中路径透传后端落盘；取消则不导出。
 */
import { useState } from 'react';
import { save } from '@tauri-apps/plugin-dialog';
import { useDataStore } from '@/store/dataStore';
import { PanelHeader } from './DataGrid';

const ACTIONS: Array<{ id: string; label: string; icon: string; color: string }> = [
  { id: 'excel', label: '导出 Excel', icon: '📊', color: '#217346' },
  { id: 'pdf', label: '导出 PDF 报表', icon: '📄', color: '#c74634' },
  { id: 'csv', label: '导出 CSV', icon: '📑', color: '#0e639c' },
  { id: 'template', label: '保存为模板', icon: '💾', color: '#059669' },
];

/** 导出格式 → save 对话框扩展名/过滤器 */
const EXPORT_EXT: Record<string, { ext: string; filterName: string }> = {
  excel: { ext: 'xlsx', filterName: 'Excel 工作簿' },
  pdf: { ext: 'pdf', filterName: 'PDF 报表' },
  csv: { ext: 'csv', filterName: 'CSV 文件' },
};

export function ExportBar(): JSX.Element {
  const result = useDataStore((s) => s.result);
  const exporting = useDataStore((s) => s.exporting);
  const doExport = useDataStore((s) => s.doExport);
  const saveTemplate = useDataStore((s) => s.saveTemplate);
  const [toast, setToast] = useState<string | null>(null);

  const onExport = async (id: string, label: string): Promise<void> => {
    if (id === 'template') {
      const ok = await saveTemplate('数据报表模板');
      setToast(ok ? '💾 已保存为报表模板' : '❌ 保存失败');
    } else {
      if (!result) {
        setToast('⚠ 请先执行查询得到结果集');
      } else {
        // 先让用户选择保存路径（2026-08-18），取消则不导出
        const meta = EXPORT_EXT[id];
        let picked: string | null = null;
        try {
          picked = await save({
            title: `${label} —— 选择保存位置`,
            defaultPath: `数据报表.${meta.ext}`,
            filters: [{ name: meta.filterName, extensions: [meta.ext] }],
          });
        } catch {
          picked = null; // 非 Tauri 环境（vitest）无对话框，回落默认路径
        }
        if (picked === null && typeof window !== 'undefined' && '__TAURI_INTERNALS__' in window) {
          return; // Tauri 环境下返回 null = 用户点了取消，不导出
        }
        const msg = await doExport(id, picked ?? undefined);
        setToast(msg || `${label} 完成`);
      }
    }
    window.setTimeout(() => setToast(null), 5000);
  };

  return (
    <div className="flex flex-col overflow-hidden border-t" style={{ borderColor: '#d0d0d0', backgroundColor: '#ffffff' }}>
      <PanelHeader title="⬇ 导出与分享" />
      <div className="grid grid-cols-2 gap-2 p-3">
        {ACTIONS.map((a) => (
          <button
            key={a.id}
            type="button"
            onClick={() => onExport(a.id, a.label)}
            disabled={exporting}
            className="flex items-center justify-center gap-1.5 rounded py-2 text-ui font-semibold transition-all hover:brightness-110"
            style={{ backgroundColor: a.color, color: '#ffffff', opacity: exporting ? 0.6 : 1 }}
          >
            <span aria-hidden>{a.icon}</span>
            {a.label}
          </button>
        ))}
      </div>
      <div className="px-3 pb-2 text-2xs" style={{ color: '#6a9955' }}>
        🔒 导出自动脱敏 + 数字水印 + 审计（可一键送「审核专家」复核）
      </div>
      {toast && (
        <div
          className="mx-3 mb-3 rounded px-3 py-2 text-2xs"
          style={{ backgroundColor: '#ececec', color: '#1f1f1f', border: '1px solid #4ec9b0' }}
        >
          {toast}
        </div>
      )}
    </div>
  );
}
