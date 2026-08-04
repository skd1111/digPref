/**
 * yamlExport —— Phase 2G V0 手写 YAML 序列化。
 *
 * 不用 js-yaml 依赖（package.json 当前无，spec review CRITICAL #1）。
 * V0 仅 featureToYaml 单向 serialize，YAML Tab 只读。
 * V1 接后端时再补 yamlToFeature（用 js-yaml 替换）。
 */
import type { Feature } from '@/types/biznav';

function escapeString(s: string): string {
  return s.replace(/\\/g, '\\\\').replace(/"/g, '\\"');
}

function arrayYaml(arr: unknown[], level: number): string {
  if (arr.length === 0) return '[]';
  const indent = '  '.repeat(level);
  return arr
    .map((item) => {
      if (typeof item === 'string') {
        return `${indent}- "${escapeString(item)}"`;
      }
      if (typeof item === 'object' && item !== null) {
        const obj = item as Record<string, unknown>;
        const keys = Object.keys(obj);
        if (keys.length === 0) return `${indent}- {}`;
        const lines: string[] = [];
        keys.forEach((k, idx) => {
          const v = obj[k];
          if (idx === 0) {
            if (typeof v === 'string') {
              lines.push(`${indent}- ${k}: "${escapeString(v)}"`);
            } else if (v === null || v === undefined) {
              lines.push(`${indent}- ${k}: null`);
            } else if (typeof v === 'number' || typeof v === 'boolean') {
              lines.push(`${indent}- ${k}: ${v}`);
            } else {
              lines.push(`${indent}- ${k}: ${JSON.stringify(v)}`);
            }
          } else {
            if (typeof v === 'string') {
              lines.push(`${indent}  ${k}: "${escapeString(v)}"`);
            } else if (v === null || v === undefined) {
              lines.push(`${indent}  ${k}: null`);
            } else if (typeof v === 'number' || typeof v === 'boolean') {
              lines.push(`${indent}  ${k}: ${v}`);
            } else {
              lines.push(`${indent}  ${k}: ${JSON.stringify(v)}`);
            }
          }
        });
        return lines.join('\n');
      }
      return `${indent}- ${String(item)}`;
    })
    .join('\n');
}

/**
 * Feature → YAML 字符串。
 * 输出格式对齐 spec §3.2 YAML 规范（缩进 2 空格）。
 */
export function featureToYaml(f: Feature): string {
  const lines: string[] = [];
  lines.push(`id: "${escapeString(f.id)}"`);
  lines.push(`name: "${escapeString(f.name)}"`);
  lines.push(`description: "${escapeString(f.description)}"`);
  lines.push(`category: "${escapeString(f.category)}"`);
  lines.push(`project_name: "${escapeString(f.project_name)}"`);
  lines.push(`project_root: "${escapeString(f.project_root)}"`);
  lines.push(`risk_level: "${f.risk_level}"`);
  lines.push(`source: "${f.source}"`);
  lines.push(`ai_confidence: ${f.ai_confidence ?? 'null'}`);
  lines.push(`version: ${f.version}`);
  lines.push('');
  lines.push('related_files:');
  lines.push(arrayYaml(f.related_files, 1));
  lines.push('');
  lines.push('related_apis:');
  lines.push(arrayYaml(f.related_apis, 1));
  lines.push('');
  lines.push('related_tables:');
  lines.push(arrayYaml(f.related_tables, 1));
  lines.push('');
  lines.push('business_rules:');
  lines.push(arrayYaml(f.business_rules, 1));
  return lines.join('\n');
}

/**
 * YAML → Feature（V0 占位，V1 用 js-yaml 替换）。
 * 当前 V0 不调用，保留接口签名供 FeatureEditorModal 编译通过。
 */
export function yamlToFeature(yaml: string, _baseId: string): Partial<Feature> {
  // V0: 不解析。返回空对象（YAML Tab 只读，编辑走表单）。
  // V1: 用 js-yaml 替换实现。
  void yaml;
  return {};
}
