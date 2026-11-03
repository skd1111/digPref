//! 本地知识库混合检索 Tauri Command 代理（_op 分发 → Python FastAPI /knowledge/v1/*）。
//!
//! 审核专家「上传参考资料」与设置页「知识库/RAG 参数」面板共用；聊天 rag_retrieve
//! 节点在 Agent 内部直调检索器，不经此命令。

use crate::error::{AppError, AppResult};
use serde_json::{json, Value};
use std::time::Duration;
use url::Url;

/// 把 `_op` + args 解析为 (相对路径, HTTP 方法, 请求体)；抽成独立纯函数便于单测路径构造。
fn resolve_request(
    op: &str,
    args: &Value,
    doc_id: Option<&str>,
) -> AppResult<(String, &'static str, Option<Value>)> {
    let (path, method, body): (String, &'static str, Option<Value>) = match op {
        "kb_upload" => (
            "/knowledge/v1/docs/upload".to_string(),
            "POST",
            Some(json!({
                "file_path": args.get("file_path").and_then(|v| v.as_str()).unwrap_or(""),
                "category": args.get("category").and_then(|v| v.as_str()).unwrap_or(""),
                // 置位时作为该资料的新版本（空串 → 后端按全新单版本处理）
                "base_doc_id": args.get("base_doc_id").and_then(|v| v.as_str()).unwrap_or(""),
            })),
        ),
        "kb_list" => {
            // 用 url crate 安全编码查询参数，然后只取 path+query 部分
            let mut u = Url::parse("http://x/knowledge/v1/docs").unwrap();
            {
                let mut pairs = u.query_pairs_mut();
                if let Some(q) = args.get("q").and_then(|v| v.as_str()) {
                    if !q.is_empty() {
                        pairs.append_pair("q", q);
                    }
                }
                if let Some(cat) = args.get("category").and_then(|v| v.as_str()) {
                    if !cat.is_empty() {
                        pairs.append_pair("category", cat);
                    }
                }
            }
            let pq = {
                let s = u[url::Position::BeforePath..].to_string();
                s.strip_suffix('?').map(String::from).unwrap_or(s)
            };
            (pq, "GET", None)
        }
        "kb_versions" => {
            let id = doc_id.ok_or_else(|| {
                AppError::Config("knowledge.kb_versions: missing doc_id".into())
            })?;
            (format!("/knowledge/v1/docs/{}/versions", id), "GET", None)
        }
        "kb_activate" => {
            let id = doc_id.ok_or_else(|| {
                AppError::Config("knowledge.kb_activate: missing doc_id".into())
            })?;
            (
                format!("/knowledge/v1/docs/{}/activate", id),
                "POST",
                Some(json!({})),
            )
        }
        "kb_set_category" => {
            let id = doc_id.ok_or_else(|| {
                AppError::Config("knowledge.kb_set_category: missing doc_id".into())
            })?;
            (
                format!("/knowledge/v1/docs/{}/category", id),
                "POST",
                Some(json!({
                    "category": args.get("category").and_then(|v| v.as_str()).unwrap_or(""),
                })),
            )
        }
        "kb_delete" => {
            let id = doc_id
                .ok_or_else(|| AppError::Config("knowledge.kb_delete: missing doc_id".into()))?;
            // all_versions=true → 删该逻辑资料全部版本（拼查询串）
            let all = args
                .get("all_versions")
                .and_then(|v| v.as_bool())
                .unwrap_or(false);
            let p = if all {
                format!("/knowledge/v1/docs/{}?all_versions=true", id)
            } else {
                format!("/knowledge/v1/docs/{}", id)
            };
            (p, "DELETE", None)
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
        "kb_entities" => {
            let id = doc_id.ok_or_else(|| {
                AppError::Config("knowledge.kb_entities: missing doc_id".into())
            })?;
            (format!("/knowledge/v1/docs/{}/entities", id), "GET", None)
        }
        "kb_entity_search" => (
            "/knowledge/v1/entities/search".to_string(),
            "POST",
            Some(json!({
                "query": args.get("query").and_then(|v| v.as_str()).unwrap_or(""),
                "top_k": args.get("top_k").and_then(|v| v.as_u64()).unwrap_or(10),
            })),
        ),
        _ => return Err(AppError::Config(format!("knowledge: unknown _op: {}", op))),
    };
    Ok((path, method, body))
}

#[tauri::command]
pub async fn knowledge(args: Value) -> AppResult<Value> {
    let op = args
        .get("_op")
        .and_then(|v| v.as_str())
        .ok_or_else(|| AppError::Config("knowledge: missing _op field".into()))?;
    let base_url = std::env::var("EAIDE_AGENT_BASE_URL")
        .unwrap_or_else(|_| "http://127.0.0.1:8765".to_string());
    let doc_id = args.get("doc_id").and_then(|v| v.as_str());
    let (path, method, body) = resolve_request(op, &args, doc_id)?;

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

    #[test]
    fn test_resolve_versions_and_activate_paths() {
        let (p, m, _) = resolve_request("kb_versions", &json!({}), Some("abc")).unwrap();
        assert_eq!(p, "/knowledge/v1/docs/abc/versions");
        assert_eq!(m, "GET");
        let (p2, m2, _) = resolve_request("kb_activate", &json!({}), Some("abc")).unwrap();
        assert_eq!(p2, "/knowledge/v1/docs/abc/activate");
        assert_eq!(m2, "POST");
    }

    #[test]
    fn test_resolve_delete_all_versions_query() {
        let (p, m, _) =
            resolve_request("kb_delete", &json!({ "all_versions": true }), Some("abc")).unwrap();
        assert_eq!(p, "/knowledge/v1/docs/abc?all_versions=true");
        assert_eq!(m, "DELETE");
        // 缺省/false → 不带查询串（只删该版本）
        let (p2, _, _) = resolve_request("kb_delete", &json!({}), Some("abc")).unwrap();
        assert_eq!(p2, "/knowledge/v1/docs/abc");
    }

    #[test]
    fn test_resolve_upload_carries_base_doc_id() {
        let (_, _, body) = resolve_request(
            "kb_upload",
            &json!({ "file_path": "/x.md", "category": "legal", "base_doc_id": "d1" }),
            None,
        )
        .unwrap();
        let b = body.unwrap();
        assert_eq!(b["base_doc_id"], "d1");
        assert_eq!(b["file_path"], "/x.md");
        assert_eq!(b["category"], "legal");
    }

    #[test]
    fn test_resolve_missing_doc_id_errors() {
        assert!(resolve_request("kb_versions", &json!({}), None).is_err());
        assert!(resolve_request("kb_activate", &json!({}), None).is_err());
        assert!(resolve_request("kb_set_category", &json!({}), None).is_err());
        assert!(resolve_request("kb_delete", &json!({}), None).is_err());
    }

    #[test]
    fn test_resolve_set_category_path_and_body() {
        let (p, m, body) =
            resolve_request("kb_set_category", &json!({ "category": "legal" }), Some("abc"))
                .unwrap();
        assert_eq!(p, "/knowledge/v1/docs/abc/category");
        assert_eq!(m, "POST");
        assert_eq!(body.unwrap()["category"], "legal");
    }

    #[test]
    fn test_resolve_kb_list_no_params() {
        let (p, m, _) = resolve_request("kb_list", &json!({}), None).unwrap();
        assert_eq!(p, "/knowledge/v1/docs");
        assert_eq!(m, "GET");
    }

    #[test]
    fn test_resolve_kb_list_with_q_and_category() {
        let (p, m, _) = resolve_request(
            "kb_list",
            &json!({ "q": "报销", "category": "finance" }),
            None,
        )
        .unwrap();
        // url crate 会 percent-encode 中文
        assert!(p.starts_with("/knowledge/v1/docs?"));
        assert!(p.contains("q="));
        assert!(p.contains("category=finance"));
        assert_eq!(m, "GET");
    }

    #[test]
    fn test_resolve_kb_list_empty_strings_omitted() {
        let (p, _, _) = resolve_request(
            "kb_list",
            &json!({ "q": "", "category": "" }),
            None,
        )
        .unwrap();
        // 空串不拼查询参数
        assert_eq!(p, "/knowledge/v1/docs");
    }
}
