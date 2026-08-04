//! 应用配置 —— 从环境变量 + `%APPDATA%/eaide/config.yaml`（macOS/Linux 为
//! `~/Library/Application Support/eaide` / `~/.local/share/eaide`）加载。
//!
//! 环境变量优先级高于 YAML 文件（12-factor app 原则）。
//! 所有 EAIDE_ 前缀的环境变量都会被读取。
use std::env;
use std::path::PathBuf;
use tauri::AppHandle;
use crate::agent_manager::app_log;
use crate::error::AppResult;

/// 应用运行时配置。
#[derive(Debug, Clone)]
pub struct AppConfig {
    /// Agent FastAPI 服务地址
    pub agent_base_url: String,
    /// 审计 SQLite 数据库路径（Rust 和 Python 共享）
    pub audit_db_path: PathBuf,
    /// MCP 服务器配置文件路径
    pub mcp_config_path: PathBuf,
    /// 系统资产配置文件路径
    pub systems_path: PathBuf,
    /// 开发者工具开关（F12 / Ctrl+Shift+I 切换 DevTools）。
    /// debug 构建默认开启，release 构建默认关闭；
    /// 可被 config.yaml 的 `devtools` 键或 `EAIDE_DEVTOOLS` 环境变量覆盖。
    pub devtools_enabled: bool,
}

/// config.yaml 中可被 AppConfig 识别的字段（未知字段忽略，便于后续扩展）。
#[derive(Debug, Default, serde::Deserialize)]
struct YamlConfig {
    #[serde(default)]
    devtools: Option<bool>,
}

impl AppConfig {
    /// 加载配置 —— 优先环境变量，fallback 到默认值。
    ///
    /// 支持的环境变量：
    ///   - EAIDE_AGENT_HOST / EAIDE_AGENT_PORT → agent_base_url
    ///   - EAIDE_AUDIT_DB_PATH → audit_db_path
    ///   - EAIDE_MCP_CONFIG_PATH → mcp_config_path
    ///   - EAIDE_DEVTOOLS → devtools_enabled（true/false/1/0/yes/no）
    pub fn load(_app: &AppHandle) -> AppResult<Self> {
        let yaml_cfg = load_yaml_config();

        let host = env::var("EAIDE_AGENT_HOST").unwrap_or_else(|_| "127.0.0.1".into());
        let port = env::var("EAIDE_AGENT_PORT").unwrap_or_else(|_| "8765".into());
        let agent_base_url = format!("http://{}:{}", host, port);

        let audit_db_path = env::var("EAIDE_AUDIT_DB_PATH")
            .map(PathBuf::from)
            .unwrap_or_else(|_| {
                // 默认放在用户数据目录；fallback 到当前目录
                dirs_data_dir()
                    .unwrap_or_else(|| PathBuf::from("."))
                    .join("audit.sqlite")
            });

        let mcp_config_path = env::var("EAIDE_MCP_CONFIG_PATH")
            .map(PathBuf::from)
            .unwrap_or_else(|_| PathBuf::from("mcp.yaml"));

        let systems_path = env::var("EAIDE_SYSTEMS_PATH")
            .map(PathBuf::from)
            .unwrap_or_else(|_| {
                // 与 safe_defaults 保持一致：固定放在应用数据目录，
                // 避免相对 CWD 导致 systems.yaml 飘忽不定（用户找不到）。
                dirs_data_dir()
                    .unwrap_or_else(|| PathBuf::from("."))
                    .join("systems.yaml")
            });

        let devtools_enabled = env::var("EAIDE_DEVTOOLS")
            .ok()
            .and_then(|v| parse_bool(&v))
            .or(yaml_cfg.devtools)
            .unwrap_or_else(|| cfg!(debug_assertions));

        Ok(Self {
            agent_base_url,
            audit_db_path,
            mcp_config_path,
            systems_path,
            devtools_enabled,
        })
    }

