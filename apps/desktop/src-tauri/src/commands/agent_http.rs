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

/// 带**总预算**的有界重试：所有尝试与退避共用一个截止时间。
///
/// 为什么慢 op 不能只限次数（2026-11-09，上传文件 OCR 报「error decoding
/// response body」）：reqwest 0.12 的 `Response::bytes()` 把 body 流错误
/// **连同请求总超时到期**一起包成 `Kind::Decode` —— `TotalTimeoutBody` 到期时
/// 抛 `error::body(TimedOut)`，出来后被 `async_impl/response.rs` 末尾的
/// `.map_err(crate::error::decode)` 重新包装。于是 `is_decode()` 为 true 而
/// `is_timeout()` 为 false：**「服务端卡到超时」与「连接被瞬断」在错误形态上
/// 完全一样**，`is_transient` 分不开两者。
///
/// 因此对 300s 级慢 op 只按次数重试，会把一次卡死放大成 5 × 300s ≈ 25 分钟
/// 前端干等。这里把每次尝试的 reqwest 超时设为「剩余预算」，慢失败最多跑满
/// 一个 budget 就收手，而毫秒级的连接瞬断仍能用满全部次数。
///
/// `make` 收到本次尝试可用的剩余时长，应据此 `.timeout(remaining)`。
/// 非幂等 op 若要走此重试，后端必须先做成幂等（否则重发会重复落副作用）。
pub async fn send_json_bounded<F>(
    err_prefix: &str,
    max_attempts: u32,
    total_budget: Duration,
    mut make: F,
) -> AppResult<Value>
where
    F: FnMut(Duration) -> RequestBuilder,
{
    use tokio::time::Instant;

    let deadline = Instant::now() + total_budget;
    let attempts = max_attempts.max(1);
    let mut attempt = 0u32;

    loop {
        attempt += 1;
        let remaining = deadline.saturating_duration_since(Instant::now());
        if remaining.is_zero() {
            return Err(AppError::Config(format!(
                "{err_prefix}: 已尝试 {attempt} 次仍未成功，累计等待已达 {:?}（Agent 侧可能长时间无响应）",
                total_budget
            )));
        }

        // 成功分支直接 return；走到 failed 一定是需要判定的错误
        let (tag, e) = match make(remaining).send().await {
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
                    // body 读一半被中断（也可能是总超时到期，见函数注释）
                    Err(e) => ("read_body", e),
                }
            }
            Err(e) => ("http", e),
        };

        let backoff = Duration::from_millis(120 * u64::from(attempt));
        if attempt < attempts && is_transient(&e) && Instant::now() + backoff < deadline {
            tokio::time::sleep(backoff).await;
            continue;
        }
        // 带上尝试次数：慢失败与瞬断在文案上原本无法区分，这是唯一的线索
        return Err(AppError::Config(format!(
            "{err_prefix}.{tag}: {e} (attempt {attempt}/{attempts})"
        )));
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

    /// 锁住 `send_json_bounded` 的核心性质：**总预算封住累计等待时长**。
    ///
    /// ocr-fill 不敢用 `send_json(.., 5, ..)` 而改用本函数，就是因为这个性质；一旦有人
    /// 把它改回只限次数，300s 级慢 op 会乘成 5 倍。这里用「连接必败 + 小预算」
    /// 构造快失败：断言的不是具体重试到第几次（依赖机器快慢，会脆），而是
    /// 总耗时被 budget 拦住、且错误里带得上尝试次数。
    #[tokio::test]
    async fn bounded_retry_caps_total_wait_by_budget_not_attempt_count() {
        let client = reqwest::Client::new();
        let budget = Duration::from_millis(400);
        let started = std::time::Instant::now();

        let err = send_json_bounded("probe", 5, budget, |_| {
            client.get("http://127.0.0.1:1/ops/ping")
        })
        .await
        .expect_err("127.0.0.1:1 无监听者，必须失败");

        let elapsed = started.elapsed();
        let AppError::Config(msg) = err else {
            panic!("expected Config error, got {err:?}");
        };
        // 连接阶段失败 → tag 为 http，且必须把尝试次数透出来供区分快/慢失败
        assert!(msg.contains("probe.http:"), "{msg}");
        assert!(msg.contains("(attempt ") && msg.contains("/5)"), "{msg}");
        // 预算 400ms + 退避，绝不可能跑满 5 次；给充足够容忍度以防慢机器
        assert!(
            elapsed < Duration::from_secs(5),
            "budget 未拦住总时长：{elapsed:?}"
        );
    }
}
