/**
 * KnowledgeRagPanel —— 设置页「知识库 / RAG」面板。
 *
 * 两块：
 *   1. 知识库概览：文档/分块数、向量模型与维度、reranker 就绪、是否需重建 + 重建入口
 *   2. 混合检索参数：FTS5 BM25 / 向量 / RRF / reranker / 父子分块 / 上传上限等
 *
 * 参数保存后**不热应用**（写 rag_config.json），提示「重启 Agent 后生效」并给一键重启；
 * 数据（kb.db + 上传文件 + 参数 JSON）统一落数据根 knowledge/ 目录，复制即迁移。
 */
import { useEffect, useState } from 'react';
import { ipc } from '@/ipc/invoke';
import type { KbConfigResponse, KbStatus } from '@/ipc/invoke';

type FieldKind = 'bool' | 'int' | 'float';

interface FieldDef {
  key: string;
  label: string;
  desc: string;
  kind: FieldKind;
  min?: number;
  max?: number;
  step?: number;
  experimental?: boolean;
}

const FIELDS: FieldDef[] = [
  { key: 'rag_enabled', label: '启用知识库检索', desc: '总开关：关闭后聊天与审核都不走 RAG（等于现状）。', kind: 'bool' },
  { key: 'rag_bm25_enabled', label: 'BM25 关键词通道', desc: 'SQLite FTS5 + jieba 分词，精准匹配术语与条款编号（如 GB/T 22239）。', kind: 'bool' },
  { key: 'rag_vector_enabled', label: '向量语义通道', desc: '本地 bge ONNX 向量 + 余弦，理解同义与上下文。', kind: 'bool' },
  { key: 'rag_rerank_enabled', label: 'Reranker 重排', desc: 'bge-reranker 交叉编码器对候选深度打分重排（模型缺失自动跳过）。', kind: 'bool' },
  { key: 'rag_contextual_prefix_enabled', label: '标题上下文前缀', desc: '把层级标题路径拼到分块开头，缓解长文档「迷失在中间」。', kind: 'bool' },
  { key: 'rag_chunk_size', label: '子块大小（字符）', desc: '检索用的碎块大小，语义聚焦匹配更准。', kind: 'int', min: 128, max: 4096, step: 32 },
  { key: 'rag_chunk_overlap', label: '子块重叠比例', desc: '相邻子块重叠（0~0.5），避免边界信息丢失。', kind: 'float', min: 0, max: 0.5, step: 0.05 },
  { key: 'rag_parent_size', label: '父块大小（字符）', desc: 'small-to-big：命中子块后回喂给模型的父块大小。', kind: 'int', min: 512, max: 16000, step: 128 },
  { key: 'rag_top_k', label: '返回条数 Top-K', desc: '每次检索最终注入提示词的条数。', kind: 'int', min: 1, max: 20 },
  { key: 'rag_candidate_multiplier', label: '候选倍数', desc: '各通道先召回 Top-K×倍数 再 RRF 融合。', kind: 'int', min: 1, max: 10 },
  { key: 'rag_rrf_k', label: 'RRF 常数 k', desc: '倒数排名融合经验常数（越大越平滑）。', kind: 'int', min: 1, max: 500 },
  { key: 'rag_rerank_top_n', label: '重排候选数', desc: '送入 reranker 的候选条数上限。', kind: 'int', min: 1, max: 100 },
  { key: 'rag_max_file_mb', label: '上传大小上限（MB）', desc: '单个参考资料文件的大小上限。', kind: 'int', min: 1, max: 500 },
  { key: 'rag_preload_on_startup', label: '冷启动预加载', desc: '启动时后台预热向量/重排模型与索引，降低首次检索延迟。', kind: 'bool' },
  { key: 'rag_llm_enhance_enabled', label: '大模型知识库验证', desc: '总开关：关闭时文档审核/聊天检索只走本地混合检索（BM25+向量+RRF+reranker），全程不调大模型；开启后下面三项增强才按各自开关生效。', kind: 'bool' },
  { key: 'rag_llm_contextual_enabled', label: 'LLM 上下文前缀（实验）', desc: '入库期用大模型为分块生成上下文前缀；需先开启「大模型知识库验证」总开关。属索引期参数，改后需重建。', kind: 'bool', experimental: true },
  { key: 'rag_hyde_enabled', label: 'HyDE 假设文档（实验）', desc: '检索期先让大模型生成假设性回答，用其向量检索；需先开启总开关。', kind: 'bool', experimental: true },
  { key: 'rag_query_expansion_enabled', label: '查询扩展（实验）', desc: '检索期用大模型扩展多个查询视角，多路 BM25 合并；需先开启总开关。', kind: 'bool', experimental: true },
];

function clampNum(v: number, f: FieldDef): number {
  let n = v;
  if (f.min !== undefined && n < f.min) n = f.min;
  if (f.max !== undefined && n > f.max) n = f.max;
  return n;
}

