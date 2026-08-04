//! Tauri runtime bootstrap.
//! Wires together: state, plugins, commands, event router.
//!
//! 关键防御：release 包没有控制台，所有 panic / stderr 都丢失。
//! 所以 `run()` 第一件事就是装 panic hook，把所有 panic
//! 写到 `%LOCALAPPDATA%\Enterprise AI IDE\crash.log`。
mod commands;
mod credentials;
mod stream;
mod audit;
mod mcp_client;
mod agent_manager;
mod error;
mod state;
mod config;
// Phase 2F+ MVP (Task 1: storage + offset codec only)
mod logviewer;
// Phase 1B V1 (2026-07-30): 原生工具层 Rust 占位
// V1 仅声明 mod + 9 占位工具名（dispatcher 在 Python 侧识别 RUST_TOOL_NAMES → 返 not_implemented）
// V1.5 接力真实 Rust 实现 + Tauri Command 注册
mod builtin;
// Phase 15 V0 (2026-08-03): 前端实时预览引擎 —— 预览窗口管理
mod preview;

use agent_manager::{app_log, crash_log, get_app_data_dir, get_log_dir};
use state::AppState;
use tauri::{Emitter, Manager};


/// 全局 panic hook —— 任何线程的 panic 都会触发。
/// 用 `panic = "unwind"` profile 才有效（Cargo.toml 已设置）。
fn install_panic_hook() {
    let prev = std::panic::take_hook();
    std::panic::set_hook(Box::new(move |info| {
        // 必须先强制抓 backtrace —— 在 hook 里
        let bt = std::backtrace::Backtrace::force_capture();
        let payload = if let Some(s) = info.payload().downcast_ref::<&str>() {
            (*s).to_string()
        } else if let Some(s) = info.payload().downcast_ref::<String>() {
            s.clone()
        } else {
            "<non-string panic payload>".to_string()
        };
        let location = info
            .location()
            .map(|l| format!("{}:{}:{}", l.file(), l.line(), l.column()))
            .unwrap_or_else(|| "<unknown>".into());

        let msg = format!(
            "==== EAIDE PANIC ====\nlocation: {}\npayload: {}\nbacktrace:\n{}\n==== END PANIC ====\n",
            location, payload, bt
        );

        // 已知在 panic 中，IO 也可能失败 —— 全程 best-effort
        crash_log(&msg);
        // 同步到 tracing（如果有人从 cmd 跑，stderr 仍可见）
        eprintln!("{}", msg);

        // 保留原 hook（tracing-subscriber / 其他）
        prev(info);
    }));
}


