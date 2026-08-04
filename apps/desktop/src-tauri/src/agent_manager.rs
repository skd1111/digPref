//! Agent 进程管理器 —— 自动启动/停止 Python Agent 服务。
//!
//! 关键设计原则：
//!   - **Agent 启动失败不能阻止应用打开** —— 所有错误降级为 warning 日志
//!   - 日志写入文件（%LOCALAPPDATA%/Enterprise AI IDE/eaide.log），方便排查
//!   - 支持通过环境变量指定 Agent 目录，适配开发和生产环境
//!
//! 环境变量：
//!   - EAIDE_AGENT_AUTO_START=0     禁用自动启动
//!   - EAIDE_AGENT_PROJECT_DIR      手动指定 Agent 源码目录
//!   - EAIDE_AGENT_HOST / PORT      连接地址
use std::fs::OpenOptions;
use std::io::Write;
use std::path::PathBuf;
use std::process::{Child, Command, Stdio};
use std::sync::{Arc, Mutex, OnceLock};
use std::time::Duration;

#[cfg(target_os = "windows")]
use std::os::windows::io::AsRawHandle;
#[cfg(target_os = "windows")]
use std::os::windows::process::CommandExt;
#[cfg(target_os = "windows")]
use windows_sys::Win32::Foundation::CloseHandle;
#[cfg(target_os = "windows")]
use windows_sys::Win32::System::JobObjects::{
    AssignProcessToJobObject, CreateJobObjectW, SetInformationJobObject,
};
#[cfg(target_os = "windows")]
use windows_sys::Win32::System::JobObjects::{
    JobObjectExtendedLimitInformation, JOBOBJECT_BASIC_LIMIT_INFORMATION,
    JOBOBJECT_EXTENDED_LIMIT_INFORMATION, JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE,
};

use crate::config::AppConfig;

/// Agent 进程管理器 —— 自动管理子进程生命周期。
pub struct AgentManager {
    child: Arc<Mutex<Option<Child>>>,
}

/// 全局共享的 child handle —— 让 `restart_agent_process` 不需要 State 句柄。
static SHARED_CHILD: OnceLock<Arc<Mutex<Option<Child>>>> = OnceLock::new();

/// 外部复用的 Agent PID（端口已被占时记录）—— close 时也杀这个 PID，
/// 否则复用模式下 Rust 没有 child 句柄，close 永远杀不掉外部 Agent。
static REUSED_AGENT_PID: OnceLock<Mutex<Option<u32>>> = OnceLock::new();

fn reused_pid_slot() -> &'static Mutex<Option<u32>> {
    REUSED_AGENT_PID.get_or_init(|| Mutex::new(None))
}

fn record_reused_pid(pid: u32) {
    if let Ok(mut g) = reused_pid_slot().lock() {
        *g = Some(pid);
    }
}

fn take_reused_pid() -> Option<u32> {
    reused_pid_slot().lock().ok().and_then(|mut g| g.take())
}

// ---- 敏感字段脱敏（写日志前必须用）-----------------------------------
// 大小写不敏感匹配。任何含 "KEY" / "SECRET" / "PASSWORD" / "TOKEN" /
// "PRIVATE_LLM_API_KEY" / "EAIDE_SECRET" 的环境变量值都不进日志。

fn is_sensitive_env_key(key: &str) -> bool {
    let k = key.to_ascii_uppercase();
    k.contains("KEY")
        || k.contains("SECRET")
        || k.contains("PASSWORD")
        || k.contains("TOKEN")
        || k.contains("DSN")
}

fn redact_env<K: AsRef<str>>(env_vars: &[(K, String)]) -> Vec<(String, String)> {
    env_vars
        .iter()
        .map(|(k, v)| {
            if is_sensitive_env_key(k.as_ref()) {
                (k.as_ref().to_string(), "***".into())
            } else {
                (k.as_ref().to_string(), v.clone())
            }
        })
        .collect()
}

fn shared_child_slot() -> Arc<Mutex<Option<Child>>> {
    SHARED_CHILD
        .get_or_init(|| Arc::new(Mutex::new(None)))
        .clone()
}

// ---- Windows Job Object —— 父进程一死 OS 自动杀子进程树 --------------------

#[cfg(target_os = "windows")]
static JOB_OBJECT: OnceLock<usize> = OnceLock::new();

