/**
 * AiExplainPanel —— Phase 12 V1：AI 解释状态简报。
 *
 * V1 简化：详细内容已内联到主对话 ExecutionBlock（Codex/Claude 风格），
 * 这里只显示一个状态徽章 + 「重新生成」按钮，方便用户知道何时在调用 LLM。
 */
import { useCodeNavStore } from '@/store/codeNavStore';

interface AiExplainPanelProps {
  symbolId: string;
  onRequestExplain: () => void;
}

export function AiExplainPanel({ symbolId, onRequestExplain }: AiExplainPanelProps): JSX.Element {
  const explanation = useCodeNavStore((s) => s.aiExplanation);
  const isExplaining = useCodeNavStore((s) => s.isExplaining);
  const showExplanation = explanation?.symbol_id === symbolId;

  return (
    <div
      className="flex-shrink-0 border-t p-3"
      style={{ borderColor: '#d4d4d4', backgroundColor: '#ffffff' }}
    >
      <div className="flex items-center gap-2">
        <span style={{ color: '#c586c0' }}>🤖</span>
        <span
          className="text-2xs font-semibold uppercase tracking-wider"
          style={{ color: '#c586c0' }}
        >
          AI 解释
        </span>
        {isExplaining ? (
          <span className="flex items-center gap-2 text-2xs" style={{ color: '#795e26' }}>
            <span
              style={{
                display: 'inline-block',
                width: 10,
                height: 10,
                border: '2px solid #dcdcaa',
                borderTopColor: 'transparent',
                borderRadius: '50%',
                animation: 'codenav-spin 0.8s linear infinite',
              }}
            />
            解释中…（详细过程看主对话）
          </span>
        ) : showExplanation && explanation ? (
          <span className="text-2xs" style={{ color: '#059669' }}>
            ✓ 已写入执行链路 · {(explanation.confidence * 100).toFixed(0)}% ·{' '}
            {explanation.latency_ms}ms
          </span>
        ) : (
          <span className="text-2xs" style={{ color: '#616161' }}>
            · 未运行
          </span>
        )}
        <button
          type="button"
          onClick={onRequestExplain}
          disabled={isExplaining}
          className="ml-auto rounded px-2 py-0.5 text-2xs transition-colors"
          style={{
            backgroundColor: isExplaining ? '#ececec' : 'transparent',
            color: isExplaining ? '#616161' : '#4f46e5',
            border: '1px solid #6366f1',
            cursor: isExplaining ? 'wait' : 'pointer',
          }}
          title="重新生成解释"
        >
          {isExplaining ? '⏳' : '↻ 重新生成'}
        </button>
      </div>
      <p className="mt-1 text-[10px]" style={{ color: '#616161' }}>
        解释内容已自动同步到主对话（按时间顺序插入 ExecutionBlock）。
      </p>
      {/* 动画只在加载时注入，避免每次 render 都创建新 <style> 节点 */}
      {isExplaining && (
        <style>{`
          @keyframes codenav-spin {
            from { transform: rotate(0deg); }
            to { transform: rotate(360deg); }
          }
        `}</style>
      )}
    </div>
  );
}