/**
 * officeAnchor —— Office 版式预览的「风险定位」注入件（2026-11-06）。
 *
 * 背景：审核完成后打开 OfficeCLI 渲染的版式原文（docx/xlsx/pptx → HTML），
 * 用户希望自动滚动到风险所在行并高亮，而不是自己在长文档里找。
 *
 * 可行性依据（实测 OfficeCLI 渲染产物）：每个段落是
 * `<p data-path="/body/p[N]" style="...">`，正文文本完整保留（表格为 <table>/<td>），
 * 因此可在渲染页内按「原文证据文本」定位到具体段落并高亮。
 *
 * 安全红线：
 *   - 注入脚本随渲染页自身在 iframe 内执行（sandbox="allow-scripts"，无 allow-same-origin），
 *     只能操作 iframe 自身 DOM，无法访问父页面；
 *   - 锚点文本只做 textContent 比较，绝不写入 DOM（不产生 XSS 面）；
 *   - JSON 内联时转义 `</script`，防止提前闭合脚本块。
 */

/** 版式预览定位锚点（一般一条风险点一个锚点）。 */
export interface OfficePreviewAnchor {
  /** 锚点 id（一般取 finding_id），写入 DOM 属性便于联动 */
  id: string;
  /** 目标文本（原文证据摘录） */
  text: string;
  /** 风险等级 → 高亮配色（critical / high / medium / low） */
  level?: string;
}

/** 风险等级 → 高亮主色（与 doc_review 的风险配色一致）。 */
const LEVEL_COLORS: Record<string, string> = {
  critical: "#b3261e",
  high: "#cd3131",
  medium: "#b25c1a",
  low: "#059669",
};

/** OfficeCLI 可渲染的格式（可做版式预览 + 风险定位高亮）。 */
export const OFFICE_RENDERABLE_EXTS = new Set(["docx", "xlsx", "pptx"]);

/** 注入脚本内使用的文本规范化（与后端 matcher 的思路一致：去空白 + 去标点）。 */
const NORM_JS = `
function eaideNorm(s){
  return String(s == null ? '' : s)
    .replace(/[\\s\\u3000]+/g, '')
    .replace(/[，。；：,.;:、（）()《》【】“”"'‘’]/g, '');
}`.trim();

/**
 * 生成注入到渲染页尾部的 `<style> + <script>` 片段。
 *
 * 无有效锚点时返回空串（不注入任何内容，保持渲染页原样）。
 */
export function buildAnchorInjection(anchors: OfficePreviewAnchor[]): string {
  const valid = (anchors ?? []).filter((a) => (a.text ?? "").trim().length > 0);
  if (valid.length === 0) return "";

  // 内联 JSON：转义 `</script`（避免提前闭合）与行分隔符（U+2028/2029 在旧解析器中非法）
  const json = JSON.stringify(valid.map((a) => ({ id: a.id, text: a.text, level: a.level ?? "high" })))
    .replace(/<\/script/gi, "<\\/script")
    .replace(/\u2028/g, "\\u2028")
    .replace(/\u2029/g, "\\u2029");

  const style = `
.eaide-hl{position:relative;}
.eaide-hl-row{background-color:rgba(14,99,156,0.07)!important;}
.eaide-hl[data-eaide-level="critical"]{background-color:rgba(179,38,30,0.14)!important;box-shadow:inset 3px 0 0 #b3261e;}
.eaide-hl[data-eaide-level="high"]{background-color:rgba(205,49,49,0.14)!important;box-shadow:inset 3px 0 0 #cd3131;}
.eaide-hl[data-eaide-level="medium"]{background-color:rgba(178,92,26,0.14)!important;box-shadow:inset 3px 0 0 #b25c1a;}
.eaide-hl[data-eaide-level="low"]{background-color:rgba(5,150,105,0.14)!important;box-shadow:inset 3px 0 0 #059669;}
@keyframes eaide-hl-flash{0%,100%{filter:none}50%{filter:brightness(1.6) saturate(1.6)}}
.eaide-hl-flash{animation:eaide-hl-flash 0.9s ease-in-out 2;}`.trim();

  const script = `
(function(){
  var ANCHORS = ${json};
  if (!ANCHORS.length) return;
  ${NORM_JS}
  var MIN_PROBE = 6; // 证据过短易误命中，直接跳过
  function run(){
    // 表格单元格（列）优先：风险常落在表格某列，先在 td/th 里找
    var cells = Array.prototype.slice.call(document.querySelectorAll('td,th'));
    var paras = Array.prototype.slice.call(
      document.querySelectorAll('p,li,h1,h2,h3,h4,h5,h6,blockquote')
    );
    var usedCell = {}, usedPara = {};
    var hits = 0;
    var first = null;
    // 长证据优先：先定位更具体的段落，避免短片段误命中
    var ordered = ANCHORS.slice().sort(function(a,b){ return b.text.length - a.text.length; });
    function mark(node, a) {
      node.setAttribute('data-eaide-hl', a.id);
      node.setAttribute('data-eaide-level', a.level || 'high');
      node.classList.add('eaide-hl', 'eaide-hl-flash');
      // 单元格命中 → 整行淡背景（明确「哪一行」），单元格本身强高亮（哪一列）
      var tr = node.closest ? node.closest('tr') : null;
      if (tr) tr.classList.add('eaide-hl-row');
      hits++;
      if (!first) first = node;
    }
    function tryMatch(list, used) {
      for (var ai = 0; ai < ordered.length; ai++) {
        var a = ordered[ai];
        if (a.__done) continue;
        var target = eaideNorm(a.text);
        if (!target) continue;
        var probe = target.length > 24 ? target.slice(0, 24) : target;
        if (probe.length < MIN_PROBE) continue;
        for (var i = 0; i < list.length; i++) {
          if (used[i]) continue;
          var node = list[i];
          var txt = eaideNorm(node.textContent);
          if (!txt || txt.indexOf(probe) === -1) continue;
          used[i] = true;
          a.__done = true;
          mark(node, a);
          break;
        }
      }
    }
    tryMatch(cells, usedCell);   // 先定位列
    tryMatch(paras, usedPara);   // 再定位正文段落
    if (first) {
      setTimeout(function(){
        // block=纵向居中；inline=横向也滚到可见（宽表格的「对应列」必须可见）
        try { first.scrollIntoView({ behavior: 'smooth', block: 'center', inline: 'center' }); } catch (e) {}
      }, 80);
    }
    try {
      parent.postMessage({ type: 'eaide-anchor-result', hits: hits, total: ANCHORS.length }, '*');
    } catch (e) {}
  }
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', function(){ run(); setTimeout(run, 400); });
  } else {
    run();
    setTimeout(run, 400);
  }
})();`.trim();

  return `<style>${style}</style><script>${script}</script>`;
}

/**
 * 把注入片段追加到渲染页 HTML 尾部（优先插到 `</body>` 前，否则直接追加）。
 */
export function injectAnchors(html: string, anchors: OfficePreviewAnchor[]): string {
  const injection = buildAnchorInjection(anchors);
  if (!injection) return html;
  const idx = html.lastIndexOf("</body>");
  if (idx >= 0) return html.slice(0, idx) + injection + html.slice(idx);
  return html + injection;
}

/** 风险等级 → 主色（供父页面提示条使用）。 */
export function anchorColor(level?: string): string {
  return LEVEL_COLORS[level ?? "high"] ?? LEVEL_COLORS.high;
}
