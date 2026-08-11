//! Phase 5 V1 · 审核专家 Tauri Command 代理。
//!
//! 设计：前端调用一个统一命令 `audit_decide`，Rust 按 `_op` 字段分发到
//! Python FastAPI 的具体端点。这样前端只需要记住一个 invoke name，Rust 侧
//! 做 op → HTTP URL + method 映射。
//!
//! 端点（Python FastAPI /audit/*）：
//!   - create_task         POST /audit/tasks
//!   - list_tasks          GET  /audit/tasks
//!   - get_task            GET  /audit/tasks/{id}
//!   - add_evidence        POST /audit/tasks/{id}/evidence
//!   - list_evidence       GET  /audit/tasks/{id}/evidence
//!   - list_compliance     GET  /audit/tasks/{id}/compliance
//!   - decide              POST /audit/tasks/{id}/decide
//!   - dual_first          POST /audit/tasks/{id}/dual-first
//!   - dual_second         POST /audit/tasks/{id}/dual-second
//!   - verify              GET  /audit/tasks/{id}/verify
//!   - totp                GET  /audit/mfa/{username}
//!   - public_key          GET  /audit/public-key
//!   - stats               GET  /audit/stats
//!
//! CLAUDE.md §4 SSE 三处同步：3 个新事件已在 Rust `sse_bridge.rs::channel`
//! + TS `events.ts::EVT` 同步注册。

use crate::error::{AppError, AppResult};
use serde_json::{json, Value};
use std::time::Duration;


