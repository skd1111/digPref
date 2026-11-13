//! Terminal shell/spawn 命令（2026-09-11）。
//!
//! 通过 `tokio::process::Command` 拉起用户选择的 shell（pwsh / cmd.exe / …），
//! stdin/stdout/stderr 全部 pipe，后台 tokio task 持续读 stdout/stderr 并以
//! Tauri event 推到前端 xterm.js。stdin 写入走 mpsc channel → 后台 task 转发。
//!
//! 会话生命周期：
//!   1. 前端调 `terminal_spawn` → Rust 返回 `terminal_id`
//!   2. 前端每次按键 → `terminal_write` → 写入 shell stdin
//!   3. Rust 后台读 stdout → emit `terminal://stdout` → 前端 xterm.write()
//!   4. 前端卸载 / 用户关闭 → `terminal_kill` → 杀进程 + 清理 map
//!
//! 注意：当前用 pipe 模式（非 ConPTY），交互式提示符可能不显示；
//! 后续可升级 ConPTY 获得完整终端体验。

use std::collections::HashMap;
use std::sync::Mutex;

use base64::{engine::general_purpose::STANDARD as B64, Engine};
use tauri::{AppHandle, Emitter};
use tokio::io::{AsyncReadExt, AsyncWriteExt};
use tokio::process::{Child, Command as TokioCommand};
use tokio::sync::mpsc;
use uuid::Uuid;

// ── 进程检查辅助 ──────────────────────────────────────────────────────────

/// 检查指定 PID 的进程是否仍在运行。
fn is_process_alive(pid: u32) -> bool {
    #[cfg(target_os = "windows")]
    {
        use std::os::windows::process::CommandExt;
        match std::process::Command::new("tasklist")
            .args(["/FI", &format!("PID eq {}", pid), "/NH"])
            .creation_flags(0x0800_0000) // CREATE_NO_WINDOW
            .output()
        {
            Ok(out) => String::from_utf8_lossy(&out.stdout)
                .lines()
                .any(|line| line.contains(&pid.to_string())),
            Err(_) => false,
        }
    }
    #[cfg(not(target_os = "windows"))]
    {
        match std::process::Command::new("kill")
            .arg("-0")
            .arg(pid.to_string())
            .output()
        {
            Ok(out) => out.status.success(),
            Err(_) => false,
        }
    }
}

// ── 全局会话表 ──────────────────────────────────────────────────────────────

struct TerminalSession {
    child: Child,
    /// 发往 stdin-writer task 的 sender；写入 = 用户按键。
    stdin_tx: mpsc::Sender<Vec<u8>>,
}

static TERMINALS: Mutex<Option<HashMap<String, TerminalSession>>> = Mutex::new(None);

fn terminals() -> std::sync::MutexGuard<'static, Option<HashMap<String, TerminalSession>>> {
    TERMINALS.lock().unwrap_or_else(|e| e.into_inner())
}

fn ensure_map() {
    let mut g = terminals();
    if g.is_none() {
        *g = Some(HashMap::new());
    }
}

// ── Shell 探测 ──────────────────────────────────────────────────────────────

/// 返回 (可执行文件, 启动参数)。
/// `shell` 为空时自动探测：Windows 上优先 pwsh，回退 cmd。
fn resolve_shell(shell: &str) -> (String, Vec<String>) {
    let s = shell.trim();

    // 用户显式指定了绝对路径
    if s.contains('\\') || s.contains('/') {
        return (s.to_string(), vec![]);
    }

    match s {
        "" | "cmd.exe" | "cmd" if cfg!(windows) => {
            // 优先探测 pwsh
            if which_pwsh().is_some() {
                let pwsh = which_pwsh().unwrap();
                (pwsh, vec!["-NoExit".into(), "-NoProfile".into()])
            } else {
                ("cmd.exe".into(), vec![])
            }
        }
        "pwsh" | "powershell" | "powershell.exe" => {
            if let Some(p) = which_pwsh() {
                (p, vec!["-NoExit".into(), "-NoProfile".into()])
            } else {
                // pwsh 未安装，回退到 Windows PowerShell
                (
                    "powershell.exe".into(),
                    vec!["-NoExit".into(), "-NoProfile".into()],
                )
            }
        }
        "wsl" | "bash" | "sh" => (s.to_string(), vec![]),
        other => (other.to_string(), vec![]),
    }
}

