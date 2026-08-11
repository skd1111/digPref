//! 文档风险合规审核 Tauri Command 代理（_op 分发 → Python FastAPI /doc-review/*）。

use crate::error::{AppError, AppResult};
use serde_json::{json, Value};
use std::time::Duration;

#[tauri::command]
pub async fn doc_review(args: Value) -> AppResult<Value> {
    let op = args
        .get("_op")
        .and_then(|v| v.as_str())
        .ok_or_else(|| AppError::Config("doc_review: missing _op field".into()))?;
    let base_url = std::env::var("EAIDE_AGENT_BASE_URL")
        .unwrap_or_else(|_| "http://127.0.0.1:8765".to_string());
    let doc_id = args.get("doc_id").and_then(|v| v.as_str());

    let (path, method, body): (String, &str, Option<Value>) = match op {
        "register" => (
            "/doc-review/documents".to_string(),
            "POST",
            Some(json!({
                "file_path": args.get("file_path").and_then(|v| v.as_str()).unwrap_or(""),
            })),
        ),
        "list" => ("/doc-review/documents".to_string(), "GET", None),
        "get" => {
            let id = doc_id.ok_or_else(|| AppError::Config("doc_review.get: missing doc_id".into()))?;
            (format!("/doc-review/documents/{}", id), "GET", None)
        }
        "analyze" => {
            let id = doc_id.ok_or_else(|| AppError::Config("doc_review.analyze: missing doc_id".into()))?;
            (format!("/doc-review/documents/{}/analyze", id), "POST", Some(json!({})))
        }
        "findings" => {
            let id = doc_id.ok_or_else(|| AppError::Config("doc_review.findings: missing doc_id".into()))?;
            let run_id = args.get("run_id").and_then(|v| v.as_str());
            let mut path = format!("/doc-review/documents/{}/findings", id);
            if let Some(rid) = run_id {
                path.push_str(&format!("?run_id={}", urlencode(rid)));
            }
            (path, "GET", None)
        }
        "status" => {
            let id = doc_id.ok_or_else(|| AppError::Config("doc_review.status: missing doc_id".into()))?;
            (format!("/doc-review/documents/{}/status", id), "GET", None)
        }
        "delete" => {
            let id = doc_id.ok_or_else(|| AppError::Config("doc_review.delete: missing doc_id".into()))?;
            (format!("/doc-review/documents/{}", id), "DELETE", None)
        }
        _ => return Err(AppError::Config(format!("doc_review: unknown _op: {}", op))),
    };

    let url = format!("{}{}", base_url, path);
    let client = reqwest::Client::builder()
        .timeout(Duration::from_secs(15))
        .build()
        .map_err(|e| AppError::Config(format!("reqwest client: {}", e)))?;
    let req = match method {
        "GET" => client.get(&url),
        "POST" => client.post(&url).json(&body.unwrap_or(json!({}))),
        "DELETE" => client.delete(&url),
        _ => return Err(AppError::Config(format!("unsupported method: {}", method))),
    };
    let resp = req.send().await.map_err(|e| AppError::Config(format!("doc_review.http: {}", e)))?;
    let status = resp.status();
    let text = resp.text().await.map_err(|e| AppError::Config(format!("doc_review.read_body: {}", e)))?;
    if !status.is_success() {
        return Err(AppError::Config(format!("doc_review: agent returned HTTP {} body={}", status, text)));
    }
    serde_json::from_str(&text).map_err(|e| AppError::Config(format!(
        "doc_review: invalid JSON response: {} (body={})", e, text
    )))
}

fn urlencode(s: &str) -> String {
    let mut out = String::with_capacity(s.len());
    for c in s.chars() {
        if c.is_ascii_alphanumeric() || c == '-' || c == '_' || c == '.' || c == '~' {
            out.push(c);
        } else {
            for b in c.to_string().as_bytes() {
                out.push_str(&format!("%{:02X}", b));
            }
        }
    }
    out
}

/// 导出审核结果为 Word：从 Agent 下载 docx 二进制并写入用户选择的路径。
///
/// 返回 `{ "path": save_path, "bytes": N }`；二进制不走 _op 分发的 JSON 通道。
#[tauri::command]
pub async fn doc_review_export_word(args: Value) -> AppResult<Value> {
    let doc_id = args
        .get("doc_id")
        .and_then(|v| v.as_str())
        .ok_or_else(|| AppError::Config("doc_review_export_word: missing doc_id".into()))?;
    let mode = args
        .get("mode")
        .and_then(|v| v.as_str())
        .unwrap_or("risks_only");
    let save_path = args
        .get("save_path")
        .and_then(|v| v.as_str())
        .ok_or_else(|| AppError::Config("doc_review_export_word: missing save_path".into()))?;
    if mode != "full" && mode != "risks_only" {
        return Err(AppError::Config("doc_review_export_word: mode 必须为 full 或 risks_only".into()));
    }
    let base_url = std::env::var("EAIDE_AGENT_BASE_URL")
        .unwrap_or_else(|_| "http://127.0.0.1:8765".to_string());
    let url = format!(
        "{}/doc-review/documents/{}/export?mode={}",
        base_url,
        doc_id,
        urlencode(mode)
    );
    let client = reqwest::Client::builder()
        .timeout(Duration::from_secs(60))
        .build()
        .map_err(|e| AppError::Config(format!("reqwest client: {}", e)))?;
    let resp = client
        .get(&url)
        .send()
        .await
        .map_err(|e| AppError::Config(format!("doc_review_export.http: {}", e)))?;
    let status = resp.status();
    let bytes = resp
        .bytes()
        .await
        .map_err(|e| AppError::Config(format!("doc_review_export.read_body: {}", e)))?;
    if !status.is_success() {
        let preview = String::from_utf8_lossy(&bytes[..bytes.len().min(300)]);
        return Err(AppError::Config(format!(
            "doc_review_export: agent returned HTTP {} body={}",
            status, preview
        )));
    }
    std::fs::write(save_path, &bytes)
        .map_err(|e| AppError::Config(format!("doc_review_export.write: {}", e)))?;
    Ok(json!({ "path": save_path, "bytes": bytes.len() }))
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_urlencode() {
        assert_eq!(urlencode("a/b"), "a%2Fb");
        assert_eq!(urlencode("中文"), "%E4%B8%AD%E6%96%87");
    }

    #[test]
    fn test_doc_review_missing_op() {
        let rt = tokio::runtime::Runtime::new().unwrap();
        let result = rt.block_on(doc_review(json!({})));
        assert!(result.is_err());
    }
}
