/**
 * biznav — Phase 2G 业务功能点导航类型定义。
 *
 * V0 前端独有，不进 shared-protocol（V1 接 FastAPI 时再镜像）。
 * 与 docs/design/phase-2g-business-nav.md §3.3 对齐。
 */

export type FeatureSource = 'ai' | 'manual' | 'merged';
export type FeatureRisk = 'high' | 'medium' | 'low';

export interface RelatedFile {
  /** 相对 project_root（V0 演示用绝对路径前缀） */
  path: string;
  /** "API 入口" / "业务逻辑" / "数据库操作" */
  role: string;
}

export interface RelatedApi {
  method: 'GET' | 'POST' | 'PUT' | 'DELETE' | 'PATCH';
  /** 如 /api/v1/orders */
  path: string;
  description: string;
}

export interface RelatedTable {
  /** t_order */
  name: string;
  description: string;
}

export interface Feature {
  /** slug, 如 order_create */
  id: string;
  /** 中文名 */
  name: string;
  description: string;
  /** 业务分类 */
  category: string;
  /** V0 = "demo" */
  project_name: string;
  /** V0 = "C:/demo/order-service" */
  project_root: string;
  related_files: RelatedFile[];
  related_apis: RelatedApi[];
  related_tables: RelatedTable[];
  business_rules: string[];
  risk_level: FeatureRisk;
  /** V0 全部 'manual'（演示数据） */
  source: FeatureSource;
  /** V0 全部 null */
  ai_confidence: number | null;
  /** 乐观锁 V0=1 */
  version: number;
  created_at: number;
  updated_at: number;
}

/**
 * 注入到 chatStore 的上下文载荷（V0 仅 chip 展示，不调 Agent）。
 * V1 接后端时由 BiznavChatBridge 填充 file content 后再扩展。
 */
export interface FeatureContextPayload {
  feature_id: string;
  feature_name: string;
  feature_description: string;
  related_files: RelatedFile[];
  related_apis: RelatedApi[];
  related_tables: RelatedTable[];
  business_rules: string[];
  source: FeatureSource;
}