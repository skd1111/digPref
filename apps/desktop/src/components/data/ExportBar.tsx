/**
 * ExportBar —— 数据专家右栏（下）：导出与分享。
 *
 * V1 真接：调用 ipc.dataExport / ipc.dataSaveTemplate，后端执行 PII 脱敏 +
 * 数字水印（操作人/时间/IP）+ 导出审计（字段/行数/文件 MD5/接收人）。
 */
import { useState } from 'react';
import { useDataStore } from '@/store/dataStore';
import { PanelHeader } from './DataGrid';

const ACTIONS: Array<{ id: string; label: string; icon: string; color: string }> = [
  { id: 'excel', label: '导出 Excel', icon: '📊', color: '#217346' },
  { id: 'pdf', label: '导出 PDF 报表', icon: '📄', color: '#c74634' },
  { id: 'csv', label: '导出 CSV', icon: '📑', color: '#0e639c' },
  { id: 'template', label: '保存为模板', icon: '💾', color: '#059669' },
];

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
        const msg = await doExport(id);
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
