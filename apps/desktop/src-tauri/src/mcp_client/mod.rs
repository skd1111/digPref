//! 本地 MCP 服务器注册表 —— 读取 mcp.yaml，按需管理 stdio 进程。
//!
//! 借鉴 VSCode 扩展宿主的设计：
//!   - 每个 MCP 服务器是独立的 stdio 子进程
//!   - 注册表负责发现、健康检查和生命周期管理
//!   - 前端通过 asset_list 命令获取可用服务器列表
//!
//! 通常 Python Agent 直接拥有 MCP 连接；此模块用于 Rust 侧需要
//! 独立调用 MCP 的场景（如启动前的工具发现）。
mod process;

use std::collections::HashMap;
use std::fs;
use std::path::Path;
use std::sync::Mutex;

use serde::Deserialize;

use crate::error::AppResult;

/// MCP 服务器注册表（线程安全）。
pub struct McpRegistry {
    servers: Mutex<HashMap<String, ServerSpec>>,
}

/// YAML 文件中单个 MCP 服务器的配置结构。
#[derive(Debug, Clone, Deserialize)]
struct McpConfig {
    servers: HashMap<String, ServerEntry>,
}

#[derive(Debug, Clone, Deserialize)]
struct ServerEntry {
    command: String,
    #[serde(default)]
    args: Vec<String>,
    #[serde(default)]
    env: HashMap<String, String>,
}

#[derive(Debug, Clone)]
struct ServerSpec {
    name: String,
    command: String,
    args: Vec<String>,
    running: bool,
}

impl McpRegistry {
    /// 空注册表 —— 配置文件不存在或解析失败时兜底用。
    pub fn empty() -> Self {
        Self {
            servers: Mutex::new(HashMap::new()),
        }
    }

    /// 从 YAML 文件加载 MCP 服务器配置。
    ///
    /// 文件格式参见 `infra/config/mcp.example.yaml`。
    /// 文件不存在或解析失败时返回空注册表（不阻塞启动）。
    pub fn load(path: &Path) -> AppResult<Self> {
        let content = match fs::read_to_string(path) {
            Ok(c) => c,
            Err(_) => {
                // 配置文件不存在 → 空注册表，MCP 功能由 Python Agent 提供
                crate::agent_manager::app_log(&format!("[mcp] 配置文件不存在: {}，返回空注册表", path.display()));
                return Ok(Self {
                    servers: Mutex::new(HashMap::new()),
                });
            }
        };

        let config: McpConfig = match serde_yaml::from_str(&content) {
            Ok(c) => c,
            Err(e) => {
                crate::agent_manager::app_log(&format!("[mcp] 配置文件解析失败: {}，返回空注册表", e));
                return Ok(Self {
                    servers: Mutex::new(HashMap::new()),
                });
            }
        };

        let servers: HashMap<String, ServerSpec> = config
            .servers
            .into_iter()
            .map(|(name, entry)| {
                (
                    name.clone(),
                    ServerSpec {
                        name,
                        command: entry.command,
                        args: entry.args,
                        running: false,
                    },
                )
            })
            .collect();

        crate::agent_manager::app_log(&format!("[mcp] 已加载 {} 个 MCP 服务器配置", servers.len()));
        Ok(Self {
            servers: Mutex::new(servers),
        })
    }

    /// 返回前端资产树可用的服务器列表。
    pub fn list_assets(&self) -> AppResult<Vec<serde_json::Value>> {
        let g = self
            .servers
            .lock()
            .map_err(|e| crate::error::AppError::Config(format!("MCP 注册表锁 poisoned: {}", e)))?;
        Ok(g.values()
            .map(|s| {
                serde_json::json!({
                    "id": s.name,
                    "type": "mcp",
                    "label": s.name,
                    "running": s.running,
                    "command": s.command,
                })
            })
            .collect())
    }
}
