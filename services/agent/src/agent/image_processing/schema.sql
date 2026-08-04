-- Phase 14 V0 · image_processing_tasks 表（image_processing.db）
-- 记录每次图像处理任务（增强 / 矫正 / OCR），供审计 + 历史回溯
CREATE TABLE IF NOT EXISTS image_processing_tasks (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id         TEXT NOT NULL,                    -- UUID4 hex（与 SSE event 关联）
    processing_type TEXT NOT NULL,                    -- enhance / correct / ocr
    backend         TEXT NOT NULL,                    -- mock / onnx / opencv / paddleocr
    input_path      TEXT NOT NULL,
    output_path     TEXT,
    input_size      INTEGER NOT NULL DEFAULT 0,
    output_size     INTEGER NOT NULL DEFAULT 0,
    elapsed_ms      INTEGER NOT NULL DEFAULT 0,
    ok              INTEGER NOT NULL DEFAULT 0,
    error           TEXT,
    -- OCR 专属：识别文本 + 置信度 + blocks 数
    ocr_text        TEXT,
    ocr_confidence  REAL,
    ocr_block_count INTEGER,
    -- 元数据（algorithm / device / languages 等 JSON）
    meta_json       TEXT NOT NULL DEFAULT '{}',
    -- ISO 8601 UTC
    ts              TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_img_tasks_id        ON image_processing_tasks(task_id);
CREATE INDEX IF NOT EXISTS idx_img_tasks_type      ON image_processing_tasks(processing_type);
CREATE INDEX IF NOT EXISTS idx_img_tasks_ts        ON image_processing_tasks(ts);
CREATE INDEX IF NOT EXISTS idx_img_tasks_ok        ON image_processing_tasks(ok, ts);