    /// 纯默认配置 —— 不读环境变量，不依赖 AppHandle。
    /// 在 fallback 模式下 AppConfig::load 也失败时兜底用。
    pub fn safe_defaults() -> Self {
        Self {
            agent_base_url: "http://127.0.0.1:8765".into(),
            audit_db_path: dirs_data_dir()
                .unwrap_or_else(|| PathBuf::from("audit.sqlite"))
                .join("audit.sqlite"),
            mcp_config_path: dirs_data_dir()
                .unwrap_or_else(|| PathBuf::from("."))
                .join("mcp.yaml"),
            systems_path: dirs_data_dir()
                .unwrap_or_else(|| PathBuf::from("."))
                .join("systems.yaml"),
            devtools_enabled: cfg!(debug_assertions),
        }
    }
}

/// 读取数据目录下的 config.yaml（best-effort：文件不存在 / 解析失败都返回默认值）。
fn load_yaml_config() -> YamlConfig {
    let Some(data_dir) = dirs_data_dir() else {
        return YamlConfig::default();
    };
    let path = data_dir.join("config.yaml");
    if !path.exists() {
        return YamlConfig::default();
    }
    match std::fs::read_to_string(&path) {
        Ok(text) => match serde_yaml::from_str::<YamlConfig>(&text) {
            Ok(cfg) => cfg,
            Err(e) => {
                app_log(&format!("[config] config.yaml 解析失败（忽略该文件）: {}", e));
                YamlConfig::default()
            }
        },
        Err(e) => {
            app_log(&format!("[config] 读取 config.yaml 失败: {}", e));
            YamlConfig::default()
        }
    }
}

/// 宽松的布尔解析：true/false/1/0/yes/no/on/off。
fn parse_bool(raw: &str) -> Option<bool> {
    match raw.trim().to_ascii_lowercase().as_str() {
        "1" | "true" | "yes" | "on" => Some(true),
        "0" | "false" | "no" | "off" => Some(false),
        _ => None,
    }
}

/// 获取用户的 eaide 数据目录（跨平台）。
fn dirs_data_dir() -> Option<PathBuf> {
    #[cfg(target_os = "windows")]
    {
        env::var("APPDATA").ok().map(|d| PathBuf::from(d).join("eaide"))
    }
    #[cfg(target_os = "macos")]
    {
        env::var("HOME").ok().map(|d| PathBuf::from(d).join("Library").join("Application Support").join("eaide"))
    }
    #[cfg(target_os = "linux")]
    {
        env::var("XDG_DATA_HOME")
            .ok()
            .map(PathBuf::from)
            .or_else(|| env::var("HOME").ok().map(|d| PathBuf::from(d).join(".local").join("share").join("eaide")))
    }
    #[cfg(not(any(target_os = "windows", target_os = "macos", target_os = "linux")))]
    {
        None
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn parse_bool_accepts_common_forms() {
        assert_eq!(parse_bool("true"), Some(true));
        assert_eq!(parse_bool("1"), Some(true));
        assert_eq!(parse_bool("YES"), Some(true));
        assert_eq!(parse_bool("on"), Some(true));
        assert_eq!(parse_bool("false"), Some(false));
        assert_eq!(parse_bool("0"), Some(false));
        assert_eq!(parse_bool("no"), Some(false));
        assert_eq!(parse_bool("off"), Some(false));
        assert_eq!(parse_bool("bogus"), None);
    }

    #[test]
    fn yaml_config_parses_devtools_key() {
        let cfg: YamlConfig = serde_yaml::from_str("devtools: true\n").unwrap();
        assert_eq!(cfg.devtools, Some(true));

        let cfg: YamlConfig = serde_yaml::from_str("devtools: false\nagent_base_url: x\n").unwrap();
        assert_eq!(cfg.devtools, Some(false));

        // 未知字段应被忽略，不导致解析失败。
        let cfg: YamlConfig = serde_yaml::from_str("some_future_key: 42\n").unwrap();
        assert_eq!(cfg.devtools, None);
    }
}
