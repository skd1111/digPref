/**
 * ExecutionBlock —— 单条执行链路 step 的渲染（Codex/Claude 风格）。
 *
 * BUGFIX #153（2026-08-26 用户反馈「不要显示个 toolcall」）：
 *   - 人性化文案：工具名映射为中文动作短语（写文件 / 执行命令 / 创建 Office 文档…），
 *     category 也换成中文标签；右侧显式状态词（进行中 / 已完成 / 失败）
 *   - 点击动作行 → 思维链面板定位并闪烁对应步骤（traceStore.highlight）
 *   - 展开详情改为右侧小箭头（▸），不与跳转点击冲突
 *
 * 2026-08-27 树形合并：行渲染抽为 ExecutionRow，供 ExecutionTree 复用；
 * 点击跳转携带 occurrence（同名工具第 N 次），思维链精确定位。
 */
import { useState } from 'react';
import type { ChatMessage } from '@eaide/shared-protocol';
import { useTraceStore } from '@/store/traceStore';
import { useChatStore } from '@/store/chatStore';
import { ShellOutputPanel } from './ShellOutputPanel';
import { WritePreviewCard } from './WritePreviewCard';

interface ExecutionBlockProps {
  message: ChatMessage;
  /** 同名工具/节点的全局第 N 次（1 基）；缺省回退最新一条 */
  occurrence?: number;
  /** 同类工具连续调用被树形合并时的重复次数（>1 显示 ×N 徽标，2026-08-27） */
  repeatCount?: number;
}

const CATEGORY_COLORS: Record<string, string> = {
  intent: '#0d9488',          // 青
  plan: '#2563eb',            // 蓝
  repair: '#d97706',          // 琥珀
  responder: '#8b5cf6',       // 紫
  summarise: '#8b5cf6',
  tool_call: '#0891b2',       // 深青
  tool_result: '#0891b2',
  hitl_gate: '#dc2626',       // 红
  step_done: '#059669',       // 绿（进度回执）
  skill_matched: '#c586c0',   // 紫（业务技能加载，2026-08-28）
  codenav: '#0d9488',
  'codenav.explain': '#0d9488',
  'codenav.jump': '#0d9488',
  log: '#6b7280',
};

/** category → 中文标签（面向用户的动作名词，替代 TOOL_CALL 这类原始字样） */
const CATEGORY_LABEL: Record<string, string> = {
  tool_call: '工具执行',
  tool_result: '工具结果',
  file_write_preview: '写前预览',
  repair: '自动修复',
  auto_decision: '自动决策',
  hitl_gate: '人工审批',
  search: '搜索',
  log: '日志',
  intent: '意图识别',
  plan: '任务规划',
  decompose: '任务分解',
  responder: '回答生成',
  summarise: '回答生成',
  step_done: '进度',
  skill_matched: '技能加载',
};

/** 工具名 → 中文动作短语（未收录的回退「调用 {name}」，#153） */
const TOOL_ACTION: Record<string, string> = {
  shell: '执行命令',
  write_file: '写入文件',
  edit_file: '编辑文件',
  read_file: '读取文件',
  list_dir: '浏览目录',
  search_files: '搜索文件',
  office_create: '创建 Office 文档',
  office_edit: '编辑 Office 文档',
  office_read: '读取 Office 文档',
  office_validate: '校验 Office 文档',
  file_to_markdown: '文档转 Markdown',
  excel_query: '查询 Excel 数据',
  excel_export: '导出 Excel',
  pdf_merge: '合并 PDF',
  pdf_split: '拆分 PDF',
  word_generate: '生成 Word 文档',
  run_sql: '执行 SQL',
  db_query: '查询数据库',
  db_execute: '执行数据库操作',
  web_search: '联网搜索',
  grep: '搜索代码内容',
  symbol_search: '检索代码符号',
  calculate: '计算',
  regex_eval: '正则求值',
  datetime_now: '查询时间',
  update_todos: '更新任务进度',
};

function colorFor(category: string | undefined): string {
  if (!category) return '#6b7280';
  return CATEGORY_COLORS[category] ?? '#6b7280';
}

/** 工具名 → 中文动作短语（未收录回退「调用 {name}」）；useAgentStream 的进度回执也复用 */
export function toolActionLabel(toolName: string): string {
  return TOOL_ACTION[toolName] ?? `调用 ${toolName}`;
}

/** 人性化正文：工具执行块把工具名换成动作短语，其余保留原文 */
function humanBody(category: string | undefined, content: string): string {
  if (category === 'tool_call' || category === 'tool_result') {
    return toolActionLabel(content.trim());
  }
  return content;
}

/** 状态词：进行中 / 已完成 / 失败（配合图标，一眼看清做完没有，#153） */
function statusText(status: string | undefined): string {
  if (status === 'running') return '进行中';
  if (status === 'err') return '失败';
  return '已完成';
}

/** 跳转查询键：工具块用工具名，其余用 category（=节点名） */
export function jumpQueryFor(message: ChatMessage): string {
  return message.category === 'tool_call' || message.category === 'tool_result'
    ? message.content.trim()
    : (message.category ?? '');
}

/**
 * ExecutionRow —— 单步行渲染（无外层卡片边框，供 ExecutionBlock / ExecutionTree 复用）。
 * 一行 = 状态图标 + 中文动作（点击跳思维链）+ 正文 + 状态词 + 耗时 + ▸ 详情。
 */