pub fn run() {
    // 1. 确保数据目录存在
    let _ = std::fs::create_dir_all(get_app_data_dir());

    // 2. 强制 backtrace（NSIS 启动的 GUI 进程不会继承 RUST_BACKTRACE）
    std::env::set_var("RUST_BACKTRACE", "1");
    if std::env::var("RUST_BACKTRACE_FULL").is_err() {
        std::env::set_var("RUST_BACKTRACE_FULL", "1");
    }

    // 3. panic hook 必须在任何可能 panic 的代码之前
    install_panic_hook();

    // 4. 注意：不能同时 init tracing_subscriber 和用 tauri_plugin_log —— 后者会
    //    触发 "attempted to set a logger after the logging system was already initialized"。
    //    我们自己写的 app_log() 直接走文件 IO（logs/eaide.log），不需要 tracing subscriber。
    //    tauri_plugin_log 是唯一全局 logger（写到 stderr + 默认 console 通道）。

    app_log(&format!(
        "EAIDE 启动 (pid={}, exe={:?}, data_dir={}, log_dir={})",
        std::process::id(),
        std::env::current_exe().ok(),
        get_app_data_dir().display(),
        get_log_dir().display(),
    ));
    app_log(&format!(
        "环境: RUST_BACKTRACE={:?}, EAIDE_AGENT_HOST={:?}, EAIDE_AGENT_PORT={:?}, EAIDE_AGENT_AUTO_START={:?}",
        std::env::var("RUST_BACKTRACE").ok(),
        std::env::var("EAIDE_AGENT_HOST").ok(),
        std::env::var("EAIDE_AGENT_PORT").ok(),
        std::env::var("EAIDE_AGENT_AUTO_START").ok(),
    ));

    // 5. Tauri builder —— setup 永不返回 Err，所有失败走 fallback
    app_log("[lib.rs] 构造 Tauri::Builder，准备进入 Builder.run");
    let result = tauri::Builder::default()
        .plugin(tauri_plugin_log::Builder::new().build())
        .plugin(tauri_plugin_os::init())
        .plugin(tauri_plugin_dialog::init())
        .setup(|app| {
            app_log("[setup] Tauri setup hook 进入 —— 准备初始化 AppState");
            match AppState::try_init(app.handle()) {
                Ok(state) => {
                    app_log("[setup] AppState::try_init 成功");
                    app.manage(state);
                }
                Err(e) => {
                    app_log(&format!("[setup] AppState::try_init 失败，进入降级模式: {}", e));
                    let fallback = AppState::fallback(app.handle(), &e);
                    app.manage(fallback);
                    // 发出降级事件，前端可以拿来显示 banner
                    let _ = app.emit("eaide://degraded", e.to_string());
                }
            }
            app_log("[setup] setup hook 结束 —— Tauri 应开始渲染主窗口");
            Ok(())
        })
        .on_window_event(|window, event| {
            if let tauri::WindowEvent::CloseRequested { .. } = event {
                // 关键：Windows 下关主窗口 ≠ app 退出；agent 子进程不会自动死。
                // 三层兜底（任意一层成功都算赢）：
                //   1) 异步 taskkill /F /T —— OS 强制杀进程树（最稳）
                //   2) child.kill() —— Rust 子进程句柄
                //   3) Windows Job Object —— 父进程退出时 OS 自动杀
                // 第 3 步是 CreateJobObjectW + KILL_ON_JOB_CLOSE + AssignProcessToJobObject
                // 在 spawn 时就绑好了，正常情况下根本到不了 1 和 2 步。
                std::thread::spawn(|| {
                    agent_manager::kill_agent_process_tree();
                    agent_manager::stop_agent_process();
                });
            }
            if let tauri::WindowEvent::Destroyed = event {
                // 兜底：Destroyed 事件时再 kill 一次（幂等）
                agent_manager::stop_agent_process();
                let _ = window;
            }
        })
        .invoke_handler(tauri::generate_handler![
            commands::agent::agent_chat,
            commands::agent::agent_approval,
            commands::agent::agent_cancel,
            commands::agent::agent_active_runs,
            commands::agent::agent_restart,
            commands::agent::agent_autonomy_confirm,
            commands::agent::agent_toolchain_get,
            commands::agent::agent_toolchain_save,
            commands::credentials::credential_get,
            commands::credentials::credential_set,
            commands::credentials::credential_delete,
            commands::credentials::credential_list,
            commands::credentials::credential_service_name,
            commands::audit::audit_search,
            commands::asset::asset_list,
            commands::asset::asset_add,
            commands::asset::asset_update,
            commands::asset::asset_remove,
            commands::llm::llm_get_config,
            commands::llm::llm_set_config,
            commands::envconfig::envconfig_list,
            commands::envconfig::envconfig_get,
            commands::envconfig::envconfig_save,
            commands::envconfig::envconfig_activate,
            commands::envconfig::envconfig_delete,
            commands::envconfig::envconfig_export,
            commands::envconfig::envconfig_import,
            commands::shell::open_devtools,
            commands::shell::read_text_file,
            commands::shell::list_dir_entries,
            commands::shell::reveal_in_explorer,
            // Phase 2D V0
            commands::skills::skills_list,
            commands::skills::skills_get,
            commands::skills::skills_save,
            commands::skills::skills_delete,
            commands::skills::skills_import,
            commands::skills::skills_export_all,
            commands::skills::skills_reload,
            // Phase 2C V0
            commands::router::router_get_metrics,
            commands::router::router_get_decisions,
            commands::router::router_set_weights,
            commands::router::router_reset_breaker,
            commands::router::router_set_spark_mode,
            commands::router::router_test_connection,
            commands::router::router_list_backends,
            commands::router::router_upsert_backend,
            commands::router::router_delete_backend,
            commands::router::router_get_weights,
            commands::router::agent_wait_ready,
            commands::router::agent_get_version,
            commands::router::agent_restart_now,
            commands::router::agent_read_log,
            // Phase 12 V0/V1.5：多智能体 Orchestrator
            commands::orchestrator::orchestrator_list,
            commands::orchestrator::orchestrator_get,
            commands::orchestrator::orchestrator_tree_stats,
            commands::orchestrator::orchestrator_cancel,
            commands::orchestrator::orchestrator_dispatch,
            commands::orchestrator::orchestrator_run_until_drained,
            commands::orchestrator::orchestrator_cancel_all,
            commands::orchestrator::orchestrator_dlq_list,
            commands::orchestrator::orchestrator_dlq_requeue,
            commands::orchestrator::orchestrator_dlq_close,
            commands::orchestrator::orchestrator_metrics,
            commands::orchestrator::orchestrator_queue_stats,
            commands::orchestrator::orchestrator_replay,
            // Phase 13 V0：DSpark 推测解码配置与指标
            commands::dspark::dspark_get_config,
            commands::dspark::dspark_get_policies,
            commands::dspark::dspark_get_recent,
            commands::dspark::dspark_reload_policies,
            commands::dspark::dspark_set_draft_model_path,
            commands::dspark::dspark_update_config,
            // Phase 2F V0
            commands::codenav::code_nav_jump,
            commands::codenav::code_nav_index,
            commands::codenav::code_nav_status,
            commands::codenav::code_nav_list_symbols,
            commands::codenav::code_nav_explain,
            commands::codenav::code_nav_llm_config,
            commands::codenav::code_nav_llm_config_reload,
            commands::codenav::code_nav_allowed_roots,
            commands::codenav::code_nav_llm_backend,
            commands::codenav::code_nav_llm_backend_bind,
            commands::codenav::code_nav_opened_projects,
            commands::codenav::code_nav_sync_opened_projects,
            commands::codenav::code_nav_add_opened_project,
            commands::codenav::code_nav_remove_opened_project,
            // Phase 2G V1.2：业务功能点导航 9 command（Rust 端包装 HTTP）
            commands::biznav::biznav_extract,
            commands::biznav::biznav_status,
            commands::biznav::biznav_list_features,
            commands::biznav::biznav_get_feature,
            commands::biznav::biznav_upsert_feature,
            commands::biznav::biznav_delete_feature,
            commands::biznav::biznav_import_yaml,
            commands::biznav::biznav_export_yaml,
            commands::biznav::biznav_affected,
            // Phase 4 V0 本地端侧模型
            commands::localai::localai_status,
            commands::localai::localai_health,
            commands::localai::knowledge_search,
            commands::localai::knowledge_status,
            // Phase 2F+ Task 6：大文件查看器 6 个 Tauri command
            logviewer::commands::logviewer_index_file,
            logviewer::commands::logviewer_search,
            logviewer::commands::logviewer_read_lines,
            logviewer::commands::logviewer_task_status,
            logviewer::commands::logviewer_cancel_task,
            logviewer::commands::logviewer_index_status,
            logviewer::commands::logviewer_stat_file,
            // Phase 2F+ V1.5: tail -f
            logviewer::commands::logviewer_tail_start,
            logviewer::commands::logviewer_tail_stop,
            logviewer::commands::logviewer_tail_status,
            logviewer::commands::logviewer_tail_list,
            // Phase 1B V1.5：原生工具层 6 安全工具 + 健康检查
            commands::builtin::builtin_stat_file,
            commands::builtin::builtin_mkdir,
            commands::builtin::builtin_find,
            commands::builtin::builtin_glob,
            commands::builtin::builtin_hash,
            commands::builtin::builtin_base64,
            commands::builtin::builtin_status,
            // Phase 1B V2：3 高危工具
            commands::builtin::builtin_delete_file,
            commands::builtin::builtin_move_file,
            commands::builtin::builtin_shell,
            // Phase 7 V0：数据专家 8 command（Rust 端包装 HTTP）
            commands::dataexpert::data_list_sources,
            commands::dataexpert::data_sync_schema,
            commands::dataexpert::data_nl2sql,
            commands::dataexpert::data_run_sql,
            commands::dataexpert::data_run_python,
            commands::dataexpert::data_chart_recommend,
            commands::dataexpert::data_export,
            commands::dataexpert::data_save_template,
            commands::dataexpert::data_test_connection,
            // Phase 5 V1：审核专家工作台（13 操作经 _op 字段分发到 Python FastAPI）
            commands::audit_expert::audit_decide,
            // Phase 6 V1.5：会话管理 17 command（V0 5 + V1.5 12）
            commands::sessions::sessions_create,
            commands::sessions::sessions_list,
            commands::sessions::sessions_get,
            commands::sessions::sessions_delete,
            commands::sessions::sessions_kb_search,
            commands::sessions::sessions_append_message,
            commands::sessions::sessions_record_checkpoint,
            commands::sessions::sessions_stats,
            commands::sessions::sessions_search,
            commands::sessions::sessions_branch_create,
            commands::sessions::sessions_branches_list,
            commands::sessions::sessions_share_create,
            commands::sessions::sessions_share_revoke,
            commands::sessions::sessions_share_grant,
            commands::sessions::sessions_share_list,
            commands::sessions::sessions_export,
            commands::sessions::sessions_import,
            commands::sessions::sessions_recovery,
            commands::sessions::sessions_event_chain,
            commands::sessions::sessions_event_chain_verify,
            // Phase 16：思维链可视化与文件操作追踪（GET 只读查询）
            commands::trace::trace_recent_sessions,
            commands::trace::trace_get_session,
            commands::trace::trace_get_step,
            commands::trace::trace_get_file_diff,
            // Phase 15 V0：前端实时预览引擎（窗口 + 会话 CRUD）
            commands::preview::preview_open_window,
            commands::preview::preview_close_window,
            commands::preview::preview_reload_window,
            commands::preview::preview_resize_window,
            commands::preview::preview_list_windows,
            commands::preview::preview_start,
            commands::preview::preview_stop,
            commands::preview::preview_sessions,
            commands::preview::preview_info,
            commands::preview::preview_reload,
            commands::preview::preview_install,
        ])
        .run(tauri::generate_context!());

    // 6. builder.run 返回 Err 也要落盘再结束
    match result {
        Ok(()) => app_log("[run] tauri::Builder::run 正常退出"),
        Err(e) => {
            let msg = format!("[run] tauri::Builder::run 返回 Err: {}", e);
            app_log(&msg);
            crash_log(&msg);
            // 让 panic 再次跑 hook（再写一次 crash.log），用户也能看到弹窗（如果有）
            panic!("{}", msg);
        }
    }
}
