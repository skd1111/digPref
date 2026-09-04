//! 本地知识库混合检索 Tauri Command 代理（_op 分发 → Python FastAPI /knowledge/v1/*）。
//!
//! 审核专家「上传参考资料」与设置页「知识库/RAG 参数」面板共用；聊天 rag_retrieve
//! 节点在 Agent 内部直调检索器，不经此命令。

use crate::error::{AppError, AppResult};
use serde_json::{json, Value};
use std::time::Duration;

#[tauri::command]
pub async fn knowledge(args: Value) -> AppResult<Value> {
    let op = args
        .get("_op")
        .and_then(|v| v.as_str())
        .ok_or_else(|| AppError::Config("knowledge: missing _op field".into()))?;
    let base_url = std::env::var("EAIDE_AGENT_BASE_URL")
        .unwrap_or_else(|_| "http://127.0.0.1:8765".to_string());
    let doc_id = args.get("doc_id").and_then(|v| v.as_str());

    let (path, method, body): (String, &str, Option<Value>) = match op {
        "kb_upload" => (
            "/knowledge/v1/docs/upload".to_string(),
            "POST",
            Some(json!({
                "file_path": args.get("file_path").and_then(|v| v.as_str()).unwrap_or(""),
                "category": args.get("category").and_then(|v| v.as_str()).unwrap_or(""),
            })),
        ),
        "kb_list" => ("/knowledge/v1/docs".to_string(), "GET", None),
        "kb_delete" => {
            let id = doc_id
                .ok_or_else(|| AppError::Config("knowledge.kb_delete: missing doc_id".into()))?;
            (format!("/knowledge/v1/docs/{}", id), "DELETE", None)
        }
        "kb_search" => (
            "/knowledge/v1/search".to_string(),
            "POST",
            Some(json!({
                "query": args.get("query").and_then(|v| v.as_str()).unwrap_or(""),
                "top_k": args.get("top_k").and_then(|v| v.as_u64()).unwrap_or(5),
                "category": args.get("category").and_then(|v| v.as_str()).unwrap_or(""),
            })),
        ),
        "kb_reindex" => ("/knowledge/v1/reindex".to_string(), "POST", Some(json!({}))),
        "kb_status" => ("/knowledge/v1/status".to_string(), "GET", None),
        "kb_config_get" => ("/knowledge/v1/config".to_string(), "GET", None),
        "kb_config_set" => (
            "/knowledge/v1/config".to_string(),
            "POST",
            Some(json!({ "config": args.get("config").cloned().unwrap_or(json!({})) })),
        ),
        _ => return Err(AppError::Config(format!("knowledge: unknown _op: {}", op))),
    };

    let url = format!("{}{}", base_url, path);
    let client = reqwest::Client::builder()
        .timeout(Duration::from_secs(30))
        .build()
        .map_err(|e| AppError::Config(format!("reqwest client: {}", e)))?;
    let req = match method {
        "GET" => client.get(&url),
        "POST" => client.post(&url).json(&body.unwrap_or(json!({}))),
        "DELETE" => client.delete(&url),
        _ => return Err(AppError::Config(format!("unsupported method: {}", method))),
    };
    let resp = req
        .send()
        .await
        .map_err(|e| AppError::Config(format!("knowledge.http: {}", e)))?;
    let status = resp.status();
    let text = resp
        .text()
        .await
        .map_err(|e| AppError::Config(format!("knowledge.read_body: {}", e)))?;
    if !status.is_success() {
        return Err(AppError::Config(format!(
            "knowledge: agent returned HTTP {} body={}",
            status, text
        )));
    }
    serde_json::from_str(&text).map_err(|e| {
        AppError::Config(format!("knowledge: invalid JSON response: {} (body={})", e, text))
    })
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_knowledge_missing_op() {
        let rt = tokio::runtime::Runtime::new().unwrap();
        let result = rt.block_on(knowledge(json!({})));
        assert!(result.is_err());
    }

    #[test]
    fn test_knowledge_unknown_op() {
        let rt = tokio::runtime::Runtime::new().unwrap();
        let result = rt.block_on(knowledge(json!({ "_op": "bogus" })));
        assert!(result.is_err());
    }
}
