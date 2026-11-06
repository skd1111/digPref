//! Phase 2C V0 LLM 路由 4 个 Tauri command —— 包装 HTTP 调用 FastAPI。

use tauri::State;

use crate::state::AppState;

type CmdResult<T> = Result<T, String>;

fn err(e: impl std::fmt::Display) -> String {
    format!("router command failed: {}", e)
}

/// 将 HTTP 响应转为 JSON；非 2xx 时提取 detail 返回 Err，
/// 避免 FastAPI 错误体（如 {"detail":"..."}）被当作正常数据传给前端。
async fn json_or_err(resp: reqwest::Response) -> CmdResult<serde_json::Value> {
    let status = resp.status();
    let json: serde_json::Value = resp.json().await.map_err(err)?;
    if !status.is_success() {
        let detail = json
            .get("detail")
            .and_then(|d| d.as_str())
            .unwrap_or("unknown error");
        return Err(format!("agent returned {}: {}", status.as_u16(), detail));
    }
    Ok(json)
}

fn agent_url(_state: &AppState, path: &str) -> String {
    let port = std::env::var("EAIDE_AGENT_PORT").unwrap_or_else(|_| "8765".into());
    format!("http://127.0.0.1:{}{}", port, path)
}

/// 等待 Agent 就绪（GET /health 返回 2xx 才返回）。
///
/// 超时（默认 30s）后返回 `ready=false` + 错误信息，前端展示遮罩 + 重试按钮。
/// 这条命令阻塞到就绪或超时——解决 EAIDE 启动比 Agent 快的竞态。
///
/// 探测顺序是「先探测再 sleep」：Agent 已在跑时（复用上一个进程 / 前端启动
/// 闸门二次调用）立刻返回，不白等 500ms —— 否则每次开窗都会看到半秒的模糊遮罩。
#[tauri::command]
pub async fn agent_wait_ready(
    timeout_s: Option<f64>,
    state: State<'_, AppState>,
) -> CmdResult<serde_json::Value> {
    let timeout = timeout_s.unwrap_or(30.0);
    let health_url = agent_url(&state, "/health");
    let client = reqwest::Client::new();
    let started = std::time::Instant::now();
    let deadline = std::time::Duration::from_secs_f64(timeout);
    // loop 保证至少跑一轮，且除「成功直接 return」外每轮都写入 last_err，
    // 所以不给初值（给了会触发 clippy unused_assignments）。
    let mut last_err: Option<String>;
    loop {
        match client
            .get(&health_url)
            .timeout(std::time::Duration::from_secs(1))
            .send()
            .await
        {
            Ok(resp) if resp.status().is_success() => {
                return Ok(serde_json::json!({
                    "ready": true,
                    "elapsed_ms": started.elapsed().as_millis() as u64,
                    "health_url": health_url,
                }));
            }
            Ok(resp) => {
                last_err = Some(format!("HTTP {}", resp.status()));
            }
            Err(e) => {
                last_err = Some(format!("{}: {}", e, e));
            }
        }
        if started.elapsed() >= deadline {
            break;
        }
        tokio::time::sleep(std::time::Duration::from_millis(500)).await;
    }
    Ok(serde_json::json!({
        "ready": false,
        "elapsed_ms": started.elapsed().as_millis() as u64,
        "health_url": health_url,
        "error": last_err.unwrap_or_else(|| "timeout".into()),
    }))
}

/// Agent 版本指纹（GET /version）。
/// 用于诊断「Agent 是不是老版」「什么时候起的」。
/// 失败（404 = 老 Agent 没这个路由，connection = 没起来）也返回 ok=false。
#[tauri::command]
pub async fn agent_get_version(state: State<'_, AppState>) -> CmdResult<serde_json::Value> {
    let url = agent_url(&state, "/version");
    match reqwest::get(&url).await {
        Ok(resp) => {
            let status = resp.status();
            let json: serde_json::Value = match resp.json().await {
                Ok(j) => j,
                Err(e) => {
                    return Ok(serde_json::json!({
                        "ok": false,
                        "status": status.as_u16(),
                        "error": format!("json parse: {}", e),
                    }));
                }
            };
            Ok(serde_json::json!({
                "ok": status.is_success(),
                "status": status.as_u16(),
                "version": json,
            }))
        }
        Err(e) => Ok(serde_json::json!({
            "ok": false,
            "error": format!("{}: {}", e, e),
        })),
    }
}