/// 在 Windows 上创建并配置一个 Job Object，KILL_ON_JOB_CLOSE。
/// 所有 spawn 出来的子进程都 AssignProcessToJobObject 进去。
/// 当父进程退出时（Last Handle 关闭），OS 自动 kill 整个进程树。
/// 这是 Windows 杀进程最保险的兜底。
#[cfg(target_os = "windows")]
fn ensure_job_object() -> Option<usize> {
    if let Some(h) = JOB_OBJECT.get() {
        return Some(*h);
    }
    unsafe {
        let handle = CreateJobObjectW(std::ptr::null(), std::ptr::null());
        if handle.is_null() {
            app_log(&format!(
                "[agent_manager] CreateJobObjectW 失败: {}",
                std::io::Error::last_os_error()
            ));
            return None;
        }
        // 配置：KILL_ON_JOB_CLOSE —— 当 Job 句柄全部关闭时，OS 杀 Job 里所有进程
        let mut info: JOBOBJECT_EXTENDED_LIMIT_INFORMATION = std::mem::zeroed();
        info.BasicLimitInformation = JOBOBJECT_BASIC_LIMIT_INFORMATION {
            LimitFlags: JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE,
            ..std::mem::zeroed()
        };
        let ok = SetInformationJobObject(
            handle,
            JobObjectExtendedLimitInformation,
            &info as *const _ as *const _,
            std::mem::size_of::<JOBOBJECT_EXTENDED_LIMIT_INFORMATION>() as u32,
        );
        if ok == 0 {
            app_log(&format!(
                "[agent_manager] SetInformationJobObject 失败: {}",
                std::io::Error::last_os_error()
            ));
            CloseHandle(handle);
            return None;
        }
        let _ = JOB_OBJECT.set(handle as usize);
        app_log(&format!(
            "[agent_manager] Windows Job Object 已创建并启用 KILL_ON_JOB_CLOSE (handle={})",
            handle as usize
        ));
        Some(handle as usize)
    }
}

/// 把刚 spawn 出来的子进程加入 Job Object。
#[cfg(target_os = "windows")]
fn attach_to_job(child: &Child) {
    if let Some(job_handle) = ensure_job_object() {
        unsafe {
            // `Child::as_raw_handle` 是 std 提供的跨平台 API
            // （在 Windows 上返回 HANDLE 伪句柄）
            let proc_handle = child.as_raw_handle() as _;
            let ok = AssignProcessToJobObject(job_handle as _, proc_handle);
            if ok == 0 {
                app_log(&format!(
                    "[agent_manager] AssignProcessToJobObject 失败: {}",
                    std::io::Error::last_os_error()
                ));
            } else {
                app_log(&format!(
                    "[agent_manager] 子进程 pid={:?} 已绑入 Job (handle=0x{:x})",
                    child.id(),
                    proc_handle as usize
                ));
            }
        }
    }
}

