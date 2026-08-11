//! LLM 配置命令 —— 让前端切换后端时落盘并重启 Agent。
//!
//! 双轨制统一后：active 配置的长期事实源是 router.db llm_kv（Python 侧
//! /router/active 端点读写）。本命令：
//!   - get：优先 HTTP 读 Agent（db），Agent 离线时回退遗留 json 镜像
//!   - set：先 HTTP PUT 到 Agent（写 db + 热应用），再写 json 镜像作离线邮箱，
//!     最后重启 Agent
//!
//! ⚠️ 安全警告：api_key 字段以明文存储（json 镜像 / router.db）。
//! 当前依赖 Windows 用户级文件权限保护。
//! 长期方案应将 api_key 存入系统 Keyring（与 envconfig 的 SecretStr 一致）。

use std::fs;

use serde::{Deserialize, Serialize};
use tauri::State;

use crate::agent_manager::{app_log, llm_config_file_path};
use crate::error::{AppError, AppResult};
use crate::state::AppState;

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct LLMSettings {
    /// 当前激活的后端："mock" | "ollama" | "private" | "custom"
    pub active: String,
    #[serde(default)]
    pub ollama: OllamaSettings,
    #[serde(default)]
    pub private: PrivateSettings,
    #[serde(default)]
    pub custom: CustomSettings,
}

impl LLMSettings {
    /// 返回脱敏后的副本，适合写日志。（api_key 替换为 "***"）
    pub fn redacted(&self) -> Self {
        Self {
            active: self.active.clone(),
            ollama: self.ollama.clone(),
            private: PrivateSettings {
                base_url: self.private.base_url.clone(),
                api_key: if self.private.api_key.is_empty() { String::new() } else { "***".into() },
                model: self.private.model.clone(),
            },
            custom: CustomSettings {
                base_url: self.custom.base_url.clone(),
                api_key: if self.custom.api_key.is_empty() { String::new() } else { "***".into() },
                model: self.custom.model.clone(),
            },
        }
    }
}

#[derive(Debug, Clone, Default, Serialize, Deserialize)]
pub struct OllamaSettings {
    #[serde(default = "default_ollama_url")]
    pub base_url: String,
    #[serde(default = "default_ollama_model")]
    pub model: String,
}

fn default_ollama_url() -> String { "http://127.0.0.1:11434".into() }
fn default_ollama_model() -> String { "qwen2.5:14b".into() }

#[derive(Debug, Clone, Default, Serialize, Deserialize)]
pub struct PrivateSettings {
    pub base_url: String,
    pub api_key: String,
    pub model: String,
}

#[derive(Debug, Clone, Default, Serialize, Deserialize)]
pub struct CustomSettings {
    pub base_url: String,
    pub api_key: String,
    pub model: String,
}

impl Default for LLMSettings {
    fn default() -> Self {
        Self {
            // 双轨制统一：不再默认 mock，避免"模型已注册却静默走 mock"
            active: "ollama".into(),
            ollama: OllamaSettings { base_url: default_ollama_url(), model: default_ollama_model() },
            private: PrivateSettings {
                base_url: "http://172.1.0.134:8000/v1".into(),
                api_key: "internal-no-auth".into(),
                model: "DeepSeek-RD-Llama-70B-Int8".into(),
            },
            custom: CustomSettings::default(),
        }
    }
}

fn agent_base_url() -> String {
    std::env::var("EAIDE_AGENT_BASE_URL").unwrap_or_else(|_| "http://127.0.0.1:8765".to_string())
}

fn config_path() -> std::path::PathBuf {
    // 统一收进安装目录：<安装目录>/config/llm-config.json（不再写 %APPDATA%）
    llm_config_file_path()
}

#[tauri::command]
pub async fn llm_get_config() -> AppResult<LLMSettings> {
    // 1. 优先从 Agent 读（router.db llm_kv 为唯一长期事实源）
    let url = format!("{}/router/active", agent_base_url());
    let client = reqwest::Client::builder()
        .timeout(std::time::Duration::from_secs(3))
        .build()
        .map_err(|e| AppError::Config(format!("reqwest client: {}", e)))?;
    match client.get(&url).send().await {
        Ok(resp) if resp.status().is_success() => {
            match resp.json::<LLMSettings>().await {
                Ok(s) => return Ok(s),
                Err(e) => app_log(&format!("[llm_config] get: agent 响应解析失败: {} → 回退 json", e)),
            }
        }
        Ok(resp) => app_log(&format!(
            "[llm_config] get: agent 返回 {} → 回退 json",
            resp.status().as_u16()
        )),
        Err(e) => app_log(&format!("[llm_config] get: agent 不可达({}) → 回退 json", e)),
    }
    // 2. 回退：遗留 json 镜像（离线邮箱）
    let p = config_path();
    if !p.exists() {
        return Ok(LLMSettings::default());
    }
    let raw = fs::read_to_string(&p).map_err(AppError::from)?;
    let s: LLMSettings = serde_json::from_str(&raw).map_err(|e| {
        AppError::Config(format!("llm-config.json 解析失败: {}", e))
    })?;
    Ok(s)
}

#[tauri::command]
pub async fn llm_set_config(settings: State<'_, AppState>, cfg: LLMSettings) -> AppResult<()> {
    app_log(&format!(
        "[llm_config] set active={} (ollama={}, private={}, custom={})",
        cfg.active,
        !cfg.ollama.base_url.is_empty(),
        !cfg.private.base_url.is_empty(),
        !cfg.custom.base_url.is_empty(),
    ));

    // 1. 优先写 Agent（router.db llm_kv + 热应用；重启前的旧进程仍在运行）
    let url = format!("{}/router/active", agent_base_url());
    let client = reqwest::Client::builder()
        .timeout(std::time::Duration::from_secs(3))
        .build()
        .map_err(|e| AppError::Config(format!("reqwest client: {}", e)))?;
    match client.put(&url).json(&cfg).send().await {
        Ok(resp) if resp.status().is_success() => {
            app_log("[llm_config] set: 已写入 router.db（经 Agent /router/active）");
        }
        Ok(resp) => app_log(&format!(
            "[llm_config] set: agent 返回 {} → 仅写 json 邮箱（启动时迁移）",
            resp.status().as_u16()
        )),
        Err(e) => app_log(&format!(
            "[llm_config] set: agent 不可达({}) → 仅写 json 邮箱（启动时迁移）",
            e
        )),
    }

    // 2. json 镜像（离线邮箱：Agent 重启后 active_config 会消费并迁入 db）
    let p = config_path();
    if let Some(parent) = p.parent() {
        let _ = fs::create_dir_all(parent);
    }
    let json = serde_json::to_string_pretty(&cfg).map_err(AppError::from)?;
    fs::write(&p, json).map_err(AppError::from)?;
    app_log(&format!("[llm_config] 已写入镜像 {}", p.display()));

    // 3. 重启 Agent —— 新进程启动时 active_config 按 env > json邮箱 > db 解析
    super::agent::agent_restart(settings)?;
    Ok(())
}
