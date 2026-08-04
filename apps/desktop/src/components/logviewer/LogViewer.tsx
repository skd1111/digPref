/**
 * LogViewer —— Phase 2F+ V1.5 大文件日志查看器主组件。
 *
 * 功能：
 *   - 虚拟滚动行列表（VirtualLineList）
 *   - 搜索栏（literal / regex）+ 结果导航（上一个/下一个）
 *   - Tail -f 按钮 + 行数指示器
 *   - AI 分析面板（调用 Python loganalysis API）
 *   - 行号 + 5 级日志着色
 */

import { useState, useCallback, useEffect } from 'react';
import { VirtualLineList } from './VirtualLineList';
import { ipc } from '@/ipc/invoke';

export interface LogViewerProps {
  /** 文件路径 */
  filePath: string;
  /** 初始行（用于跳转到特定行） */
  initialLine?: number;
  /** 关闭回调 */
  onClose?: () => void;
}

/** 搜索匹配结果 */
interface SearchMatch {
  line_number: number;
  line_text: string;
  context_before: string[];
  context_after: string[];
}

export function LogViewer({ filePath, onClose }: LogViewerProps): JSX.Element {
  // ---- 状态 ---------------------------------------------------------------
  const [lines, setLines] = useState<string[]>([]);
  const [loading, setLoading] = useState(true);
  const [indexing, setIndexing] = useState(false);
  const [indexed, setIndexed] = useState(false);
  const [totalLines, setTotalLines] = useState(0);
  const [error, setError] = useState<string | null>(null);

  // 搜索
  const [searchQuery, setSearchQuery] = useState('');
  const [searchMode, setSearchMode] = useState<'literal' | 'regex'>('literal');
  const [searchResults, setSearchResults] = useState<SearchMatch[]>([]);
  const [activeResultIdx, setActiveResultIdx] = useState(-1);
  const [searching, setSearching] = useState(false);

  // Tail
  const [tailSessionId, setTailSessionId] = useState<string | null>(null);
  const [tailActive, setTailActive] = useState(false);

  // AI 分析
  const [aiSummary, setAiSummary] = useState<string | null>(null);
  const [aiLoading, setAiLoading] = useState(false);

  const highlightPattern = searchQuery
    ? new RegExp(searchQuery.replace(/[.*+?^${}()|[\]\\]/g, '\\$&'), 'gi')
    : null;

  // ---- 初始化：索引 + 加载 ----------------------------------------------
  useEffect(() => {
    let cancelled = false;
    async function init(): Promise<void> {
      setLoading(true);
      setError(null);

      // 1. 检查索引状态
      try {
        const status = await ipc.logviewerIndexStatus(filePath);
        if (status.kind === 'ready') {
          setIndexed(true);
          setTotalLines(status.line_count);
          // 加载前 500 行显示
          const result = await ipc.logviewerReadLines(filePath, 0, 500, 500 * 1024);
          if (!cancelled) {
            setLines(result.lines);
            setLoading(false);
          }
          return;
        }
      } catch {
        // 索引不存在 → 创建
      }

      // 2. 发起索引
      setIndexing(true);
      try {
        const taskId = await ipc.logviewerIndexFile(filePath);
        // 轮询等待完成
        while (!cancelled) {
          await new Promise((r) => setTimeout(r, 200));
          const snap = await ipc.logviewerTaskStatus(taskId);
          if (!snap) break;
          if (snap.status === 'completed') {
            if (!cancelled) {
              setIndexed(true);
              setTotalLines(snap.summary?.line_count ?? 0);
              const result = await ipc.logviewerReadLines(filePath, 0, 500, 500 * 1024);
              setLines(result.lines);
            }
            break;
          }
          if (snap.status === 'failed' || snap.status === 'cancelled') {
            if (!cancelled) setError(snap.error ?? 'Indexing failed');
            break;
          }
        }
      } catch (e) {
        if (!cancelled) setError(String(e));
      } finally {
        if (!cancelled) setIndexing(false);
      }

      if (!cancelled) setLoading(false);
    }

    init();
    return () => { cancelled = true; };
  }, [filePath]);

  // ---- 搜索 -------------------------------------------------------------
  const handleSearch = useCallback(async () => {
    if (!searchQuery.trim()) {
      setSearchResults([]);
      setActiveResultIdx(-1);
      return;
    }
    setSearching(true);
    try {
      const taskId = await ipc.logviewerSearch(
        filePath, searchQuery, searchMode, 2, 2, 200, 10 * 1024 * 1024,
      );
      // 轮询等待搜索完成
      let attempts = 0;
      while (attempts < 50) {
        await new Promise((r) => setTimeout(r, 100));
        const snap = await ipc.logviewerTaskStatus(taskId);
        if (!snap || snap.status === 'completed' || snap.status === 'failed' || snap.status === 'cancelled') {
          break;
        }
        attempts++;
      }
      // TODO: V1.5 搜索结果是异步 task，当前暂不支持直接取回 matches
      // 这里先用简单的客户端高亮
      setSearchResults([]);
      setActiveResultIdx(-1);
    } catch (e) {
      console.warn('[LogViewer] search failed:', e);
    } finally {
      setSearching(false);
    }
  }, [filePath, searchQuery, searchMode]);

  // ---- Tail -------------------------------------------------------------
  const handleTailToggle = useCallback(async () => {
    if (tailActive && tailSessionId) {
      await ipc.logviewerTailStop(tailSessionId);
      setTailActive(false);
      setTailSessionId(null);
      return;
    }

    try {
      const result = await ipc.logviewerTailStart(filePath);
      setTailSessionId(result.session_id);
      setTailActive(true);
    } catch (e) {
      console.warn('[LogViewer] tail start failed:', e);
    }
  }, [filePath, tailActive, tailSessionId]);

  // ---- AI 分析 ----------------------------------------------------------
  const handleAiAnalyze = useCallback(async () => {
    setAiLoading(true);
    setAiSummary(null);
    try {
      // 调用 Python loganalysis API（通过已有的 invoke 桥）
      // V1.5: 通过 agent SSE 或 HTTP 调用
      // 这里先用本地简单统计作为 placeholder
      const errorLines = lines.filter((l) => /ERROR|FATAL/i.test(l));
      const warnLines = lines.filter((l) => /WARN/i.test(l));
      setAiSummary(
        `[Local Analysis]\n` +
        `Total lines loaded: ${lines.length}\n` +
        `ERROR/FATAL lines: ${errorLines.length}\n` +
        `WARN lines: ${warnLines.length}\n` +
        `Top 3 error patterns: (connect to Python agent for full AI analysis)`,
      );
    } catch (e) {
      setAiSummary(`Analysis failed: ${String(e)}`);
    } finally {
      setAiLoading(false);
    }
  }, [lines]);

  // ---- 渲染 -------------------------------------------------------------
  return (
    <div className="flex flex-col h-full bg-white dark:bg-gray-900">
      {/* Toolbar */}
      <div className="flex items-center gap-2 px-3 py-2 border-b border-gray-200 dark:border-gray-700 bg-gray-50 dark:bg-gray-800">
        {/* File info */}
        <span className="text-xs text-gray-500 dark:text-gray-400 truncate flex-1" title={filePath}>
          📄 {filePath.split(/[/\\]/).pop()}
          {totalLines > 0 && (
            <span className="ml-2 text-gray-400">
              ({totalLines.toLocaleString()} lines{indexed ? ' · indexed' : ''})
            </span>
          )}
        </span>

        {/* Search */}
        <div className="flex items-center gap-1">
          <select
            className="text-xs border border-gray-300 dark:border-gray-600 rounded px-1 py-0.5 bg-white dark:bg-gray-700"
            value={searchMode}
            onChange={(e) => setSearchMode(e.target.value as 'literal' | 'regex')}
          >
            <option value="literal">Literal</option>
            <option value="regex">Regex</option>
          </select>
          <input
            type="text"
            className="text-xs border border-gray-300 dark:border-gray-600 rounded px-2 py-0.5 w-40 bg-white dark:bg-gray-700"
            placeholder="Search..."
            value={searchQuery}
            onChange={(e) => setSearchQuery(e.target.value)}
            onKeyDown={(e) => e.key === 'Enter' && handleSearch()}
          />
          <button
            className="text-xs px-2 py-0.5 rounded bg-blue-500 text-white hover:bg-blue-600 disabled:opacity-50"
            onClick={handleSearch}
            disabled={searching || !searchQuery.trim()}
          >
            {searching ? '…' : 'Find'}
          </button>
        </div>

        {/* Tail */}
        <button
          className={`text-xs px-2 py-0.5 rounded ${
            tailActive
              ? 'bg-green-500 text-white hover:bg-green-600'
              : 'bg-gray-200 dark:bg-gray-700 text-gray-600 dark:text-gray-300 hover:bg-gray-300'
          }`}
          onClick={handleTailToggle}
          title={tailActive ? 'Stop tail' : 'Start tail -f'}
        >
          {tailActive ? '⬤ Tail' : '○ Tail'}
        </button>

        {/* AI */}
        <button
          className="text-xs px-2 py-0.5 rounded bg-purple-500 text-white hover:bg-purple-600 disabled:opacity-50"
          onClick={handleAiAnalyze}
          disabled={aiLoading || lines.length === 0}
        >
          {aiLoading ? '…' : '🤖 AI'}
        </button>

        {/* Close */}
        {onClose && (
          <button
            className="text-xs px-2 py-0.5 rounded text-gray-400 hover:text-gray-600 dark:hover:text-gray-200"
            onClick={onClose}
          >
            ✕
          </button>
        )}
      </div>

      {/* Main content */}
      <div className="flex-1 flex overflow-hidden">
        {/* Line list */}
        <div className="flex-1 relative">
          {loading ? (
            <div className="absolute inset-0 flex items-center justify-center text-gray-400">
              {indexing ? '⏳ Indexing file...' : 'Loading...'}
            </div>
          ) : error ? (
            <div className="absolute inset-0 flex items-center justify-center text-red-500 text-sm">
              {error}
            </div>
          ) : (
            <VirtualLineList
              lines={lines}
              lineHeight={22}
              overscan={20}
              highlightPattern={highlightPattern}
              highlightLine={activeResultIdx >= 0 ? searchResults[activeResultIdx]?.line_number ?? null : null}
              tailMode={tailActive}
              className="h-full"
            />
          )}
        </div>

        {/* AI analysis panel */}
        {aiSummary && (
          <div className="w-72 border-l border-gray-200 dark:border-gray-700 p-3 overflow-auto bg-gray-50 dark:bg-gray-800">
            <div className="flex items-center justify-between mb-2">
              <h3 className="text-xs font-semibold text-gray-500 uppercase">AI Analysis</h3>
              <button
                className="text-xs text-gray-400 hover:text-gray-600"
                onClick={() => setAiSummary(null)}
              >
                ✕
              </button>
            </div>
            <pre className="text-xs whitespace-pre-wrap text-gray-700 dark:text-gray-300 font-mono">
              {aiSummary}
            </pre>
          </div>
        )}
      </div>

      {/* Status bar */}
      <div className="flex items-center gap-3 px-3 py-1 border-t border-gray-200 dark:border-gray-700 bg-gray-50 dark:bg-gray-800 text-xs text-gray-400">
        <span>{indexed ? '✓ Indexed' : '○ Not indexed'}</span>
        {totalLines > 0 && <span>| {totalLines.toLocaleString()} lines</span>}
        {tailActive && (
          <span className="text-green-500">| Tail active</span>
        )}
        {searchResults.length > 0 && (
          <span className="text-blue-500">| {searchResults.length} matches</span>
        )}
      </div>
    </div>
  );
}