fn which_pwsh() -> Option<String> {
    // 优先 PATH 中的 pwsh
    if let Ok(p) = std::process::Command::new("where")
        .args(["pwsh"])
        .output()
    {
        if p.status.success() {
            let first_line = String::from_utf8_lossy(&p.stdout)
                .lines()
                .next()?
                .trim()
                .to_string();
            if !first_line.is_empty() {
                return Some(first_line);
            }
        }
    }
    // 常见安装路径兜底
    let candidates = [
        "C:\\Program Files\\PowerShell\\7\\pwsh.exe",
        "C:\\Program Files (x86)\\PowerShell\\7\\pwsh.exe",
    ];
    candidates
        .iter()
        .find(|p| std::path::Path::new(p).exists())
        .map(|p| p.to_string())
}

// ─ Tauri Commands ──────────────────────────────────────────────────────────

/// 启动 shell 进程，返回 terminal_id。
///
/// `shell` 参数：`""` / `"cmd.exe"` / `"pwsh"` / `"powershell"` / `"wsl"` / 绝对路径。
/// `cwd` 可选，默认当前用户目录。
#[tauri::command]
pub async fn terminal_spawn(
    app: AppHandle,
    shell: String,
    cwd: Option<String>,
) -> Result<
    (
        String,
        String,
        Vec<String>,
    ),
    String,
> {
    ensure_map();

    let (exe, args) = resolve_shell(&shell);
    let term_id = Uuid::new_v4().to_string();

    let work_dir = cwd.unwrap_or_else(|| {
        std::env::var("USERPROFILE")
            .or_else(|_| std::env::var("HOME"))
            .unwrap_or_else(|_| ".".into())
    });

    let mut cmd = TokioCommand::new(&exe);
    cmd.args(&args)
        .current_dir(&work_dir)
        .stdin(std::process::Stdio::piped())
        .stdout(std::process::Stdio::piped())
        .stderr(std::process::Stdio::piped());

    // Windows: 隐藏子进程控制台窗口
    #[cfg(target_os = "windows")]
    {
        cmd.creation_flags(0x0800_0000); // CREATE_NO_WINDOW
    }

    let mut child = cmd.spawn().map_err(|e| format!("spawn 失败: {e} (exe={exe})"))?;

    let stdin = child
        .stdin
        .take()
        .ok_or_else(|| "stdin pipe 获取失败".to_string())?;
    let stdout = child
        .stdout
        .take()
        .ok_or_else(|| "stdout pipe 获取失败".to_string())?;
    let stderr = child
        .stderr
        .take()
        .ok_or_else(|| "stderr pipe 获取失败".to_string())?;

    // stdin 写入 channel：前端 terminal_write → tx → 后台 task → child.stdin
    let (stdin_tx, mut stdin_rx) = mpsc::channel::<Vec<u8>>(256);

    // 后台 stdin writer
    let mut stdin_handle = stdin;
    let stdin_id = term_id.clone();
    let app_clone = app.clone();
    tokio::spawn(async move {
        while let Some(data) = stdin_rx.recv().await {
            if stdin_handle.write_all(&data).await.is_err() {
                break;
            }
            if stdin_handle.flush().await.is_err() {
                break;
            }
        }
        // channel 关闭或写入失败 → 通知前端
        let _ = app_clone.emit(
            "terminal://exit",
            serde_json::json!({ "terminal_id": stdin_id, "exit_code": null }),
        );
    });

    // 后台 stdout reader
    let stdout_id = term_id.clone();
    let app_stdout = app.clone();
    tokio::spawn(async move {
        let mut buf = vec![0u8; 4096];
        let mut reader = stdout;
        loop {
            match reader.read(&mut buf).await {
                Ok(0) => break, // EOF
                Ok(n) => {
                    let b64 = B64.encode(&buf[..n]);
                    let _ = app_stdout.emit(
                        "terminal://stdout",
                        serde_json::json!({ "terminal_id": stdout_id, "data": b64 }),
                    );
                }
                Err(_) => break,
            }
        }
    });

    // 后台 stderr reader
    let stderr_id = term_id.clone();
    let app_stderr = app.clone();
    tokio::spawn(async move {
        let mut buf = vec![0u8; 4096];
        let mut reader = stderr;
        loop {
            match reader.read(&mut buf).await {
                Ok(0) => break,
                Ok(n) => {
                    let b64 = B64.encode(&buf[..n]);
                    let _ = app_stderr.emit(
                        "terminal://stderr",
                        serde_json::json!({ "terminal_id": stderr_id, "data": b64 }),
                    );
                }
                Err(_) => break,
            }
        }
    });

    // 后台 wait + 退出通知（不持有 Child，通过 PID 检查）
    let wait_id = term_id.clone();
    let app_wait = app.clone();
    let child_pid = child.id().unwrap_or(0);
    tokio::spawn(async move {
        // 等待进程退出：每 500ms 检查一次
        loop {
            tokio::time::sleep(std::time::Duration::from_millis(500)).await;
            // 通过 PID 检查进程是否还在运行
            let alive = is_process_alive(child_pid);
            if !alive {
                break;
            }
        }
        let _ = app_wait.emit(
            "terminal://exit",
            serde_json::json!({ "terminal_id": wait_id, "exit_code": null }),
        );
        // 清理 map
        if let Ok(mut g) = TERMINALS.lock() {
            if let Some(map) = g.as_mut() {
                map.remove(&wait_id);
            }
        }
    });

    // 存入全局 map
    {
        let mut g = terminals();
        if let Some(map) = g.as_mut() {
            map.insert(
                term_id.clone(),
                TerminalSession {
                    child,
                    stdin_tx,
                },
            );
        }
    }

    Ok((term_id, exe, args))
}

