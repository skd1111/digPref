/**
 * ReqWorkbenchView —— reqflow V1 需求工作台（运营专家模式，三栏）。
 *
 * 左 220px 批次列表（含完成情况统计）｜中央 卡片网格（筛选/搜索/导出）
 * ｜右 320px 卡片详情编辑（含历史版本切换）。
 *
 * 入口：ActivityBar「📋 需求」；生成卡片成功后自动跳入。
 */
import { useEffect, useMemo, useState } from 'react';
import { save } from '@tauri-apps/plugin-dialog';
import { ipc } from '@/ipc/invoke';
import { useBiznavStore } from '@/store/biznavStore';
import { useReqcardStore } from '@/store/reqcardStore';
import { STATUS_META, type CardStatus } from '@/types/reqcard';
import { ReqCardDetailEditor } from '@/components/reqflow/ReqCardDetailEditor';

export function ReqWorkbenchView(): JSX.Element {
  const projectName = useBiznavStore((s) => s.projectName);
  const batches = useReqcardStore((s) => s.batches);
  const batchStats = useReqcardStore((s) => s.batchStats);
  const currentBatchId = useReqcardStore((s) => s.currentBatchId);
  const cards = useReqcardStore((s) => s.cards);
  const selectedCardId = useReqcardStore((s) => s.selectedCardId);
  const error = useReqcardStore((s) => s.error);
  const loadBatches = useReqcardStore((s) => s.loadBatches);
  const createBatch = useReqcardStore((s) => s.createBatch);
  const selectBatch = useReqcardStore((s) => s.selectBatch);
  const exportBatch = useReqcardStore((s) => s.exportBatch);
  const clearError = useReqcardStore((s) => s.clearError);

  const [statusFilter, setStatusFilter] = useState<CardStatus | 'all'>('all');
  const [search, setSearch] = useState('');
  const [exporting, setExporting] = useState(false);

  // 挂载 + 工程变化时拉批次
  useEffect(() => {
    void loadBatches(projectName);
  }, [loadBatches, projectName]);

  const filtered = useMemo(() => {
    const q = search.trim().toLowerCase();
    return cards.filter((c) => {
      if (statusFilter !== 'all' && c.status !== statusFilter) return false;
      if (!q) return true;
      return (
        c.id.toLowerCase().includes(q) ||
        c.title.toLowerCase().includes(q) ||
        c.system_name.toLowerCase().includes(q)
      );
    });
  }, [cards, statusFilter, search]);

  const selectedCard = cards.find((c) => c.id === selectedCardId) ?? null;

  const handleNewBatch = async (): Promise<void> => {
    const name = window.prompt('批次名称（留空按日期）', '');
    if (name === null) return;
    await createBatch(projectName, name.trim() || undefined);
  };

  const handleExport = async (format: 'md' | 'docx'): Promise<void> => {
    if (!currentBatchId || exporting) return;
    setExporting(true);
    try {
      const batch = batches.find((b) => b.id === currentBatchId);
      const r = await exportBatch(currentBatchId, format);
      if (!r) return;
      const ext = format === 'md' ? 'md' : 'docx';
      const defaultName = `${batch?.name ?? currentBatchId}-需求文档.${ext}`;
      const path = await save({
        defaultPath: defaultName,
        filters:
          format === 'md'
            ? [{ name: 'Markdown', extensions: ['md'] }]
            : [{ name: 'Word 文档', extensions: ['docx'] }],
      });
      if (!path) return;
      if (format === 'md' && r.markdown) {
        await ipc.reqflowWriteExport(path, { content_text: r.markdown });
      } else if (r.base64) {
        await ipc.reqflowWriteExport(path, { content_base64: r.base64 });
      }
      window.alert(`需求文档已导出：${path}`);
    } catch (e) {
      window.alert(`导出失败：${String(e)}`);
    } finally {
      setExporting(false);
    }
  };

  return (
    <div className="flex h-full" style={{ backgroundColor: '#f3f3f3' }}>
      {/* 左栏：批次列表 */}
      <div
        className="flex w-[220px] flex-shrink-0 flex-col border-r"
        style={{ borderColor: '#d4d4d4', backgroundColor: '#f8f8f8' }}
      >
        <div
          className="flex items-center justify-between border-b px-3 py-2"
          style={{ borderColor: '#d4d4d4' }}
        >
          <span className="text-ui font-semibold" style={{ color: '#333333' }}>
            📋 需求批次
          </span>
          <button
            type="button"
            onClick={() => void handleNewBatch()}
            className="rounded px-1.5 py-0.5 text-2xs"
            style={{ color: '#0451a5' }}
            title="新建需求批次"
          >
            ＋
          </button>
        </div>
        <div className="flex-1 overflow-auto p-1">
          {batches.length === 0 && (
            <div className="px-2 py-4 text-center text-2xs" style={{ color: '#616161' }}>
              暂无批次，点 ＋ 新建
            </div>
          )}
          {batches.map((b) => {
            const st = batchStats[b.id] ?? {};
            const active = b.id === currentBatchId;
            return (
              <button
                key={b.id}
                type="button"
                onClick={() => void selectBatch(b.id)}
                className="mb-1 w-full rounded px-2 py-1.5 text-left text-2xs transition-colors"
                style={{
                  backgroundColor: active ? '#0e639c' : '#ffffff',
                  color: active ? '#ffffff' : '#1f1f1f',
                  border: '1px solid #e0e0e0',
                }}
              >
                <div className="font-semibold">{b.name}</div>
                <div style={{ color: active ? '#cfe4f5' : '#616161' }}>
                  {b.id} · 共 {st.total ?? 0} · 完成 {st.done ?? 0}
                </div>
              </button>
            );
          })}
        </div>
      </div>

      {/* 中栏：卡片网格 */}
      <div className="flex min-w-0 flex-1 flex-col">
        <div
          className="flex flex-shrink-0 items-center gap-2 border-b px-3 py-2"
          style={{ borderColor: '#d4d4d4' }}
        >
          <span className="text-ui font-semibold" style={{ color: '#333333' }}>
            需求卡片
          </span>
          <select
            value={statusFilter}
            onChange={(e) => setStatusFilter(e.target.value as CardStatus | 'all')}
            className="rounded border px-1 py-0.5 text-2xs outline-none"
            style={{ backgroundColor: '#ffffff', borderColor: '#d4d4d4' }}
          >
            <option value="all">全部状态</option>
            {(Object.keys(STATUS_META) as CardStatus[]).map((s) => (
              <option key={s} value={s}>
                {STATUS_META[s].label}
              </option>
            ))}
          </select>
          <input
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            placeholder="🔍 搜索编号 / 标题 / 系统…"
            className="w-[200px] rounded border px-2 py-0.5 text-2xs outline-none"
            style={{ backgroundColor: '#ffffff', borderColor: '#d4d4d4' }}
          />
          <div className="ml-auto flex gap-1">
            <button
              type="button"
              onClick={() => void handleExport('md')}
              disabled={!currentBatchId || exporting}
              className="rounded border px-2 py-0.5 text-2xs"
              style={{
                borderColor: '#007acc',
                color: currentBatchId ? '#0451a5' : '#a0a0a0',
                backgroundColor: '#ffffff',
              }}
              title="按当前批次导出 Markdown 需求文档"
            >
              导出 MD
            </button>
            <button
              type="button"
              onClick={() => void handleExport('docx')}
              disabled={!currentBatchId || exporting}
              className="rounded border px-2 py-0.5 text-2xs"
              style={{
                borderColor: '#007acc',
                color: currentBatchId ? '#0451a5' : '#a0a0a0',
                backgroundColor: '#ffffff',
              }}
              title="按当前批次导出 Word 需求文档"
            >
              导出 Word
            </button>
          </div>
        </div>

        {error && (
          <div
            className="flex items-center gap-2 border-b px-3 py-1 text-2xs"
            style={{ borderColor: '#d4d4d4', backgroundColor: '#fdecec', color: '#cd3131' }}
          >
            ⚠ {error}
            <button type="button" onClick={clearError} className="ml-auto underline">
              关闭
            </button>
          </div>
        )}

        <div className="flex-1 overflow-auto p-3">
          {!currentBatchId && (
            <div className="py-8 text-center text-2xs" style={{ color: '#616161' }}>
              请在左侧选择批次；从功能点树「📝 发起改造需求」生成的卡片会进入对应批次
            </div>
          )}
          {currentBatchId && filtered.length === 0 && (
            <div className="py-8 text-center text-2xs" style={{ color: '#616161' }}>
              当前批次没有匹配的需求卡片
            </div>
          )}
          <div className="grid grid-cols-2 gap-2 xl:grid-cols-3">
            {filtered.map((c) => {
              const meta = STATUS_META[c.status];
              const active = c.id === selectedCardId;
              return (
                <button
                  key={c.id}
                  type="button"
                  onClick={() =>
                    useReqcardStore.setState({
                      selectedCardId: c.id,
                      viewingVersion: null,
                      versionSnapshot: null,
                    })
                  }
                  className="rounded border p-2 text-left text-2xs transition-colors"
                  style={{
                    backgroundColor: active ? '#e8f2fb' : '#ffffff',
                    borderColor: active ? '#007acc' : '#e0e0e0',
                  }}
                >
                  <div className="mb-1 flex items-center gap-1">
                    <span className="font-mono font-semibold" style={{ color: '#0451a5' }}>
                      {c.id}
                    </span>
                    <span
                      className="ml-auto rounded px-1"
                      style={{
                        backgroundColor: `${meta.color}22`,
                        color: meta.color,
                      }}
                    >
                      {meta.icon} {meta.label}
                    </span>
                  </div>
                  <div className="mb-1 truncate font-semibold" style={{ color: '#1f1f1f' }}>
                    {c.title}
                  </div>
                  <div className="flex items-center gap-2" style={{ color: '#616161' }}>
                    <span className="truncate">{c.system_name}</span>
                    <span>· 功能点 {c.feature_ids.length}</span>
                    <span className="ml-auto rounded px-1" style={{ backgroundColor: '#ececec' }}>
                      {c.priority}
                    </span>
                    <span>v{c.version}</span>
                  </div>
                </button>
              );
            })}
          </div>
        </div>
      </div>

      {/* 右栏：卡片详情编辑 */}
      <div
        className="w-[320px] flex-shrink-0 border-l"
        style={{ borderColor: '#d4d4d4', backgroundColor: '#f8f8f8' }}
      >
        {selectedCard ? (
          <ReqCardDetailEditor card={selectedCard} />
        ) : (
          <div
            className="flex h-full items-center justify-center text-2xs"
            style={{ color: '#616161' }}
          >
            ← 选择一张需求卡片
          </div>
        )}
      </div>
    </div>
  );
}
