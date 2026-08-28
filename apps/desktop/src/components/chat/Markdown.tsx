/**
 * Markdown —— 轻量零依赖 Markdown 渲染器（2026-08-07）。
 *
 * 为什么不用 react-markdown：内网环境不便装新依赖。本组件把 Markdown
 * 解析成 React 元素树（绝不走 dangerouslySetInnerHTML），天然免疫 XSS。
 *
 * 支持：围栏代码块 / 标题 / 无序有序列表（两级缩进）/ 引用 / 表格 /
 *       分隔线 / 任务列表（2026-08-10 起渲染为 aicss 风格 To-do 卡片）/
 *       行内加粗、斜体、行内代码、链接。
 * 不支持（按纯文本降级）：图片、脚注、嵌套引用。
 *
 * 样式由父级 `.md-body` class 提供（见 globals.css）。
 */
import { useMemo, useState, type ReactNode } from 'react';
import { AiTodoList } from './AiStatus';
import { FilePathChip, isFilePath, renderTextWithPaths } from './FilePathChip';

/** 行内标记正则：`code`、**bold**、*em*、~~del~~、[text](url)；\x0A 即换行符 */
/* \x0A 为有意使用的控制字符（排除跨行匹配） */
/* eslint-disable no-control-regex */
const INLINE_RE =
  /(`[^`\x0A]+`)|(\*\*[^*\x0A]+\*\*)|(\*[^*\x0A]+\*)|(~~[^~\x0A]+~~)|(\[[^\]\x0A]+\]\([^)\s]+\))/g;
/* eslint-enable no-control-regex */

/** 链接 URL 白名单：只放行 http(s)，其余协议（javascript: 等）按纯文本渲染 */
function safeUrl(url: string): string {
  return /^https?:\/\//i.test(url) ? url : '#';
}

/** 行内语法解析为 React 节点（递归处理 bold 内部的 em 等）。
 *  纯文本与行内代码中的文件路径（2026-08-26）渲染为可点击 FilePathChip：
 *  左键默认程序打开，右键资源管理器/复制等菜单。 */
export function renderInline(text: string): ReactNode[] {
  const nodes: ReactNode[] = [];
  let last = 0;
  let k = 0;
  for (const m of text.matchAll(INLINE_RE)) {
    const idx = m.index ?? 0;
    if (idx > last) nodes.push(...renderTextWithPaths(text.slice(last, idx), `t${k}`));
    const s = m[0];
    const key = `i${k++}`;
    if (s.startsWith('`')) {
      const inner = s.slice(1, -1);
      // 行内代码整体是文件路径 → 可交互胶囊（点击直接打开）；否则普通代码样式
      if (isFilePath(inner)) {
        nodes.push(<FilePathChip key={key} path={inner.trim()} />);
      } else {
        nodes.push(<code key={key} className="md-ic">{inner}</code>);
      }
    } else if (s.startsWith('**')) {
      nodes.push(<strong key={key}>{renderInline(s.slice(2, -2))}</strong>);
    } else if (s.startsWith('~~')) {
      nodes.push(<del key={key}>{s.slice(2, -2)}</del>);
    } else if (s.startsWith('[')) {
      const sep = s.indexOf('](');
      const label = s.slice(1, sep);
      const url = s.slice(sep + 2, -1);
      nodes.push(
        <a key={key} href={safeUrl(url)} target="_blank" rel="noreferrer" className="md-link">
          {label}
        </a>,
      );
    } else {
      nodes.push(<em key={key}>{renderInline(s.slice(1, -1))}</em>);
    }
    last = idx + s.length;
  }
  if (last < text.length) nodes.push(...renderTextWithPaths(text.slice(last), `t${k}`));
  return nodes;
}

// ---- 块级解析 ---------------------------------------------------------------

interface ListItem {
  indent: number;
  ordered: boolean;
  marker: number;
  text: string;
}

function isTableSep(line: string): boolean {
  const cells = line.split('|').map((c) => c.trim());
  return cells.length >= 2 && cells.every((c) => /^:?-{2,}:?$/.test(c) || c === '');
}