/// 手动重启 Agent：**杀掉旧进程并重新 spawn**，等 /health 就绪后返回。
///
/// 历史 Bug（2026-11-06 复现）：旧实现**只 kill 不 spawn**，依赖「下次请求会
/// 触发 spawn」——但请求侧并没有懒启动逻辑，于是点完按钮 Agent 永远不再起来，
/// 界面一直「未连接」，日志停在 `kill_agent_process_tree: 兜底 taskkill` 之后。
/// 现改为真重启：kill 端口占用者 → spawn 新进程 → 等端口 → 等 /health 就绪。
///
/// `agent_restart_now`（前端按钮）与 `agent_restart`（保存 LLM 配置后内部调用）
/// 共用本函数，避免两处实现漂移。
pub async fn restart_agent_and_wait(state: State<'_, AppState>) -> CmdResult<serde_json::Value> {
    // 1. 清掉 :8765 占用者（含非本进程 spawn 的外部 Agent）
    crate::agent_manager::kill_agent_process_tree();
    let host = std::env::var("EAIDE_AGENT_HOST").unwrap_or_else(|_| "127.0.0.1".into());
    let port = std::env::var("EAIDE_AGENT_PORT").unwrap_or_else(|_| "8765".into());
    for _ in 0..30 {
        if !crate::agent_manager::is_port_open_pub(&host, &port) {
            break;
        }
        tokio::time::sleep(std::time::Duration::from_millis(100)).await;
    }

    // 2. 真重启：spawn 新 Agent 子进程（内部含后台 30s 健康检查）
    //    放到 blocking 线程，避免同步 spawn 阻塞 async runtime worker
    let _spawned = tauri::async_runtime::spawn_blocking(crate::agent_manager::restart_agent_process)
        .await
        .map_err(err)?;

    // 3. 等端口重新监听（最多 30s）
    let mut port_up = false;
    for _ in 0..300 {
        if crate::agent_manager::is_port_open_pub(&host, &port) {
            port_up = true;
            break;
        }
        tokio::time::sleep(std::time::Duration::from_millis(100)).await;
    }

    // 4. 再等 /health 2xx（最多 30s）：端口开放 != 应用 startup 完成
    let health = agent_wait_ready(Some(30.0), state).await?;
    let ready = health
        .get("ready")
        .and_then(|v| v.as_bool())
        .unwrap_or(false);
    Ok(serde_json::json!({
        "ok": ready,
        "restarted": true,
        "port_freed": true,
        "port_up": port_up,
        "ready": ready,
        "health": health,
    }))
}

/// 手动重启 Agent（前端「重启 Agent」按钮入口）。
#[tauri::command]
pub async fn agent_restart_now(state: State<'_, AppState>) -> CmdResult<serde_json::Value> {
    restart_agent_and_wait(state).await
}

/// 读取 EAIDE 主日志末尾 N 行（默认 60）—— 诊断 Agent 启动失败。
#[tauri::command]
pub async fn agent_read_log(lines: Option<usize>) -> CmdResult<serde_json::Value> {
    use std::io::{Read, Seek, SeekFrom};
    let n = lines.unwrap_or(60);
    let log_path = crate::agent_manager::get_log_dir().join("eaide.log");
    let path_str = log_path.to_string_lossy().into_owned();

    // 简化：直接读整个文件，取最后 n 行
    let mut s = String::new();
    let bytes_read = match std::fs::File::open(&log_path) {
        Ok(mut f) => {
            // 限制最多读 200KB（旧日志不必要）
            let _ = f.seek(SeekFrom::End(0));
            let len = f.metadata().map(|m| m.len()).unwrap_or(0);
            let start = len.saturating_sub(200_000);
            let _ = f.seek(SeekFrom::Start(start));
            let _ = f.read_to_string(&mut s);
            true
        }
        Err(_) => false,
    };

    let all: Vec<&str> = s.lines().collect();
    let last: Vec<&str> = if all.len() > n {
        all[all.len() - n..].to_vec()
    } else {
        all
    };

    Ok(serde_json::json!({
        "ok": bytes_read,
        "path": path_str,
        "tail": last.join("\n"),
        "line_count": last.len(),
        "hint": "若 tail 含 '[agent_manager] 未解析到 Agent' 则出厂 one-dir 布局缺失（安装根需有 eaide-agent.exe + 同级 _internal；打包期为 src-tauri/resources 经 'resources/*' 平铺），EAIDE 退到了 dev 模式；versions 根与当前激活指针见 app_update_status 命令输出",
    }))
}