/// 单 command 入口：按 `args._op` 字段分发到 Python FastAPI。
///
/// Python FastAPI 端点由 env `EAIDE_AGENT_BASE_URL` 配置（默认
/// `http://127.0.0.1:8765`，与 sse_bridge 同源）。
#[tauri::command]
pub async fn audit_decide(args: Value) -> AppResult<Value> {
    let op = args
        .get("_op")
        .and_then(|v| v.as_str())
        .ok_or_else(|| AppError::Config("audit_decide: missing _op field".into()))?;

    let base_url = std::env::var("EAIDE_AGENT_BASE_URL")
        .unwrap_or_else(|_| "http://127.0.0.1:8765".to_string());

    let task_id = args.get("task_id").and_then(|v| v.as_str());
    let username = args.get("username").and_then(|v| v.as_str());

    // 构造 URL + method + body
    let (path, method, body) = match op {
        "create" => (
            "/audit/tasks".to_string(),
            "POST",
            Some(serde_json::json!({
                "run_id": args.get("run_id").and_then(|v| v.as_str()).unwrap_or("default"),
                "title": args.get("title").and_then(|v| v.as_str()).unwrap_or(""),
                "description": args.get("description").and_then(|v| v.as_str()).unwrap_or(""),
                "risk_level": args.get("risk_level").and_then(|v| v.as_str()).unwrap_or("medium"),
                "pending_tool_call": args.get("pending_tool_call").cloned().unwrap_or(json!({})),
                "requested_by": args.get("requested_by").and_then(|v| v.as_str()).unwrap_or("u-shen"),
                "meta": args.get("meta").cloned().unwrap_or(json!({})),
            })),
        ),
        "list" => {
            let status = args.get("status").and_then(|v| v.as_str());
            let risk = args.get("risk_level").and_then(|v| v.as_str());
            let limit = args.get("limit").and_then(|v| v.as_i64()).unwrap_or(50);
            let mut qs = format!("limit={}", limit);
            if let Some(s) = status {
                qs.push_str(&format!("&status={}", urlencode(s)));
            }
            if let Some(r) = risk {
                qs.push_str(&format!("&risk_level={}", urlencode(r)));
            }
            (format!("/audit/tasks?{}", qs), "GET", None)
        }
        "get" => {
            let id = task_id.ok_or_else(|| AppError::Config("audit_decide.get: missing task_id".into()))?;
            (format!("/audit/tasks/{}", id), "GET", None)
        }
        "evidence" => {
            let id = task_id.ok_or_else(|| AppError::Config("audit_decide.evidence: missing task_id".into()))?;
            (
                format!("/audit/tasks/{}/evidence", id),
                "POST",
                Some(json!({
                    "evidence_type": args.get("evidence_type").and_then(|v| v.as_str()).unwrap_or("tool_call"),
                    "title": args.get("title").and_then(|v| v.as_str()).unwrap_or(""),
                    "content": args.get("content").cloned().unwrap_or(json!({})),
                    "source": args.get("source").and_then(|v| v.as_str()).unwrap_or("agent"),
                })),
            )
        }
        "decide" => {
            let id = task_id.ok_or_else(|| AppError::Config("audit_decide.decide: missing task_id".into()))?;
            (
                format!("/audit/tasks/{}/decide", id),
                "POST",
                Some(json!({
                    "action_type": args.get("action_type").and_then(|v| v.as_str()).unwrap_or("approve"),
                    "actor": args.get("actor").and_then(|v| v.as_str()).unwrap_or("u-shen"),
                    "reason": args.get("reason").and_then(|v| v.as_str()).unwrap_or(""),
                    "mfa_verified": args.get("mfa_verified").and_then(|v| v.as_bool()).unwrap_or(false),
                    "totp_code": args.get("totp_code").cloned().unwrap_or(Value::Null),
                    "use_rsa": args.get("use_rsa").and_then(|v| v.as_bool()).unwrap_or(true),
                })),
            )
        }
        "dual_first" => {
            let id = task_id.ok_or_else(|| AppError::Config("audit_decide.dual_first: missing task_id".into()))?;
            (
                format!("/audit/tasks/{}/dual-first", id),
                "POST",
                Some(json!({
                    "actor": args.get("actor").and_then(|v| v.as_str()).unwrap_or("u-shen"),
                    "reason": args.get("reason").and_then(|v| v.as_str()).unwrap_or(""),
                    "mfa_verified": args.get("mfa_verified").and_then(|v| v.as_bool()).unwrap_or(false),
                    "totp_code": args.get("totp_code").cloned().unwrap_or(Value::Null),
                    "use_rsa": args.get("use_rsa").and_then(|v| v.as_bool()).unwrap_or(true),
                })),
            )
        }
        "dual_second" => {
            let id = task_id.ok_or_else(|| AppError::Config("audit_decide.dual_second: missing task_id".into()))?;
            (
                format!("/audit/tasks/{}/dual-second", id),
                "POST",
                Some(json!({
                    "actor": args.get("actor").and_then(|v| v.as_str()).unwrap_or("u-chenyu"),
                    "reason": args.get("reason").and_then(|v| v.as_str()).unwrap_or(""),
                    "mfa_verified": args.get("mfa_verified").and_then(|v| v.as_bool()).unwrap_or(false),
                    "totp_code": args.get("totp_code").cloned().unwrap_or(Value::Null),
                    "use_rsa": args.get("use_rsa").and_then(|v| v.as_bool()).unwrap_or(true),
                })),
            )
        }
        "verify" => {
            let id = task_id.ok_or_else(|| AppError::Config("audit_decide.verify: missing task_id".into()))?;
            (format!("/audit/tasks/{}/verify", id), "GET", None)
        }
        "totp" => {
            let u = username.ok_or_else(|| AppError::Config("audit_decide.totp: missing username".into()))?;
            (format!("/audit/mfa/{}", u), "GET", None)
        }
        "public_key" => ("/audit/public-key".to_string(), "GET", None),
        "stats" => ("/audit/stats".to_string(), "GET", None),
        _ => {
            return Err(AppError::Config(format!("audit_decide: unknown _op: {}", op)));
        }
    };

    // 调用 Python FastAPI
    let url = format!("{}{}", base_url, path);
    let client = reqwest::Client::builder()
        .timeout(Duration::from_secs(15))
        .build()
        .map_err(|e| AppError::Config(format!("reqwest client: {}", e)))?;

    let req = match method {
        "GET" => client.get(&url),
        "POST" => client.post(&url).json(&body.unwrap_or(json!({}))),
        _ => return Err(AppError::Config(format!("unsupported method: {}", method))),
    };

    let resp = req.send().await.map_err(|e| AppError::Config(format!("audit_decide.http: {}", e)))?;
    let status = resp.status();
    let text = resp.text().await.map_err(|e| AppError::Config(format!("audit_decide.read_body: {}", e)))?;

    if !status.is_success() {
        return Err(AppError::Config(format!(
            "audit_decide: agent returned HTTP {} body={}",
            status, text
        )));
    }

    // 解析 JSON 返回
    serde_json::from_str(&text).map_err(|e| AppError::Config(format!(
        "audit_decide: invalid JSON response: {} (body={})", e, text
    )))
}


/// URL-encode (minimal — only alphanumerics + a few chars allowed in query)
fn urlencode(s: &str) -> String {
    let mut out = String::with_capacity(s.len());
    for c in s.chars() {
        if c.is_ascii_alphanumeric() || c == '-' || c == '_' || c == '.' || c == '~' {
            out.push(c);
        } else {
            // UTF-8 字节逐个 %XX
            for b in c.to_string().as_bytes() {
                out.push_str(&format!("%{:02X}", b));
            }
        }
    }
    out
}


// ---- 单元测试 ---------------------------------------------------------------

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_urlencode_basic() {
        assert_eq!(urlencode("pending"), "pending");
        assert_eq!(urlencode("a/b"), "a%2Fb");
        assert_eq!(urlencode("中文"), "%E4%B8%AD%E6%96%87");
    }

    #[test]
    fn test_audit_decide_missing_op() {
        // 同步测：在 tokio runtime 中跑
        let rt = tokio::runtime::Runtime::new().unwrap();
        let args = serde_json::json!({});
        let result = rt.block_on(audit_decide(args));
        assert!(result.is_err());
    }
}