function splitRow(line: string): string[] {
  return line.split('|').slice(1, -1).map((c) => c.trim());
}

function renderCodeBlock(code: string, key: string, lang: string): JSX.Element {
  return <CodeChunk key={key} code={code.replace(/\n$/, '')} lang={lang} />;
}

function renderList(items: ListItem[], key: string): JSX.Element {
  const build = (i: number, indent: number): [JSX.Element[], number] => {
    const out: JSX.Element[] = [];
    let j = i;
    while (j < items.length && items[j].indent >= indent) {
      if (items[j].indent > indent) {
        const [sub, next] = build(j, items[j].indent);
        if (out.length > 0) out[out.length - 1] = <li key={`w${j}`}>{out[out.length - 1].props.children}{sub}</li>;
        j = next;
        continue;
      }
      out.push(<li key={`l${j}`}>{renderInline(items[j].text)}</li>);
      j++;
    }
    return [out, j];
  };
  const [nodes] = build(0, items[0].indent);
  return items[0].ordered ? <ol key={key}>{nodes}</ol> : <ul key={key}>{nodes}</ul>;
}

/** 把一段无围栏代码块的行序列解析为块级元素 */
function parseBlocks(lines: string[], keyBase: string): JSX.Element[] {
  const out: JSX.Element[] = [];
  let i = 0;
  let k = 0;
  const key = (): string => `${keyBase}-${k++}`;
  const flushPara = (buf: string[]): void => {
    if (buf.length > 0) {
      out.push(<p key={key()}>{renderInline(buf.join(' '))}</p>);
      buf.length = 0;
    }
  };

  const para: string[] = [];
  while (i < lines.length) {
    const line = lines[i];
    const trimmed = line.trim();

    if (!trimmed) {
      flushPara(para);
      i++;
      continue;
    }
    // 标题
    const h = /^(#{1,4})\s+(.*)$/.exec(trimmed);
    if (h) {
      flushPara(para);
      const lvl = h[1].length;
      const content = renderInline(h[2]);
      if (lvl === 1) out.push(<h1 key={key()}>{content}</h1>);
      else if (lvl === 2) out.push(<h2 key={key()}>{content}</h2>);
      else if (lvl === 3) out.push(<h3 key={key()}>{content}</h3>);
      else out.push(<h4 key={key()}>{content}</h4>);
      i++;
      continue;
    }
    // 分隔线
    if (/^(-{3,}|\*{3,}|_{3,})$/.test(trimmed)) {
      flushPara(para);
      out.push(<hr key={key()} />);
      i++;
      continue;
    }
    // 引用（连续的 > 行合并）
    if (trimmed.startsWith('>')) {
      flushPara(para);
      const quote: string[] = [];
      while (i < lines.length && lines[i].trim().startsWith('>')) {
        quote.push(lines[i].trim().replace(/^>\s?/, ''));
        i++;
      }
      out.push(
        <blockquote key={key()}>
          {quote.map((q, qi) => (
            <p key={qi}>{renderInline(q)}</p>
          ))}
        </blockquote>,
      );
      continue;
    }
    // 表格：当前行含 | 且下一行是分隔行
    if (trimmed.includes('|') && i + 1 < lines.length && isTableSep(lines[i + 1])) {
      flushPara(para);
      const header = splitRow(trimmed);
      i += 2;
      const rows: string[][] = [];
      while (i < lines.length && lines[i].trim().includes('|')) {
        rows.push(splitRow(lines[i].trim()));
        i++;
      }
      out.push(
        <table key={key()}>
          <thead>
            <tr>
              {header.map((cell, ci) => (
                <th key={ci}>{renderInline(cell)}</th>
              ))}
            </tr>
          </thead>
          <tbody>
            {rows.map((row, ri) => (
              <tr key={ri}>
                {row.map((cell, ci) => (
                  <td key={ci}>{renderInline(cell)}</td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>,
      );
      continue;
    }
    // 列表（连续块合并，支持两级缩进）
    const lm = /^(\s*)([-*+]|\d+[.)])\s+(.*)$/.exec(line);
    if (lm) {
      flushPara(para);
      const items: ListItem[] = [];
      while (i < lines.length) {
        const m2 = /^(\s*)([-*+]|\d+[.)])\s+(.*)$/.exec(lines[i]);
        if (!m2) break;
        items.push({
          indent: Math.floor(m2[1].replace(/\t/g, '  ').length / 2),
          ordered: /\d/.test(m2[2][0]),
          marker: items.length + 1,
          text: m2[3],
        });
        i++;
      }
      // 任务列表（- [ ] / - [x]）→ aicss 风格 To-do 卡片（2026-08-10）
      const todoRe = /^\[([ xX])\]\s+(.*)$/;
      if (items.length > 0 && items.every((it) => todoRe.test(it.text))) {
        out.push(
          <AiTodoList
            key={key()}
            items={items.map((it) => {
              const m = todoRe.exec(it.text);
              return {
                text: m ? m[2] : it.text,
                done: m ? m[1].toLowerCase() === 'x' : false,
              };
            })}
          />,
        );
        continue;
      }
      out.push(renderList(items, key()));
      continue;
    }
    // 普通段落
    para.push(trimmed);
    i++;
  }
  flushPara(para);
  return out;
}

/** 围栏代码块：aicss 风格（2026-08-10）—— 语言头 + Copy 勾选态 + 行号（CSS 计数器，
 *  不进 textContent，兼容既有测试对 pre 纯文本的断言） */
function CodeChunk({ code, lang }: { code: string; lang: string }): JSX.Element {
  const [copied, setCopied] = useState(false);
  const copy = (): void => {
    void navigator.clipboard.writeText(code).then(() => {
      setCopied(true);
      setTimeout(() => setCopied(false), 1500);
    });
  };
  const lines = code.split('\n');
  return (
    <div className="md-code">
      <div className="md-code-head">
        <span className="md-code-lang">
          <svg viewBox="0 0 24 24" width="12" height="12" aria-hidden="true">
            <path
              d="m8 6-6 6 6 6M16 6l6 6-6 6"
              fill="none"
              stroke="currentColor"
              strokeWidth="1.8"
              strokeLinecap="round"
              strokeLinejoin="round"
            />
          </svg>
          {lang || 'text'}
        </span>
        <button type="button" className="md-code-copy" onClick={copy}>
          {copied ? (
            <svg viewBox="0 0 24 24" width="11" height="11" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
              <path d="m4.5 12.75 6 6 9-13.5" />
            </svg>
          ) : (
            <svg viewBox="0 0 24 24" width="11" height="11" fill="none" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
              <rect x="9" y="9" width="11" height="11" rx="2.5" />
              <path d="M5 15a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h8a2 2 0 0 1 2 2" />
            </svg>
          )}
          <span>{copied ? '已复制' : '复制'}</span>
        </button>
      </div>
      <pre>
        {lines.map((line, i) => (
          <span key={i} className="md-line">
            {line}
            {i < lines.length - 1 ? '\n' : ''}
          </span>
        ))}
      </pre>
    </div>
  );
}

/** 主入口：文本 → React 元素树 */
export function Markdown({ text }: { text: string }): JSX.Element {
  const blocks = useMemo(() => {
    const out: JSX.Element[] = [];
    const lines = text.split('\n');
    let i = 0;
    let k = 0;
    let buf: string[] = [];
    const flush = (): void => {
      if (buf.length > 0) {
        out.push(...parseBlocks(buf, `b${k}`));
        buf = [];
        k++;
      }
    };
    while (i < lines.length) {
      const fence = /^```(\S*)/.exec(lines[i].trim());
      if (fence) {
        flush();
        const codeLines: string[] = [];
        i++;
        while (i < lines.length && !/^```/.test(lines[i].trim())) {
          codeLines.push(lines[i]);
          i++;
        }
        i++; // 跳过结束围栏（文件末尾缺围栏也安全兜底）
        out.push(renderCodeBlock(codeLines.join('\n'), `c${k++}`, fence[1] ?? ''));
        continue;
      }
      buf.push(lines[i]);
      i++;
    }
    flush();
    return out;
  }, [text]);

  return <div className="md-body">{blocks}</div>;
}
