/**
 * datadict —— Phase 2H 数据字典类型（前端独有，与 agent/datadict/models.py 对齐）。
 *
 * 公共参数单独维护（不进 Skill）：Skill 里写「查字典 key」，这里存参数值。
 */

export interface DictItem {
  key: string;
  category: string;
  label: string;
  value: string;
  description: string;
  source: "seed" | "manual";
  updated_by: string;
  created_at: number;
  updated_at: number;
}