impl AgentManager {
    /// 启动 Agent 子进程。**绝不会返回 Err** —— 失败只记日志。
    pub fn start(config: &AppConfig) -> Self {
        let auto_start = std::env::var("EAIDE_AGENT_AUTO_START")
            .unwrap_or_else(|_| "1".into())
            == "1";

        let mgr = Self {
            child: shared_child_slot(),
        };

        app_log("[agent_manager] AgentManager::start 进入 —— 准备启动/复用 Agent 子进程");

        if !auto_start {
            app_log("Agent 自动启动已禁用 (EAIDE_AGENT_AUTO_START=0)");
            return mgr;
        }

        let host = std::env::var("EAIDE_AGENT_HOST").unwrap_or_else(|_| "127.0.0.1".into());
        let port = std::env::var("EAIDE_AGENT_PORT").unwrap_or_else(|_| "8765".into());
        app_log(&format!("[agent_manager] 配置: host={}, port={}, exe_dir={}",
            host, port,
            std::env::current_exe()
                .ok()
                .and_then(|p| p.parent().map(|d| d.to_path_buf()))
                .map(|p| p.display().to_string())
                .unwrap_or_else(|| "<unknown>".into())
        ));

        // 检查是否已有 Agent 在运行 —— 默认强制杀掉再起（修复老 EXE + 旧 Agent 兼容性问题导致 404）。
// 开发用户自己起 Agent：设 EAIDE_AGENT_AUTO_START=0，跳过这个分支；
// 然后调用 RouterEngine 走 venv/uvicorn 起的就是 EAIDE 自己 spawn 的。
// 强杀的逻辑见 kill_agent_process_tree()：按端口 PID 杀，不依赖进程名。
        if is_port_open(&host, &port) {
            app_log(&format!(
                "[agent_manager] 检测到 {}:{} 已开放，默认强杀后起新的（修 404）",
                host, port
            ));
            kill_agent_process_tree();
            // 等待端口真正释放（最多 3s），否则 spawn 会因端口已被占而失败
            for _ in 0..30 {
                if !is_port_open(&host, &port) {
                    break;
                }
                std::thread::sleep(std::time::Duration::from_millis(100));
            }
        }
        app_log(&format!("[agent_manager] 准备启动新 Agent (host={}:{})", host, port));

        // 优先使用安装包自带的 exe，其次尝试源码开发模式
        let exe_dir = std::env::current_exe()
            .ok()
            .and_then(|p| p.parent().map(|d| d.to_path_buf()))
            .unwrap_or_default();

        // bundled Agent 候选路径（按优先级）
        //  1) NSIS currentUser / Linux AppImage / macOS .app → 安装根目录扁平布局
        //  2) Tauri's own "resources/" 约定 → NSIS perMachine / dev hot-reload
        //  3) EAIDE_AGENT_EXE 环境变量（绝对路径或相对于 exe_dir）
        let candidates: Vec<PathBuf> = {
            let mut v = vec![
                exe_dir.join("eaide-agent.exe"),
                exe_dir.join("eaide-agent"),
                exe_dir.join("resources").join("eaide-agent.exe"),
                exe_dir.join("resources").join("eaide-agent"),
            ];
            if let Ok(p) = std::env::var("EAIDE_AGENT_EXE") {
                let pb = PathBuf::from(&p);
                v.push(if pb.is_absolute() {
                    pb
                } else {
                    exe_dir.join(pb)
                });
            }
            v
        };
        // 列一下所有候选路径的探测结果，便于排错
        {
            let mut probe = String::new();
            let probes: Vec<PathBuf> = vec![
                exe_dir.join("eaide-agent.exe"),
                exe_dir.join("eaide-agent"),
                exe_dir.join("resources").join("eaide-agent.exe"),
                exe_dir.join("resources").join("eaide-agent"),
            ];
            for p in &probes {
                probe.push_str(&format!("    {} → {}\n", p.display(), if p.exists() { "EXISTS" } else { "missing" }));
            }
            app_log(&format!("[agent_manager] bundled exe 候选探测:\n{}", probe));
        }
        let bundled_exe: Option<PathBuf> = candidates.into_iter().find(|p| p.exists());

        let (cmd_owned, args, cwd, env_vars): (String, Vec<String>, PathBuf, Vec<(&'static str, String)>) =
            if let Some(bundled) = bundled_exe {
                // 生产模式：安装包自带 agent（PyInstaller 单文件 exe）
                app_log(&format!("使用安装包自带 Agent: {}", bundled.display()));
                let cmd = bundled.to_string_lossy().into_owned();
                // 合并：基础 host/port + LLM 配置（可能含 mock / ollama / private）
                let mut envs: Vec<(&'static str, String)> = vec![
                    ("EAIDE_HOST", host.clone()),
                    ("EAIDE_PORT", port.clone()),
                    ("EAIDE_LOG_LEVEL", "info".to_string()),
                ];
                for (k, v) in read_llm_env() {
                    // leak 字符串到 'static 借用（这些 key 都是 'static str 形式）
                    let k_static: &'static str = Box::leak(k.into_boxed_str());
                    envs.push((k_static, v));
                }
                // 让 Agent 把 envconfig 单文件写到安装目录下：<exe_dir>/config/environments.json
                let config_dir = exe_dir.join("config");
                let config_dir_str = config_dir.to_string_lossy().into_owned();
                envs.push(("EAIDE_CONFIG_DIR", config_dir_str));
                app_log(&format!(
                    "[agent_manager] 注入 EAIDE_CONFIG_DIR={}",
                    config_dir.display()
                ));
                (cmd, vec![], exe_dir, envs)
            } else {
                // 开发模式：uv run（源码目录存在时）
                let project_dir = find_project_dir();
                if project_dir.join("pyproject.toml").exists() {
                    app_log(&format!("开发模式: uv run uvicorn (工作目录: {})", project_dir.display()));
                    let args = vec![
                        "run".to_string(),
                        "uvicorn".to_string(),
                        "agent.main:app".to_string(),
                        "--host".to_string(),
                        host.clone(),
                        "--port".to_string(),
                        port.clone(),
                        "--log-level".to_string(),
                        "info".to_string(),
                    ];
                    // 开发模式：envconfig 单文件落在源码目录下 ./config/，避免污染 APPDATA
                    let config_dir = project_dir.join("config");
                    let config_dir_str = config_dir.to_string_lossy().into_owned();
                    let dev_env: Vec<(&'static str, String)> = vec![
                        ("EAIDE_CONFIG_DIR", config_dir_str),
                    ];
                    ("uv".to_string(), args, project_dir, dev_env)
                } else {
                    app_log(&format!(
                        "未找到 Agent（候选路径均不存在，源码目录 {} 也不存在）\n\
                         请设置环境变量 EAIDE_AGENT_PROJECT_DIR 指向 Agent 源码目录，\n\
                         或 EAIDE_AGENT_EXE 指向已构建的 Agent 可执行文件",
                        project_dir.display(),
                    ));
                    return mgr;
                }
            };

        let mut cmd_builder = Command::new(&cmd_owned);
        cmd_builder.args(&args).current_dir(&cwd).stdout(Stdio::piped()).stderr(Stdio::piped());
        #[cfg(target_os = "windows")]
        cmd_builder.creation_flags(0x0800_0000); // CREATE_NO_WINDOW
        for (k, v) in &env_vars {
            cmd_builder.env(k, v);
        }
        let redacted = redact_env(&env_vars);
        app_log(&format!(
            "[agent_manager] 准备 spawn 子进程: cmd={}, args={:?}, cwd={}, env_vars={}",
            cmd_owned, args, cwd.display(),
            redacted.iter().map(|(k, v)| format!("{}={}", k, v)).collect::<Vec<_>>().join(" ")
        ));
        match cmd_builder.spawn()
        {
            Ok(mut child) => {
                let pid = child.id();
                app_log(&format!("[agent_manager] Agent 子进程已 spawn (pid={:?})，开始 30s 健康检查", pid));
                #[cfg(target_os = "windows")]
                attach_to_job(&child);
                let health_url = format!("http://{}:{}/health", host, port);
                // 用 tauri::async_runtime::spawn —— 不依赖外部 Tokio runtime，
                // 避免同步 setup 回调里 `Handle::current()` 出现 "no reactor" panic
                let _ = tauri::async_runtime::spawn(async move {
                    for i in 0..60 {
                        tokio::time::sleep(Duration::from_millis(500)).await;
                        // 用非阻塞 reqwest 客户端，避免在 async 上下文中再开一个 runtime
                        match reqwest::Client::new()
                            .get(&health_url)
                            .timeout(Duration::from_secs(1))
                            .send()
                            .await
                        {
                            Ok(resp) if resp.status().is_success() => {
                                app_log(&format!("[agent_manager] Agent 就绪 ({:.1}s) — {} 返回 2xx", (i + 1) as f64 * 0.5, health_url));
                                return;
                            }
                            Ok(resp) => {
                                if i % 10 == 0 {
                                    app_log(&format!("[agent_manager] 健康检查第 {} 轮：{} 返回 {}", i + 1, health_url, resp.status()));
                                }
                            }
                            Err(e) => {
                                if i % 10 == 0 {
                                    app_log(&format!("[agent_manager] 健康检查第 {} 轮：{} 失败 {}", i + 1, health_url, e));
                                }
                            }
                        }
                    }
                    app_log(&format!("[agent_manager] Agent 健康检查超时 (30s) —— 进程可能在但 /health 不可达: {}", health_url));
                });

                // 把 child 句柄塞进共享 slot，drop guard 后再继续
                // 先 take 出 stdout / stderr —— 这样能拿到 uvicorn 的 traceback
                let pid = child.id();
                let stdout = child.stdout.take();
                let stderr = child.stderr.take();
                let store_result = {
                    let mut guard = mgr.child.lock().expect("shared child Mutex");
                    *guard = Some(child);
                    app_log("[agent_manager] Agent 子进程句柄已存入 Arc<Mutex>");
                    Ok::<_, String>(())
                };
                if let Err(e) = store_result {
                    app_log(&format!("[agent_manager] shared child Mutex poisoned: {}", e));
                }

                if let Some(out) = stdout {
                    std::thread::spawn(move || {
                        use std::io::{BufRead, BufReader};
                        let r = BufReader::new(out);
                        for line in r.lines().map_while(Result::ok) {
                            app_log(&format!("[agent:stdout pid={:?}] {}", pid, line));
                        }
                    });
                }
                if let Some(err) = stderr {
                    std::thread::spawn(move || {
                        use std::io::{BufRead, BufReader};
                        let r = BufReader::new(err);
                        for line in r.lines().map_while(Result::ok) {
                            // stderr 大概率是 traceback —— 走 crash.log
                            crash_log(&format!("[agent:stderr pid={:?}] {}", pid, line));
                        }
                    });
                }
                app_log("[agent_manager] Agent stdout / stderr 已接到日志");
            }
            Err(e) => {
                crash_log(&format!(
                    "[agent_manager] 无法启动 Agent 子进程: {}\n命令: {} {}\n工作目录: {}",
                    e,
                    cmd_owned,
                    args.join(" "),
                    cwd.display(),
                ));
            }
        }

        mgr
    }

    /// 终止 Agent 子进程。
    pub fn stop(&self) {
        let mut guard = match self.child.try_lock() {
            Ok(g) => g,
            Err(_) => {
                app_log("[agent_manager] stop(): child Mutex 上锁失败 (可能在异步上下文中)，跳过终止");
                return;
            }
        };
        if let Some(mut child) = guard.take() {
            let pid = child.id();
            app_log(&format!("[agent_manager] 终止 Agent 子进程 (pid={:?})…", pid));
            let _ = child.kill();
            match child.wait() {
                Ok(status) => app_log(&format!("[agent_manager] Agent 已退出: {:?}", status)),
                Err(e) => app_log(&format!("[agent_manager] Agent 退出异常: {}", e)),
            }
        } else {
            app_log("[agent_manager] stop(): child 句柄已为空，无需终止");
        }
    }
}

/// 主动终止 Agent 子进程。供 WindowEvent::CloseRequested 调用，
/// 避免 Windows 下用户关窗口后 agent 还活着。
pub fn stop_agent_process() {
    let slot = shared_child_slot();
    let mut guard = match slot.lock() {
        Ok(g) => g,
        Err(e) => {
            app_log(&format!("[agent_manager] stop_agent_process: Mutex 锁失败: {}", e));
            return;
        }
    };
    if let Some(mut child) = guard.take() {
        let pid = child.id();
        app_log(&format!("[agent_manager] stop_agent_process: 终止 Agent (pid={:?})", pid));
        let _ = child.kill();
        match child.wait() {
            Ok(status) => app_log(&format!("[agent_manager] stop_agent_process: Agent 已退出 {:?}", status)),
            Err(e) => app_log(&format!("[agent_manager] stop_agent_process: wait 异常: {}", e)),
        }
    } else {
        app_log("[agent_manager] stop_agent_process: child 句柄已为空");
    }
}

/// 强力杀进程树：Windows 上调 `taskkill /F /T`。
/// 用于 CloseRequested 异步路径——`child.kill()` 在 agent 卡 IO 时可能
/// 不会立刻生效，`taskkill /F /T` 是 OS 层面的 force-kill + 杀整棵进程树。
/// 非 Windows 上退化为 `child.kill()`。
///
/// 修复 #1+#2：不管 Agent 是不是 EAIDE spawn 的、进程名叫什么，
/// 只要端口被占就按 PID 杀（治本）。同时保留旧的 /IM 兜底。
pub fn kill_agent_process_tree() {
    #[cfg(target_os = "windows")]
    {
        // 第一优先级：按复用的外部 PID 杀
        if let Some(pid) = take_reused_pid() {
            app_log(&format!("[agent_manager] kill by reused PID={}", pid));
            let _ = std::process::Command::new("taskkill")
                .args(["/F", "/T", "/PID", &pid.to_string()])
                .output();
        }

        // 第二优先级：按端口查当前占用者并杀（覆盖所有 Agent 名字变体）
        let host = std::env::var("EAIDE_AGENT_HOST").unwrap_or_else(|_| "127.0.0.1".into());
        let port = std::env::var("EAIDE_AGENT_PORT").unwrap_or_else(|_| "8765".into());
        if let Some(pid) = pid_listening_on_port(&host, &port) {
            app_log(&format!(
                "[agent_manager] 端口 {}:{} 仍被 PID={} 占用，强杀",
                host, port, pid
            ));
            let _ = std::process::Command::new("taskkill")
                .args(["/F", "/T", "/PID", &pid.to_string()])
                .output();
        }

        // 第三优先级：兜底——按进程名杀（PyInstaller 标准 EAIDE Agent）
        app_log("[agent_manager] kill_agent_process_tree: 兜底 taskkill /IM eaide-agent.exe");
        let _ = std::process::Command::new("taskkill")
            .args(["/F", "/T", "/IM", "eaide-agent.exe"])
            .output();
    }
    #[cfg(not(target_os = "windows"))]
    {
        stop_agent_process();
        // POSIX：按端口杀（lsof 或 fuser）
        let host = std::env::var("EAIDE_AGENT_HOST").unwrap_or_else(|_| "127.0.0.1".into());
        let port = std::env::var("EAIDE_AGENT_PORT").unwrap_or_else(|_| "8765".into());
        let _ = std::process::Command::new("fuser")
            .args(["-k", &format!("{}/tcp", port)])
            .output();
        let _ = host; // suppress unused
    }
}

/// 取占用 `host:port` 的进程 PID（Windows 用 netstat）。
/// 返回 None 表示端口已空，或 netstat 解析失败。
#[cfg(target_os = "windows")]
pub fn pid_listening_on_port(host: &str, port: &str) -> Option<u32> {
    let out = std::process::Command::new("netstat")
        .args(["-ano", "-p", "TCP"])
        .output()
        .ok()?;
    let text = String::from_utf8_lossy(&out.stdout);
    let want_suffix = format!(":{}", port);
    // 典型行: "  TCP    127.0.0.1:8765    0.0.0.0:0    LISTENING    12345"
    for line in text.lines() {
        if !line.contains("LISTENING") {
            continue;
        }
        if !line.contains(&want_suffix) {
            continue;
        }
        let cols: Vec<&str> = line.split_whitespace().collect();
        if let Some(pid_str) = cols.last() {
            if let Ok(pid) = pid_str.parse::<u32>() {
                if pid > 0 {
                    return Some(pid);
                }
            }
        }
    }
    let _ = host; // suppress unused
    None
}

#[cfg(not(target_os = "windows"))]
pub fn pid_listening_on_port(_host: &str, _port: &str) -> Option<u32> {
    // POSIX 未实现：lsof 需要安装；保守返回 None（让 taskkill 兜底）
    None
}

/// 重启 Agent 子进程。先停掉旧的，从 `llm-config.json` 读出当前 LLM 设置，
/// 转换成 env vars 启动新进程。
///
/// 整个过程持锁——防止并发重启导致孤儿进程。
pub fn restart_agent_process() {
    let slot = shared_child_slot();

    // 全程持锁，防止两个并发重启各自 kill/spawn 导致孤儿进程
    let mut guard = match slot.lock() {
        Ok(g) => g,
        Err(e) => {
            app_log(&format!("[agent_manager] restart: Mutex 锁失败: {}", e));
            return;
        }
    };

    // 1. 停掉旧进程
    if let Some(mut old) = guard.take() {
        let pid = old.id();
        app_log(&format!("[agent_manager] restart: kill 旧 Agent (pid={:?})", pid));
        let _ = old.kill();
        let _ = old.wait();
    } else {
        app_log("[agent_manager] restart: 旧 child 句柄为空，继续 spawn 新进程");
    }

    // 2. 读 LLM 配置，转 env vars
    let env_vars = read_llm_env();
    let redacted = redact_env(&env_vars);
    app_log(&format!(
        "[agent_manager] restart: 注入 env vars: {:?}",
        redacted
    ));

    // 3. 找到 bundled exe
    let exe_dir = std::env::current_exe()
        .ok()
        .and_then(|p| p.parent().map(|d| d.to_path_buf()))
        .unwrap_or_default();
    let candidates = vec![
        exe_dir.join("eaide-agent.exe"),
        exe_dir.join("eaide-agent"),
        exe_dir.join("resources").join("eaide-agent.exe"),
        exe_dir.join("resources").join("eaide-agent"),
    ];
    let Some(exe) = candidates.into_iter().find(|p| p.exists()) else {
        app_log("[agent_manager] restart: 找不到 bundled eaide-agent.exe");
        return;
    };
    app_log(&format!("[agent_manager] restart: spawn 新 Agent: {}", exe.display()));

    let mut cmd = Command::new(&exe);
    cmd.current_dir(&exe_dir)
        .stdout(Stdio::piped())
        .stderr(Stdio::piped());
    #[cfg(target_os = "windows")]
    cmd.creation_flags(0x0800_0000); // CREATE_NO_WINDOW
    for (k, v) in &env_vars {
        cmd.env(k, v);
    }

    match cmd.spawn() {
        Ok(mut child) => {
            let pid = child.id();
            app_log(&format!("[agent_manager] restart: 新 Agent 已 spawn (pid={:?})", pid));
            #[cfg(target_os = "windows")]
            attach_to_job(&child);

            // Spawn stdout/stderr reader threads (same as AgentManager::start)
            let stdout = child.stdout.take();
            let stderr = child.stderr.take();

            if let Some(out) = stdout {
                std::thread::spawn(move || {
                    use std::io::{BufRead, BufReader};
                    let r = BufReader::new(out);
                    for line in r.lines().map_while(Result::ok) {
                        app_log(&format!("[agent:stdout pid={:?}] {}", pid, line));
                    }
                });
            }
            if let Some(err) = stderr {
                std::thread::spawn(move || {
                    use std::io::{BufRead, BufReader};
                    let r = BufReader::new(err);
                    for line in r.lines().map_while(Result::ok) {
                        crash_log(&format!("[agent:stderr pid={:?}] {}", pid, line));
                    }
                });
            }

            // Start health check loop (same as AgentManager::start)
            let host = std::env::var("EAIDE_AGENT_HOST").unwrap_or_else(|_| "127.0.0.1".into());
            let port = std::env::var("EAIDE_AGENT_PORT").unwrap_or_else(|_| "8765".into());
            let health_url = format!("http://{}:{}/health", host, port);
            let _ = tauri::async_runtime::spawn(async move {
                for i in 0..60 {
                    tokio::time::sleep(Duration::from_millis(500)).await;
                    match reqwest::Client::new()
                        .get(&health_url)
                        .timeout(Duration::from_secs(1))
                        .send()
                        .await
                    {
                        Ok(resp) if resp.status().is_success() => {
                            app_log(&format!("[agent_manager] restart 健康检查通过 ({:.1}s)", (i + 1) as f64 * 0.5));
                            return;
                        }
                        Ok(_) | Err(_) => {}
                    }
                }
                app_log("[agent_manager] restart 健康检查超时 (30s)");
            });

            *guard = Some(child);
        }
        Err(e) => {
            app_log(&format!("[agent_manager] restart: spawn 失败: {}", e));
        }
    }
}

/// 从 `%APPDATA%/eaide/llm-config.json` 读出 env vars。
///
/// 返回的 Vec 每个元素是 (KEY, value)：
///   - "mock"  → EAIDE_LLM_BACKEND=mock
///   - "ollama" → EAIDE_LLM_BACKEND=ollama + 不设 EAIDE_PRIVATE_*
///   - "private" → EAIDE_LLM_BACKEND=private + EAIDE_PRIVATE_LLM_BASE_URL/API_KEY/MODEL
///   - "custom" → 类似
/// 读不到文件 → 走默认（mock）。
fn read_llm_env() -> Vec<(String, String)> {
    let path = std::env::var("APPDATA")
        .map(PathBuf::from)
        .unwrap_or_else(|_| PathBuf::from("."))
        .join("eaide")
        .join("llm-config.json");

    let raw = match std::fs::read_to_string(&path) {
        Ok(s) => s,
        Err(e) => {
            app_log(&format!("[agent_manager] read_llm_env: 读取 {} 失败: {} → 用默认 mock", path.display(), e));
            return vec![("EAIDE_LLM_BACKEND".into(), "mock".into())];
        }
    };

    let v: serde_json::Value = match serde_json::from_str(&raw) {
        Ok(v) => v,
        Err(e) => {
            app_log(&format!("[agent_manager] read_llm_env: JSON 解析失败: {} → 用默认 mock", e));
            return vec![("EAIDE_LLM_BACKEND".into(), "mock".into())];
        }
    };

    let active = v.get("active").and_then(|x| x.as_str()).unwrap_or("mock");
    let mut envs: Vec<(String, String)> = vec![("EAIDE_LLM_BACKEND".into(), active.into())];

    match active {
        "ollama" => {
            if let Some(b) = v.get("ollama").and_then(|x| x.get("base_url")).and_then(|x| x.as_str()) {
                envs.push(("EAIDE_OLLAMA_BASE_URL".into(), b.into()));
            }
            if let Some(m) = v.get("ollama").and_then(|x| x.get("model")).and_then(|x| x.as_str()) {
                envs.push(("EAIDE_OLLAMA_MODEL".into(), m.into()));
            }
        }
        "private" => {
            if let Some(b) = v.get("private").and_then(|x| x.get("base_url")).and_then(|x| x.as_str()) {
                envs.push(("EAIDE_PRIVATE_LLM_BASE_URL".into(), b.into()));
            }
            if let Some(k) = v.get("private").and_then(|x| x.get("api_key")).and_then(|x| x.as_str()) {
                envs.push(("EAIDE_PRIVATE_LLM_API_KEY".into(), k.into()));
            }
            if let Some(m) = v.get("private").and_then(|x| x.get("model")).and_then(|x| x.as_str()) {
                envs.push(("EAIDE_PRIVATE_LLM_MODEL".into(), m.into()));
            }
        }
        "custom" => {
            if let Some(b) = v.get("custom").and_then(|x| x.get("base_url")).and_then(|x| x.as_str()) {
                envs.push(("EAIDE_PRIVATE_LLM_BASE_URL".into(), b.into()));
            }
            if let Some(k) = v.get("custom").and_then(|x| x.get("api_key")).and_then(|x| x.as_str()) {
                envs.push(("EAIDE_PRIVATE_LLM_API_KEY".into(), k.into()));
            }
            if let Some(m) = v.get("custom").and_then(|x| x.get("model")).and_then(|x| x.as_str()) {
                envs.push(("EAIDE_PRIVATE_LLM_MODEL".into(), m.into()));
            }
        }
        _ => {} // mock — no extra envs
    }

    envs
}

impl Drop for AgentManager {
    fn drop(&mut self) {
        app_log("[agent_manager] Drop —— AppState / AgentManager 即将释放，调 stop_agent_process()");
        // 注意：drop 时不能再 borrow self.child 持锁返回 mgr 路径，
        // 改用 free function 直接清 SHARED_CHILD。
        stop_agent_process();
    }
}

// ---- 辅助函数 ----------------------------------------------------------------

fn is_port_open(host: &str, port: &str) -> bool {
    is_port_open_pub(host, port)
}

/// 公开别名 —— commands/router.rs 需要从外面调（agent_restart_now 等）
pub fn is_port_open_pub(host: &str, port: &str) -> bool {
    std::net::TcpStream::connect_timeout(
        &format!("{}:{}", host, port).parse().unwrap(),
        Duration::from_secs(2),
    )
    .is_ok()
}

/// 查找 Agent 项目目录（开发环境自动发现，生产环境需手动配置）。
fn find_project_dir() -> PathBuf {
    // 1. 环境变量（最优先）
    if let Ok(dir) = std::env::var("EAIDE_AGENT_PROJECT_DIR") {
        let p = PathBuf::from(&dir);
        if p.join("pyproject.toml").exists() {
            return p;
        }
        app_log(&format!("EAIDE_AGENT_PROJECT_DIR={} 但未找到 pyproject.toml", dir));
    }

    // 2. 从 exe 路径向上查找（开发环境：target/release → src-tauri → apps/desktop → 项目根）
    if let Ok(exe) = std::env::current_exe() {
        for ancestor in exe.ancestors().skip(1).take(10) {
            let candidate = ancestor.join("services").join("agent");
            if candidate.join("pyproject.toml").exists() {
                return candidate;
            }
        }
    }

    // 3. 硬编码常见开发路径
    let hardcoded = [
        r"D:\ditPref\services\agent",
        r"C:\ditPref\services\agent",
    ];
    for path in &hardcoded {
        let p = PathBuf::from(path);
        if p.join("pyproject.toml").exists() {
            return p;
        }
    }

    // 4. fallback（几乎肯定不存在，但给用户明确的提示）
    PathBuf::from(r"D:\ditPref\services\agent")
}

/// 将日志写入应用数据目录下的文件（GUI 应用无控制台，必须写文件才能看到日志）。
pub(crate) fn app_log(msg: &str) {
    write_log("eaide.log", msg);
    // tracing-subscriber 已不在此 init（避免和 tauri_plugin_log 冲突）；
    // 上面 write_log 已经把消息落盘，这里不再二次发送
    let _ = msg;
}

/// 把 panic / 致命错误写到独立的 crash.log（与 eaide.log 分开，便于排查）。
pub(crate) fn crash_log(msg: &str) {
    write_log("crash.log", msg);
    // 同时写 stderr（如果用户从 cmd 启动 exe，能直接看到）
    eprintln!("{}", msg);
}

/// 内部统一日志写入：所有日志都进 `logs/` 子目录。
fn write_log(file_name: &str, msg: &str) {
    let log_path = get_log_dir().join(file_name);
    if let Some(parent) = log_path.parent() {
        let _ = std::fs::create_dir_all(parent);
    }
    if let Ok(mut f) = OpenOptions::new().create(true).append(true).open(&log_path) {
        let ts = chrono::Local::now().format("%Y-%m-%d %H:%M:%S%.3f");
        let _ = writeln!(f, "[{}] {}", ts, msg);
    }
}

pub(crate) fn get_app_data_dir() -> PathBuf {
    #[cfg(target_os = "windows")]
    let dir = {
        std::env::var("LOCALAPPDATA")
            .map(PathBuf::from)
            .unwrap_or_else(|_| PathBuf::from("."))
            .join("Enterprise AI IDE")
    };
    #[cfg(not(target_os = "windows"))]
    let dir = {
        std::env::var("HOME")
            .map(|d| PathBuf::from(d).join(".local").join("share").join("eaide"))
            .unwrap_or_else(|_| PathBuf::from("."))
    };
    // 容错：首次启动 / 父目录未建也能写
    let _ = std::fs::create_dir_all(&dir);
    dir
}

/// 日志子目录：所有运行时日志都进这里，方便集中排查。
pub(crate) fn get_log_dir() -> PathBuf {
    let dir = get_app_data_dir().join("logs");
    let _ = std::fs::create_dir_all(&dir);
    dir
}
