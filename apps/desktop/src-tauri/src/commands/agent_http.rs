//! 转发到 Agent（FastAPI）的 HTTP 执行公共逻辑。
//!
//! 背景（BUGFIX read_body 报错）：批量导入高峰期 Agent 单 worker 被 LLM 429 重试 /
//! reranker 内联推理打满时，个别 GET 请求的连接会在响应体传完前被中断，reqwest 表现为
//! `error decoding response body`。这类错误是瞬时的，且 GET 幂等——整段重发即可恢复，
//! 不该直接冒泡成前端红字「读取知识库失败」。非幂等 op（POST/DELETE，尤其 HITL 审批）
//! 一律 `max_attempts = 1`，绝不重试，避免重复副作用。

use crate::error::{AppError, AppResult};
use reqwest::RequestBuilder;
use serde_json::Value;
use std::time::Duration;

/// 是否为可重试的瞬时错误：连接层失败 / 读写 body 中断 / body 解码失败 / 请求发送失败。
/// 这些都会**快速失败**（不是等满超时），重试成本低。
/// 刻意排除 `is_timeout`：长超时 op（如 doc_review get/findings 90s）若因真超时重试会放大到分钟级；
/// 而观测到的 `error decoding response body` 属 `is_decode`/`is_body`，本就被覆盖。
/// 明确拿到 HTTP 状态码（含 4xx/5xx）也不算瞬时错误——那是后端的真实响应。
fn is_transient(e: &reqwest::Error) -> bool {
    e.is_connect() || e.is_request() || e.is_body() || e.is_decode()
}

/// 发送 → 读 body → 校验状态 → 解析 JSON；对瞬时错误做有界重试。
///
/// `make` 每次调用返回一个**全新**的 RequestBuilder：reqwest 的 builder 会被 `send`
/// 消费，重试必须重建请求。`max_attempts` 为总尝试次数（含首次），幂等 GET 传 >1，
/// 非幂等 POST/DELETE 传 1。退避线性递增（120ms × 第 n 次）。
pub async fn send_json<F>(err_prefix: &str, max_attempts: u32, mut make: F) -> AppResult<Value>
where
    F: FnMut() -> RequestBuilder,
{
    let attempts = max_attempts.max(1);
    let mut attempt = 0u32;
    loop {
        attempt += 1;
        match make().send().await {
            Ok(resp) => {
                let status = resp.status();
                match resp.text().await {
                    Ok(text) => {
                        if !status.is_success() {
                            return Err(AppError::Config(format!(
                                "{err_prefix}: agent returned HTTP {status} body={text}"
                            )));
                        }
                        return serde_json::from_str(&text).map_err(|e| {
                            AppError::Config(format!(
                                "{err_prefix}: invalid JSON response: {e} (body={text})"
                            ))
                        });
                    }
                    // body 读取被中断：未拿到完整响应，幂等请求可整段重发
                    Err(e) if attempt < attempts && is_transient(&e) => {
                        tokio::time::sleep(Duration::from_millis(120 * u64::from(attempt))).await;
                    }
                    Err(e) => {
                        return Err(AppError::Config(format!("{err_prefix}.read_body: {e}")));
                    }
                }
            }
            // 连接/发送层瞬时错误：同样整段重发
            Err(e) if attempt < attempts && is_transient(&e) => {
                tokio::time::sleep(Duration::from_millis(120 * u64::from(attempt))).await;
            }
            Err(e) => {
                return Err(AppError::Config(format!("{err_prefix}.http: {e}")));
            }
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_is_transient_predicate_compiles_and_classifies_build_error() {
        // builder 阶段错误（非法 URL）既非 connect/body/decode/timeout/request，
        // 不应被判定为瞬时错误——这类应直接失败，不进入重试。
        let client = reqwest::Client::new();
        let err = client.get("http://::bad::url").build().unwrap_err();
        assert!(!is_transient(&err));
    }
}