export function ExecutionRow({
  message,
  occurrence,
  repeatCount,
}: ExecutionBlockProps): JSX.Element {
  const [open, setOpen] = useState(false);
  const setHighlight = useTraceStore((s) => s.setHighlight);
  const color = colorFor(message.category);
  const status = message.status ?? 'ok';
  const latency =
    message.latencyMs != null ? ` · ${message.latencyMs}ms` : '';
  const label =
    (message.category && CATEGORY_LABEL[message.category]) || message.category || '步骤';
  const body = humanBody(message.category, message.content);
  // 执行过程可视化（阶段三）：工具卡副标题 —— tool_progress 阶段文案实时刷新；
  // callId 从消息 id（tool-<callId>，BUGFIX #164 配对键）反推。
  const callId = message.id.startsWith('tool-') ? message.id.slice('tool-'.length) : '';
  const previewCallId = message.id.startsWith('preview-')
    ? message.id.slice('preview-'.length)
    : '';
  const progress = useChatStore((s) => (callId ? s.toolProgressByCall[callId] : undefined));

  /** 点击动作 → 思维链定位对应步骤（带 occurrence 精确定位同名多次调用） */
  const jumpToTrace = (): void => {
    const query = jumpQueryFor(message);
    if (query) setHighlight(query, occurrence);
  };

  return (
    <>
      {/* 一行式 summary：图标 + 中文动作 + 状态词；点动作跳思维链，点 ▸ 看详情 */}
      <div className="flex w-full items-center gap-2 px-3 py-1.5" style={{ color: '#202124' }}>
        {status === 'running' ? (
          <span
            className="animate-spin-ring flex-shrink-0 rounded-full"
            style={{
              width: 11,
              height: 11,
              border: '2px solid #d1d5db',
              borderTopColor: color,
            }}
            title="进行中"
          />
        ) : (
          <span
            className="flex-shrink-0 font-bold"
            style={{ color, width: 14 }}
            title={statusText(status)}
          >
            {status === 'err' ? '✗' : '✓'}
          </span>
        )}
        <button
          type="button"
          onClick={jumpToTrace}
          title="点击查看执行过程中的对应步骤"
          className="flex-shrink-0 font-semibold transition-opacity hover:opacity-70 hover:underline"
          style={{ color }}
        >
          {label}
        </button>
        <span className="flex-1 truncate">{body}</span>
        {/* 同类合并次数徽标（执行树压缩行，2026-08-27）：同类工具连续 N 次只占一行 */}
        {repeatCount != null && repeatCount > 1 && (
          <span
            className="flex-shrink-0 rounded px-1 font-mono"
            style={{ backgroundColor: '#f3f4f6', color: '#6b7280' }}
            title={`连续 ${repeatCount} 次同类调用`}
          >
            ×{repeatCount}
          </span>
        )}
        <span
          className="flex-shrink-0 rounded px-1 py-px text-[10px] font-medium"
          style={{
            color: status === 'err' ? '#dc2626' : status === 'running' ? '#6b7280' : '#059669',
            backgroundColor:
              status === 'err' ? '#fef2f2' : status === 'running' ? '#f3f4f6' : '#ecfdf5',
          }}
        >
          {statusText(status)}
        </span>
        {latency && (
          <span className="flex-shrink-0 font-mono" style={{ color: '#9ca3af' }}>
            {latency}
          </span>
        )}
        <button
          type="button"
          onClick={() => setOpen((v) => !v)}
          title={open ? '收起详情' : '展开详情'}
          className="flex-shrink-0 transition-opacity hover:opacity-70"
          style={{ color: '#9ca3af' }}
        >
          {open ? '▾' : '▸'}
        </button>
      </div>
      {/* 进度副标题（执行过程可视化）：长耗时工具的阶段文案，跑完自动消失 */}
      {status === 'running' && progress && (
        <div className="truncate px-3 pb-1 text-[10px]" style={{ color: '#6b7280' }}>
          {progress}
        </div>
      )}
      {/* shell 流式输出 / 写前 Diff 预览（执行过程可视化）：挂在 Row 层，
          保证独立卡（ExecutionBlock）与树形合并（ExecutionTree）两条渲染路径都能看到；
          无数据时组件自己返 null */}
      {callId && <ShellOutputPanel callId={callId} />}
      {previewCallId && <WritePreviewCard callId={previewCallId} />}
      {open && (
        <div
          className="border-t px-3 py-2"
          style={{ borderColor: '#f0efed', color: '#6b7280' }}
        >
          {/* 详情：完整 content + runId，方便用户复制 */}
          <pre className="whitespace-pre-wrap break-all font-mono text-[10px]">
            {message.content}
          </pre>
          {message.runId && (
            <div className="mt-1 text-[10px]" style={{ color: '#9ca3af' }}>
              run_id={message.runId}
            </div>
          )}
        </div>
      )}
    </>
  );
}

export function ExecutionBlock({ message, occurrence }: ExecutionBlockProps): JSX.Element {
  const color = colorFor(message.category);

  return (
    <div
      className="my-1 rounded-lg text-2xs"
      style={{
        backgroundColor: '#fafaf9',
        border: '1px solid #f0efed',
        borderLeft: `3px solid ${color}`,
      }}
    >
      {/* 输出面板 / 预览卡由 ExecutionRow 内部渲染（与树形合并路径共享） */}
      <ExecutionRow message={message} {...(occurrence != null ? { occurrence } : {})} />
    </div>
  );
}