#[tauri::command]
pub async fn router_get_metrics(state: State<'_, AppState>) -> CmdResult<serde_json::Value> {
    let url = agent_url(&state, "/router/metrics");
    let resp = reqwest::get(&url).await.map_err(err)?;
    json_or_err(resp).await
}

#[tauri::command]
pub async fn router_get_decisions(
    limit: Option<usize>,
    state: State<'_, AppState>,
) -> CmdResult<serde_json::Value> {
    let l = limit.unwrap_or(100);
    let url = format!("{}/decisions?limit={}", agent_url(&state, "/router"), l);
    let resp = reqwest::get(&url).await.map_err(err)?;
    json_or_err(resp).await
}

#[tauri::command]
pub async fn router_get_weights(state: State<'_, AppState>) -> CmdResult<serde_json::Value> {
    let url = agent_url(&state, "/router/weights");
    let resp = reqwest::get(&url).await.map_err(err)?;
    json_or_err(resp).await
}

#[tauri::command]
pub async fn router_set_weights(
    weights: serde_json::Value,
    state: State<'_, AppState>,
) -> CmdResult<serde_json::Value> {
    let url = agent_url(&state, "/router/weights");
    let client = reqwest::Client::new();
    let resp = client.put(&url).json(&weights).send().await.map_err(err)?;
    json_or_err(resp).await
}

#[tauri::command]
pub async fn router_reset_breaker(
    backend_name: String,
    state: State<'_, AppState>,
) -> CmdResult<serde_json::Value> {
    let url = format!("{}/breakers/{}/reset", agent_url(&state, "/router"), backend_name);
    let client = reqwest::Client::new();
    let resp = client.post(&url).send().await.map_err(err)?;
    json_or_err(resp).await
}

#[tauri::command]
pub async fn router_list_backends(state: State<'_, AppState>) -> CmdResult<serde_json::Value> {
    let url = agent_url(&state, "/router/backends");
    let resp = reqwest::get(&url).await.map_err(err)?;
    json_or_err(resp).await
}

/// 模型管理保存后热重载 LMRouter（端侧 base_url/model/max_context 无需重启生效）
#[tauri::command]
pub async fn router_reload_context(state: State<'_, AppState>) -> CmdResult<serde_json::Value> {
    let url = agent_url(&state, "/router/reload-context");
    let client = reqwest::Client::new();
    let resp = client
        .post(&url)
        .timeout(std::time::Duration::from_secs(10))
        .send()
        .await
        .map_err(err)?;
    json_or_err(resp).await
}

#[tauri::command]
pub async fn router_upsert_backend(
    backend: serde_json::Value,
    state: State<'_, AppState>,
) -> CmdResult<serde_json::Value> {
    // POST /router/backends（新建）；name 已存在时切到 PUT /router/backends/{name}
    // 让前端用一个 IPC 入口，无需关心 exists/不存在
    let name = backend
        .get("name")
        .and_then(|v| v.as_str())
        .ok_or_else(|| "backend.name required".to_string())?;

    // 先 list 看看是否已存在
    let list_url = agent_url(&state, "/router/backends");
    let list_resp = reqwest::get(&list_url).await.map_err(err)?;
    let list_json: serde_json::Value = list_resp.json().await.map_err(err)?;
    let exists = list_json
        .get("backends")
        .and_then(|b| b.as_array())
        .map(|arr| arr.iter().any(|b| b.get("name").and_then(|n| n.as_str()) == Some(name)))
        .unwrap_or(false);

    let client = reqwest::Client::new();
    let url = if exists {
        format!("{}/backends/{}", agent_url(&state, "/router"), name)
    } else {
        format!("{}/backends", agent_url(&state, "/router"))
    };
    let method = if exists { reqwest::Method::PUT } else { reqwest::Method::POST };
    let resp = client
        .request(method, &url)
        .timeout(std::time::Duration::from_secs(10))
        .json(&backend)
        .send()
        .await
        .map_err(err)?;
    json_or_err(resp).await
}