export function KnowledgeRagPanel(): JSX.Element {
  const [cfg, setCfg] = useState<KbConfigResponse | null>(null);
  const [status, setStatus] = useState<KbStatus | null>(null);
  const [draft, setDraft] = useState<Record<string, string | boolean>>({});
  const [saving, setSaving] = useState(false);
  const [reindexing, setReindexing] = useState(false);
  const [toast, setToast] = useState<{ text: string; kind: 'ok' | 'err' } | null>(null);

  const flash = (text: string, kind: 'ok' | 'err'): void => {
    setToast({ text, kind });
    window.setTimeout(() => setToast(null), 4000);
  };

  const loadAll = async (): Promise<void> => {
    try {
      const ready = await ipc.agentWaitReady(15);
      if (!ready.ready) {
        flash(`⚠ Agent 未就绪（${ready.error ?? 'timeout'}）`, 'err');
        return;
      }
      const [c, s] = await Promise.all([ipc.kbConfigGet(), ipc.kbStatus()]);
      setCfg(c);
      setStatus(s);
      setReindexing(s.reindexing);
      const d: Record<string, string | boolean> = {};
      for (const f of FIELDS) {
        const v = c.config[f.key];
        d[f.key] = f.kind === 'bool' ? Boolean(v) : String(v ?? '');
      }
      setDraft(d);
    } catch (e) {
      flash(`⚠ 读取知识库配置失败 · ${String(e)}`, 'err');
    }
  };

  useEffect(() => {
    void loadAll();
    // 重建期间轮询进度
    const t = window.setInterval(() => {
      void (async () => {
        try {
          const s = await ipc.kbStatus();
          setStatus(s);
          setReindexing(s.reindexing);
        } catch {
          /* ignore */
        }
      })();
    }, 2000);
    return () => window.clearInterval(t);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const dirty = (): boolean => {
    if (!cfg) return false;
    return FIELDS.some((f) => {
      const cur = cfg.config[f.key];
      const dv = draft[f.key];
      return f.kind === 'bool' ? Boolean(cur) !== Boolean(dv) : String(cur ?? '') !== String(dv ?? '');
    });
  };

  const save = async (): Promise<void> => {
    if (!cfg) return;
    const patch: Record<string, number | boolean> = {};
    for (const f of FIELDS) {
      const dv = draft[f.key];
      if (f.kind === 'bool') {
        if (Boolean(cfg.config[f.key]) !== Boolean(dv)) patch[f.key] = Boolean(dv);
        continue;
      }
      const num = Number(String(dv).trim());
      if (!Number.isFinite(num)) {
        flash(`「${f.label}」需为数字`, 'err');
        return;
      }
      const clamped = clampNum(num, f);
      if (String(cfg.config[f.key] ?? '') !== String(clamped)) patch[f.key] = clamped;
    }
    if (Object.keys(patch).length === 0) {
      flash('没有需要保存的改动', 'ok');
      return;
    }
    setSaving(true);
    try {
      const r = await ipc.kbConfigSet(patch);
      if (!r.ok) {
        flash('⚠ 保存失败', 'err');
        return;
      }
      // 热应用后重拉配置（后端可能 clamp），以免 draft 与生效值不一致
      await loadAll();
      const needReindex = r.needs_reindex ?? [];
      if (needReindex.length > 0) {
        // 索引期参数（分块/父块/上下文前缀）：已入库数据需重建才一致
        const labels = needReindex
          .map((k) => FIELDS.find((f) => f.key === k)?.label ?? k)
          .join('、');
        if (window.confirm(`「${labels}」属索引期参数，已保存。\n已入库文档需重建索引才能一致生效，是否立即重建？`)) {
          await reindex();
        } else {
          flash('✓ 已保存并热生效（索引期参数待重建后对旧数据生效）', 'ok');
        }
      } else {
        flash('✓ 已保存并立即生效（无需重启）', 'ok');
      }
    } catch (e) {
      flash(`⚠ 保存失败 · ${String(e)}`, 'err');
    } finally {
      setSaving(false);
    }
  };

  const restart = async (): Promise<void> => {
    if (!window.confirm('重启 Agent 使新参数生效？进行中的分析会中断。')) return;
    try {
      await ipc.agentRestartNow();
      flash('✓ 已发送重启请求，稍候自动恢复', 'ok');
    } catch (e) {
      flash(`⚠ 重启失败 · ${String(e)}`, 'err');
    }
  };

  const reindex = async (): Promise<void> => {
    try {
      const r = await ipc.kbReindex();
      if (r.ok) {
        setReindexing(true);
        flash(r.already ? '重建进行中…' : '✓ 已开始重建向量索引', 'ok');
      }
    } catch (e) {
      flash(`⚠ 重建失败 · ${String(e)}`, 'err');
    }
  };

  return (
    <div className="mx-auto max-w-3xl">
      <h1 className="text-ui-lg font-semibold" style={{ color: '#1f1f1f' }}>
        知识库 / RAG
      </h1>
      <p className="mb-4 mt-1 text-2xs" style={{ color: '#616161' }}>
        审核专家与聊天共用的本地混合检索（FTS5 BM25 + 向量 + RRF + reranker）。参数统一在此管理并落库 kb.db，查询期参数保存即热生效（无需重启）；分块/父块等索引期参数改后需重建索引。数据落安装目录 knowledge/，复制即迁移。
      </p>

      {toast && (
        <div
          className="mb-3 rounded border px-3 py-2 text-2xs"
          style={{
            borderColor: toast.kind === 'ok' ? '#059669' : '#cd3131',
            color: toast.kind === 'ok' ? '#059669' : '#cd3131',
            backgroundColor: '#ffffff',
          }}
        >
          {toast.text}
        </div>
      )}

      {/* 概览 */}
      <section
        className="mb-4 rounded border p-4"
        style={{ borderColor: '#d4d4d4', backgroundColor: '#fafafa' }}
      >
        <div className="mb-2 text-ui font-semibold" style={{ color: '#1f1f1f' }}>
          知识库概览
        </div>
        {status ? (
          <div className="grid grid-cols-2 gap-x-4 gap-y-1 text-2xs" style={{ color: '#333333' }}>
            <div>文档数：{status.stats.total_docs}</div>
            <div>分块数：{status.stats.total_chunks}</div>
            <div>向量模型：{status.embed_model || '（未建库）'}</div>
            <div>维度：{status.dim || '—'}</div>
            <div>Embedding：{status.embedding_available ? '就绪' : '不可用（退化 BM25）'}</div>
            <div>Reranker：{status.reranker_available ? '就绪' : '未就绪（自动跳过）'}</div>
            <div>数据位置：{cfg?.kb_dir ?? status.db_path}</div>
            <div style={{ color: status.needs_reindex ? '#b25c1a' : '#616161' }}>
              {status.needs_reindex ? '⚠ 模型已变更，建议重建索引' : '索引与当前模型一致'}
            </div>
          </div>
        ) : (
          <div className="text-2xs" style={{ color: '#616161' }}>
            读取中…
          </div>
        )}
        <div className="mt-3 flex items-center gap-2">
          <button
            type="button"
            onClick={() => void reindex()}
            disabled={reindexing}
            className="rounded px-3 py-1 text-2xs text-white disabled:opacity-50"
            style={{ backgroundColor: '#0e639c' }}
          >
            {reindexing ? '重建中…' : '重建向量索引'}
          </button>
          {reindexing && status && (
            <span className="text-2xs" style={{ color: '#616161' }}>
              进度 {Math.round((status.reindex_progress || 0) * 100)}%
            </span>
          )}
        </div>
      </section>

      {/* 参数 */}
      <section>
        {FIELDS.map((f) => (
          <div
            key={f.key}
            className="mb-2 flex items-center justify-between gap-4 rounded border px-4 py-3"
            style={{ borderColor: '#d4d4d4', backgroundColor: '#ffffff' }}
          >
            <div className="min-w-0">
              <div className="text-ui font-semibold" style={{ color: '#1f1f1f' }}>
                {f.label}
                {f.experimental && (
                  <span className="ml-2 text-2xs" style={{ color: '#b25c1a' }}>
                    实验
                  </span>
                )}
                {cfg?.index_time?.includes(f.key) && (
                  <span className="ml-2 text-2xs" style={{ color: '#616161' }}>
                    改后需重建
                  </span>
                )}
              </div>
              <div className="mt-0.5 text-2xs leading-relaxed" style={{ color: '#616161' }}>
                {f.desc}
              </div>
            </div>
            <div className="flex flex-shrink-0 items-center gap-2">
              {f.kind === 'bool' ? (
                <input
                  type="checkbox"
                  checked={Boolean(draft[f.key])}
                  onChange={(e) => setDraft((d) => ({ ...d, [f.key]: e.target.checked }))}
                  disabled={cfg === null}
                />
              ) : (
                <input
                  className="w-28 rounded border px-2 py-1 text-2xs outline-none"
                  style={{ borderColor: '#d4d4d4', backgroundColor: '#ffffff', color: '#1f1f1f' }}
                  value={String(draft[f.key] ?? '')}
                  min={f.min}
                  max={f.max}
                  step={f.step}
                  inputMode={f.kind === 'int' ? 'numeric' : 'decimal'}
                  onChange={(e) => setDraft((d) => ({ ...d, [f.key]: e.target.value }))}
                  disabled={cfg === null}
                />
              )}
            </div>
          </div>
        ))}
      </section>

      <div className="mt-4 flex items-center justify-end gap-2">
        <button
          type="button"
          onClick={() => void restart()}
          className="rounded px-3 py-1.5 text-ui disabled:opacity-50"
          style={{ backgroundColor: '#ececec', color: '#333333' }}
        >
          🔄 重启 Agent
        </button>
        <button
          type="button"
          onClick={() => void save()}
          disabled={!dirty() || saving}
          className="rounded px-4 py-1.5 text-ui text-white disabled:opacity-50"
          style={{ backgroundColor: '#0e639c' }}
        >
          {saving ? '保存中…' : '保存（立即生效）'}
        </button>
      </div>
    </div>
  );
}