/// 向 shell stdin 写入数据（用户按键 / 粘贴）。
#[tauri::command]
pub async fn terminal_write(terminal_id: String, data: String) -> Result<bool, String> {
    let tx = {
        let g = terminals();
        let map = g.as_ref().ok_or("终端未初始化")?;
        let session = map.get(&terminal_id).ok_or("终端不存在或已关闭")?;
        session.stdin_tx.clone()
    }; // MutexGuard 在此释放

    tx.send(data.into_bytes())
        .await
        .map_err(|_| "stdin channel 已关闭".to_string())?;
    Ok(true)
}

/// 向 shell 发送特殊键（Enter / Tab / Ctrl+C 等）。
/// `key` 支持: "Enter", "Tab", "Ctrl+C", "Ctrl+D", "ArrowUp", "ArrowDown",
///             "ArrowLeft", "ArrowRight", "Backspace", "Delete"
#[tauri::command]
pub async fn terminal_write_key(terminal_id: String, key: String) -> Result<bool, String> {
    let tx = {
        let g = terminals();
        let map = g.as_ref().ok_or("终端未初始化")?;
        let session = map.get(&terminal_id).ok_or("终端不存在或已关闭")?;
        session.stdin_tx.clone()
    }; // MutexGuard 在此释放

    let bytes: Vec<u8> = match key.as_str() {
        "Enter" => b"\r".to_vec(),
        "Tab" => b"\t".to_vec(),
        "Ctrl+C" => vec![0x03],
        "Ctrl+D" => vec![0x04],
        "Backspace" => vec![0x7f],
        "Delete" => b"\x1b[3~".to_vec(),
        "ArrowUp" => b"\x1b[A".to_vec(),
        "ArrowDown" => b"\x1b[B".to_vec(),
        "ArrowRight" => b"\x1b[C".to_vec(),
        "ArrowLeft" => b"\x1b[D".to_vec(),
        "Home" => b"\x1b[H".to_vec(),
        "End" => b"\x1b[F".to_vec(),
        "PageUp" => b"\x1b[5~".to_vec(),
        "PageDown" => b"\x1b[6~".to_vec(),
        other => return Err(format!("未知特殊键: {other}")),
    };

    tx.send(bytes)
        .await
        .map_err(|_| "stdin channel 已关闭".to_string())?;
    Ok(true)
}

/// 终止 shell 进程并清理会话。
#[tauri::command]
pub async fn terminal_kill(terminal_id: String) -> Result<bool, String> {
    let session = {
        let mut g = terminals();
        let map = g.as_mut().ok_or("终端未初始化")?;
        map.remove(&terminal_id)
    }; // MutexGuard 在此释放

    if let Some(mut session) = session {
        // 先关 stdin channel（stdin writer task 会自动退出）
        drop(session.stdin_tx);
        // 杀进程
        let _ = session.child.kill().await;
        let _ = session.child.wait().await;
        Ok(true)
    } else {
        Ok(false)
    }
}

/// 查询终端是否存活。
#[tauri::command]
pub async fn terminal_alive(terminal_id: String) -> bool {
    let g = terminals();
    g.as_ref()
        .is_some_and(|map| map.contains_key(&terminal_id))
}