#[tauri::command]
pub async fn router_delete_backend(
    name: String,
    state: State<'_, AppState>,
) -> CmdResult<serde_json::Value> {
    let url = format!("{}/backends/{}", agent_url(&state, "/router"), name);
    let client = reqwest::Client::new();
    let resp = client
        .delete(&url)
        .timeout(std::time::Duration::from_secs(10))
        .send()
        .await
        .map_err(err)?;
    json_or_err(resp).await
}

#[tauri::command]
pub async fn router_test_connection(
    payload: serde_json::Value,
    state: State<'_, AppState>,
) -> CmdResult<serde_json::Value> {
    // 真实探测后端连通性（POST /router/backends/test-connection）。
    // payload 字段：type / base_url / model / api_key / timeout_s
    let url = format!("{}/backends/test-connection", agent_url(&state, "/router"));
    let client = reqwest::Client::new();
    let resp = client
        .post(&url)
        .timeout(std::time::Duration::from_secs(15))
        .json(&payload)
        .send()
        .await
        .map_err(err)?;
    let status = resp.status();
    let json: serde_json::Value = resp.json().await.map_err(err)?;
    // FastAPI 异常 → 4xx；这里统一包装成 {ok: false, error: ...}
    if !status.is_success() {
        let detail = json.get("detail").and_then(|v| v.as_str()).unwrap_or("(no detail)");
        return Ok(serde_json::json!({
            "ok": false,
            "error": format!("HTTP {} · {}", status.as_u16(), detail),
        }));
    }
    Ok(json)
}

/// Phase 2C V2.0：Spark 模式 toggle（前端 RouterDashboard 直连）。
/// POST /router/spark-mode → {enabled: bool} → 后端 LMRouter.set_spark_mode()。
#[tauri::command]
pub async fn router_set_spark_mode(
    enabled: bool,
    state: State<'_, AppState>,
) -> CmdResult<serde_json::Value> {
    let url = format!("{}/spark-mode", agent_url(&state, "/router"));
    let client = reqwest::Client::new();
    let resp = client
        .post(&url)
        .timeout(std::time::Duration::from_secs(5))
        .json(&serde_json::json!({ "enabled": enabled }))
        .send()
        .await
        .map_err(err)?;
    json_or_err(resp).await
}

/// 生成限制（两级回退）：读全局默认（最大输出长度 / 默认上下文长度）。
/// GET /router/gen-limits → { ok, limits: { max_output_tokens, default_context_window } }
#[tauri::command]
pub async fn router_get_gen_limits(state: State<'_, AppState>) -> CmdResult<serde_json::Value> {
    let url = agent_url(&state, "/router/gen-limits");
    let resp = reqwest::get(&url).await.map_err(err)?;
    json_or_err(resp).await
}

/// 生成限制（两级回退）：写全局默认 + 后端热生效。
/// PUT /router/gen-limits，limits 为稀疏 patch（只传要改的字段）。
#[tauri::command]
pub async fn router_set_gen_limits(
    limits: serde_json::Value,
    state: State<'_, AppState>,
) -> CmdResult<serde_json::Value> {
    let url = agent_url(&state, "/router/gen-limits");
    let client = reqwest::Client::new();
    let resp = client
        .put(&url)
        .timeout(std::time::Duration::from_secs(10))
        .json(&limits)
        .send()
        .await
        .map_err(err)?;
    json_or_err(resp).await
}
