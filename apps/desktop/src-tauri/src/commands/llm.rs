//! LLM 配置命令 —— 让前端切换后端时直接落盘并重启 Agent。
//!
//! 设计：配置写到 `%APPDATA%\eaide\llm-config.json`，
//! 启动 Agent 子进程时把它读出来注入环境变量 `EAIDE_LLM_BACKEND=...`。
//! 切后端 = 改 JSON + 杀子进程 + 重启。
//!
//! ⚠️ 安全警告：api_key 字段以明文存储在 JSON 文件中。
//! 当前依赖 Windows 用户级文件权限保护（%APPDATA% 仅当前用户可读）。
//! 长期方案应将 api_key 存入系统 Keyring（与 envconfig 的 SecretStr 一致）。

use std::fs;
use std::path::PathBuf;

use serde::{Deserialize, Serialize};
use tauri::State;

use crate::agent_manager::app_log;
use crate::error::{AppError, AppResult};
use crate::state::AppState;

const CONFIG_FILE: &str = "llm-config.json";

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
            active: "mock".into(),
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

fn config_path() -> PathBuf {
    // 与 config.rs 中 audit_db_path 同一目录约定：%APPDATA%/eaide/
    std::env::var("APPDATA")
        .map(PathBuf::from)
        .unwrap_or_else(|_| PathBuf::from("."))
        .join("eaide")
        .join(CONFIG_FILE)
}

#[tauri::command]
pub fn llm_get_config() -> AppResult<LLMSettings> {
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
pub fn llm_set_config(settings: State<'_, AppState>, cfg: LLMSettings) -> AppResult<()> {
    app_log(&format!(
        "[llm_config] set active={} (ollama={}, private={}, custom={})",
        cfg.active,
        cfg.ollama.base_url.is_empty() == false,
        cfg.private.base_url.is_empty() == false,
        cfg.custom.base_url.is_empty() == false,
    ));

    // 1. 落盘
    let p = config_path();
    if let Some(parent) = p.parent() {
        let _ = fs::create_dir_all(parent);
    }
    let json = serde_json::to_string_pretty(&cfg).map_err(AppError::from)?;
    fs::write(&p, json).map_err(AppError::from)?;
    app_log(&format!("[llm_config] 已写入 {}", p.display()));

    // 2. 重启 Agent —— 把新 env vars 注入新进程
    // 我们没存 AppState 的可写句柄（config 不可变），所以新进程会从 JSON 读。
    // 但为了一次性把 env vars 设到新进程，我们在这里：
    //   a) 重启 Agent（agent_manager.rs 暴露新方法 restart_with_env）
    //   b) 临时把 env vars 写到 env file 让 Agent 读
    //
    // 简化：仅写 JSON + 调用 restart；下次 Agent 启动时由 agent_manager.rs 读 JSON → 转 env vars。
    super::agent::agent_restart(settings)?;
    Ok(())
}
