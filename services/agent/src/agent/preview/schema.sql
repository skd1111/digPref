-- Phase 15 V0 · preview_sessions 表（preview.db 物理隔离）
-- 记录每个预览会话（项目根 / 框架 / 端口 / Vite 子进程 PID / HMR 状态）
CREATE TABLE IF NOT EXISTS preview_sessions (
    id              VARCHAR(64) PRIMARY KEY,   -- UUIDv4 hex
    project_path    TEXT NOT NULL,             -- 项目根目录绝对路径
    entry_file      TEXT NOT NULL,             -- 入口文件相对路径（如 'src/main.ts'）
    framework       VARCHAR(16) NOT NULL,      -- 'vue' / 'react' / 'svelte' / 'html'
    port            INTEGER NOT NULL,          -- Vite 监听端口（5173-5300）
    url             TEXT NOT NULL,             -- http://127.0.0.1:5173
    status          VARCHAR(16) DEFAULT 'running',  -- starting/running/installing/stopped/errored
    created_at      INTEGER NOT NULL,          -- Unix 毫秒
    last_active_at  INTEGER NOT NULL,          -- 最后一次 SSE 心跳 / HMR 事件
    config_path     TEXT,                      -- 临时 .eaide-vite.config.mjs 路径
    install_progress INTEGER DEFAULT 0         -- node_modules 安装进度 0-100
);
CREATE INDEX IF NOT EXISTS idx_sessions_status ON preview_sessions(status);
CREATE INDEX IF NOT EXISTS idx_sessions_last_active ON preview_sessions(last_active_at);

-- 预览白名单追加根目录（BUGFIX #175）：用户在预览按钮确认后追加，
-- 与 settings.preview_allowed_paths 叠加生效（持久化，重启不丢）。
CREATE TABLE IF NOT EXISTS preview_allowed_roots (
    path        TEXT PRIMARY KEY,            -- 规范化后的绝对路径（正斜杠）
    created_at  INTEGER NOT NULL             -- Unix 毫秒
);
