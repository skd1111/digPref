//! ops Tauri commands —— 运营工作台业务记录（Phase 2H）。
//!
//! 包装 HTTP 调用 FastAPI `/ops/*`（记录卡片 CRUD + AI 总结）。
//! 模板照抄 reqflow.rs。

use tauri::State;

use crate::commands::agent_http::{send_json, send_json_bounded};
use crate::error::AppError;
use crate::state::AppState;

type CmdResult<T> = Result<T, String>;

fn err(e: impl std::fmt::Display) -> String {
    format!("ops command failed: {}", e)
}

/// send_json 的 AppResult → ops 的 String 错误：Config 内原文（含
/// "ops command failed" 前缀）原样透传，前端瞬态判定（"error sending request"
/// 等连接失败文案）仍能命中。
fn to_cmd_err(e: AppError) -> String {
    match e {
        AppError::Config(s) => s,
        other => err(other),
    }
}

/// 幂等 GET 统一走 send_json：连接 / body 截断 / 解码失败等瞬时错误有界重试 3 次。
/// BUGFIX #207：与 #197 同源 —— Agent 单 worker 忙时响应体传完前被中断，
/// reqwest 报 `error decoding response body`；ops 此前无重试，首开一次截断即
/// 冒泡成前端红字「ops command failed」。
async fn get_json<F>(make: F) -> CmdResult<serde_json::Value>
where
    F: FnMut() -> reqwest::RequestBuilder,
{
    send_json("ops command failed", 3, make).await.map_err(to_cmd_err)
}

/// 将 HTTP 响应转为 JSON；非 2xx 时提取 detail 返回 Err。
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

/// 简易 URL 编码（避免拉 urlencoding crate）。
fn urlencoding(s: &str) -> String {
    let mut out = String::with_capacity(s.len());
    for b in s.bytes() {
        match b {
            b'A'..=b'Z' | b'a'..=b'z' | b'0'..=b'9' | b'-' | b'_' | b'.' | b'~' => {
                out.push(b as char);
            }
            _ => {
                out.push_str(&format!("%{:02X}", b));
            }
        }
    }
    out
}

/// POST /ops/records —— 创建业务记录卡片。
#[tauri::command]
pub async fn ops_create_record(
    body: serde_json::Value,
    state: State<'_, AppState>,
) -> CmdResult<serde_json::Value> {
    let url = agent_url(&state, "/ops/records");
    let client = reqwest::Client::new();
    let resp = client.post(&url).json(&body).send().await.map_err(err)?;
    json_or_err(resp).await
}

/// GET /ops/records?feature_id=&project_name=&limit=
#[tauri::command]
pub async fn ops_list_records(
    feature_id: Option<String>,
    project_name: Option<String>,
    limit: Option<u32>,
    state: State<'_, AppState>,
) -> CmdResult<serde_json::Value> {
    let mut url = agent_url(&state, "/ops/records");
    let mut first = true;
    let mut push = |k: &str, v: &str| {
        if first {
            url.push('?');
            first = false;
        } else {
            url.push('&');
        }
        url.push_str(&format!("{}={}", k, urlencoding(v)));
    };
    if let Some(f) = feature_id.as_deref() {
        push("feature_id", f);
    }
    if let Some(p) = project_name.as_deref() {
        push("project_name", p);
    }
    if let Some(l) = limit {
        push("limit", &l.to_string());
    }
    let client = reqwest::Client::new();
    get_json(|| client.get(&url)).await
}

/// GET /ops/records/{id} —— 详情。
#[tauri::command]
pub async fn ops_get_record(
    record_id: String,
    state: State<'_, AppState>,
) -> CmdResult<serde_json::Value> {
    let url = agent_url(
        &state,
        &format!("/ops/records/{}", urlencoding(&record_id)),
    );
    let client = reqwest::Client::new();
    get_json(|| client.get(&url)).await
}

/// DELETE /ops/records/{id} —— 删除记录卡片。
#[tauri::command]
pub async fn ops_delete_record(
    record_id: String,
    state: State<'_, AppState>,
) -> CmdResult<serde_json::Value> {
    let url = agent_url(
        &state,
        &format!("/ops/records/{}", urlencoding(&record_id)),
    );
    let client = reqwest::Client::new();
    let resp = client.delete(&url).send().await.map_err(err)?;
    json_or_err(resp).await
}

/// POST /ops/records/summarize —— AI 根据会话 + 功能点 + Skill 生成总结草稿。
#[tauri::command]
pub async fn ops_summarize_record(
    body: serde_json::Value,
    state: State<'_, AppState>,
) -> CmdResult<serde_json::Value> {
    let url = agent_url(&state, "/ops/records/summarize");
    let client = reqwest::Client::new();
    let resp = client.post(&url).json(&body).send().await.map_err(err)?;
    json_or_err(resp).await
}

// ---------------------------------------------------------------------------
// 专家验收工作流 Case（2026-08-10）
// ---------------------------------------------------------------------------

/// GET /ops/case?project_name=&feature_id= —— 获取 Case（材料文件 + 问答）。
#[tauri::command]
pub async fn ops_case_get(
    project_name: Option<String>,
    feature_id: Option<String>,
    state: State<'_, AppState>,
) -> CmdResult<serde_json::Value> {
    let mut url = agent_url(&state, "/ops/case");
    let mut first = true;
    let mut push = |k: &str, v: &str| {
        if first {
            url.push('?');
            first = false;
        } else {
            url.push('&');
        }
        url.push_str(&format!("{}={}", k, urlencoding(v)));
    };
    if let Some(p) = project_name.as_deref() {
        push("project_name", p);
    }
    if let Some(f) = feature_id.as_deref() {
        push("feature_id", f);
    }
    let client = reqwest::Client::new();
    get_json(|| client.get(&url)).await
}

/// POST /ops/case/files —— 上传材料给对应专家（base64 内容）。
#[tauri::command]
pub async fn ops_case_file_add(
    body: serde_json::Value,
    state: State<'_, AppState>,
) -> CmdResult<serde_json::Value> {
    let url = agent_url(&state, "/ops/case/files");
    let client = reqwest::Client::new();
    let resp = client.post(&url).json(&body).send().await.map_err(err)?;
    json_or_err(resp).await
}

/// DELETE /ops/case —— 清空 Case 重新开始办理（BUGFIX #85）。
#[tauri::command]
pub async fn ops_case_clear(
    project_name: Option<String>,
    feature_id: Option<String>,
    state: State<'_, AppState>,
) -> CmdResult<serde_json::Value> {
    let mut url = agent_url(&state, "/ops/case");
    let mut first = true;
    let mut push = |k: &str, v: &str| {
        if first {
            url.push('?');
            first = false;
        } else {
            url.push('&');
        }
        url.push_str(&format!("{}={}", k, urlencoding(v)));
    };
    if let Some(p) = project_name.as_deref() {
        push("project_name", p);
    }
    if let Some(f) = feature_id.as_deref() {
        push("feature_id", f);
    }
    let client = reqwest::Client::new();
    let resp = client.delete(&url).send().await.map_err(err)?;
    json_or_err(resp).await
}

/// POST /ops/case/files/{id}/review —— AI 专家审核验收。
#[tauri::command]
pub async fn ops_case_file_review(
    file_id: String,
    state: State<'_, AppState>,
) -> CmdResult<serde_json::Value> {
    let url = agent_url(
        &state,
        &format!("/ops/case/files/{}/review", urlencoding(&file_id)),
    );
    let client = reqwest::Client::new();
    // BUGFIX #202：审核是长耗时外部调用，必须有上限 —— 否则 Agent 挂死时
    // 桌面端命令永远不返回，前端只能对着「审核中」干等。
    let resp = client
        .post(&url)
        .timeout(std::time::Duration::from_secs(300))
        .send()
        .await
        .map_err(err)?;
    json_or_err(resp).await
}

/// GET /ops/case/files/{id}/content —— 交付物柜预览（BUGFIX #79，base64 内容）。
#[tauri::command]
pub async fn ops_case_file_content(
    file_id: String,
    state: State<'_, AppState>,
) -> CmdResult<serde_json::Value> {
    let url = agent_url(
        &state,
        &format!("/ops/case/files/{}/content", urlencoding(&file_id)),
    );
    let client = reqwest::Client::new();
    get_json(|| client.get(&url)).await
}

/// POST /ops/case/files/{id}/save-as —— 交付物柜另存（后端直接复制文件）。
#[tauri::command]
pub async fn ops_case_file_save_as(
    file_id: String,
    body: serde_json::Value,
    state: State<'_, AppState>,
) -> CmdResult<serde_json::Value> {
    let url = agent_url(
        &state,
        &format!("/ops/case/files/{}/save-as", urlencoding(&file_id)),
    );
    let client = reqwest::Client::new();
    let resp = client.post(&url).json(&body).send().await.map_err(err)?;
    json_or_err(resp).await
}

/// POST /ops/case/files/{id}/override —— 人工改判。
#[tauri::command]
pub async fn ops_case_file_override(
    file_id: String,
    body: serde_json::Value,
    state: State<'_, AppState>,
) -> CmdResult<serde_json::Value> {
    let url = agent_url(
        &state,
        &format!("/ops/case/files/{}/override", urlencoding(&file_id)),
    );
    let client = reqwest::Client::new();
    let resp = client.post(&url).json(&body).send().await.map_err(err)?;
    json_or_err(resp).await
}

/// DELETE /ops/case/files/{id} —— 删除材料。
#[tauri::command]
pub async fn ops_case_file_delete(
    file_id: String,
    state: State<'_, AppState>,
) -> CmdResult<serde_json::Value> {
    let url = agent_url(
        &state,
        &format!("/ops/case/files/{}", urlencoding(&file_id)),
    );
    let client = reqwest::Client::new();
    let resp = client.delete(&url).send().await.map_err(err)?;
    json_or_err(resp).await
}

/// POST /ops/case/ask —— 向专家提问。
#[tauri::command]
pub async fn ops_case_ask(
    body: serde_json::Value,
    state: State<'_, AppState>,
) -> CmdResult<serde_json::Value> {
    let url = agent_url(&state, "/ops/case/ask");
    let client = reqwest::Client::new();
    let resp = client.post(&url).json(&body).send().await.map_err(err)?;
    json_or_err(resp).await
}

/// PUT /ops/case/drafts/{id} —— 保存交付草稿填写值（BUGFIX #78：界面直填）。
#[tauri::command]
pub async fn ops_case_draft_save(
    draft_id: String,
    body: serde_json::Value,
    state: State<'_, AppState>,
) -> CmdResult<serde_json::Value> {
    let url = agent_url(
        &state,
        &format!("/ops/case/drafts/{}", urlencoding(&draft_id)),
    );
    let client = reqwest::Client::new();
    let resp = client.put(&url).json(&body).send().await.map_err(err)?;
    json_or_err(resp).await
}

/// POST /ops/case/drafts/direct —— 点交付物直开草稿（零 LLM，模板来自专家团 yaml）。
#[tauri::command]
pub async fn ops_case_draft_direct(
    body: serde_json::Value,
    state: State<'_, AppState>,
) -> CmdResult<serde_json::Value> {
    let url = agent_url(&state, "/ops/case/drafts/direct");
    let client = reqwest::Client::new();
    let resp = client
        .post(&url)
        .timeout(std::time::Duration::from_secs(10))
        .json(&body)
        .send()
        .await
        .map_err(err)?;
    json_or_err(resp).await
}

/// POST /ops/case/drafts/{id}/submit —— 提交草稿：自动入材料走专家审核。
#[tauri::command]
pub async fn ops_case_draft_submit(
    draft_id: String,
    state: State<'_, AppState>,
) -> CmdResult<serde_json::Value> {
    let url = agent_url(
        &state,
        &format!("/ops/case/drafts/{}/submit", urlencoding(&draft_id)),
    );
    let client = reqwest::Client::new();
    // BUGFIX #202：提交会同步触发一次审核，必须有上限（略大于后端审核墙钟）
    let resp = client
        .post(&url)
        .timeout(std::time::Duration::from_secs(300))
        .send()
        .await
        .map_err(err)?;
    json_or_err(resp).await
}

/// POST /ops/case/drafts/{id}/ocr-fill —— 上传文件 OCR 识别并抽取字段值。
/// 端侧 OCR + 文档解析 + 模型抽取链路较慢（扫描件可达数十秒）。
///
/// BUGFIX（2026-11-09）：前端红字「ops command failed: error decoding response
/// body」—— 后端其实已 200 完成（agent 访问日志 + audit 均有记录），是响应体
/// 在传完前被中断，与 #207 同源。ops 此前只给幂等 GET 加了重试，POST 全裸奔，
/// 一次抖动即冒红字。
///
/// 用 `send_json_bounded` 而不是 `send_json(.., 5, ..)`：ocr-fill 单请求可跑满
/// 300s，只限次数会把一次卡死放大成 5 × 300s ≈ 25 分钟前端干等。总预算取 300s
/// —— 与改动前的单次超时**等值**，所以慢失败不会比原来更久，只有毫秒级的连接
/// 瞬断才能多拿到 4 次机会。重发的重复副作用由后端
/// `_register_ocr_source_image` 的幂等登记兜住。
#[tauri::command]
pub async fn ops_case_draft_ocr_fill(
    draft_id: String,
    body: serde_json::Value,
    state: State<'_, AppState>,
) -> CmdResult<serde_json::Value> {
    let url = agent_url(
        &state,
        &format!("/ops/case/drafts/{}/ocr-fill", urlencoding(&draft_id)),
    );
    let client = reqwest::Client::new();
    send_json_bounded(
        "ops command failed",
        5,
        std::time::Duration::from_secs(300),
        |remaining| client.post(&url).timeout(remaining).json(&body),
    )
    .await
    .map_err(to_cmd_err)
}

/// POST /ops/case/export —— 全部验收后打包导出交付物 zip 到 target_path。
#[tauri::command]
pub async fn ops_case_export(
    body: serde_json::Value,
    state: State<'_, AppState>,
) -> CmdResult<serde_json::Value> {
    let url = agent_url(&state, "/ops/case/export");
    let client = reqwest::Client::new();
    let resp = client.post(&url).json(&body).send().await.map_err(err)?;
    json_or_err(resp).await
}

/// GET /ops/case/crosscheck?case_id= —— 多文档要素交叉比对（不一致标红，防退件）。
#[tauri::command]
pub async fn ops_case_crosscheck(
    case_id: String,
    state: State<'_, AppState>,
) -> CmdResult<serde_json::Value> {
    let url = agent_url(
        &state,
        &format!("/ops/case/crosscheck?case_id={}", urlencoding(&case_id)),
    );
    let client = reqwest::Client::new();
    get_json(|| client.get(&url)).await
}

// ---------------------------------------------------------------------------
// 业务办理台账（业务汇总，2026-11-09）
// ---------------------------------------------------------------------------

/// GET /ops/tasks —— 业务台账多维查询（状态 / 大类 / 小类 / 日期 / 关键词）。
/// Tauri 命令参数必须平铺（前端按名传参），多维查询维度多 → 豁免参数个数 lint。
#[allow(clippy::too_many_arguments)]
#[tauri::command]
pub async fn ops_task_list(
    status: Option<String>,
    category: Option<String>,
    feature_id: Option<String>,
    project_name: Option<String>,
    case_id: Option<String>,
    date_from: Option<i64>,
    date_to: Option<i64>,
    keyword: Option<String>,
    limit: Option<u32>,
    state: State<'_, AppState>,
) -> CmdResult<serde_json::Value> {
    let mut url = agent_url(&state, "/ops/tasks");
    let mut first = true;
    let mut push = |k: &str, v: &str| {
        if first {
            url.push('?');
            first = false;
        } else {
            url.push('&');
        }
        url.push_str(&format!("{}={}", k, urlencoding(v)));
    };
    if let Some(v) = status.as_deref() {
        push("status", v);
    }
    if let Some(v) = category.as_deref() {
        push("category", v);
    }
    if let Some(v) = feature_id.as_deref() {
        push("feature_id", v);
    }
    if let Some(v) = project_name.as_deref() {
        push("project_name", v);
    }
    if let Some(v) = case_id.as_deref() {
        push("case_id", v);
    }
    if let Some(v) = date_from {
        push("date_from", &v.to_string());
    }
    if let Some(v) = date_to {
        push("date_to", &v.to_string());
    }
    if let Some(v) = keyword.as_deref() {
        push("keyword", v);
    }
    if let Some(v) = limit {
        push("limit", &v.to_string());
    }
    let client = reqwest::Client::new();
    get_json(|| client.get(&url)).await
}

/// GET /ops/tasks/{id} —— 台账详情（待处理项 + 材料进度）。
#[tauri::command]
pub async fn ops_task_get(
    task_id: String,
    state: State<'_, AppState>,
) -> CmdResult<serde_json::Value> {
    let url = agent_url(
        &state,
        &format!("/ops/tasks/{}", urlencoding(&task_id)),
    );
    let client = reqwest::Client::new();
    get_json(|| client.get(&url)).await
}

/// POST /ops/tasks/{id}/status —— 人工挂起 / 恢复办理。
#[tauri::command]
pub async fn ops_task_status(
    task_id: String,
    body: serde_json::Value,
    state: State<'_, AppState>,
) -> CmdResult<serde_json::Value> {
    let url = agent_url(
        &state,
        &format!("/ops/tasks/{}/status", urlencoding(&task_id)),
    );
    let client = reqwest::Client::new();
    let resp = client.post(&url).json(&body).send().await.map_err(err)?;
    json_or_err(resp).await
}

/// POST /ops/tasks/start —— 业务发起建档（幂等）。
#[tauri::command]
pub async fn ops_task_start(
    body: serde_json::Value,
    state: State<'_, AppState>,
) -> CmdResult<serde_json::Value> {
    let url = agent_url(&state, "/ops/tasks/start");
    let client = reqwest::Client::new();
    let resp = client.post(&url).json(&body).send().await.map_err(err)?;
    json_or_err(resp).await
}

// ---------------------------------------------------------------------------
// 凭证预览（2026-11-09）：凭证节点聚合 · 归类 · 影像识别
// ---------------------------------------------------------------------------

/// GET /ops/case/credentials?case_id=&team_ids= —— 凭证预览聚合。
#[tauri::command]
pub async fn ops_case_credentials(
    case_id: String,
    team_ids: Option<String>,
    state: State<'_, AppState>,
) -> CmdResult<serde_json::Value> {
    let mut url = agent_url(
        &state,
        &format!("/ops/case/credentials?case_id={}", urlencoding(&case_id)),
    );
    if let Some(t) = team_ids.as_deref() {
        if !t.is_empty() {
            url.push_str(&format!("&team_ids={}", urlencoding(t)));
        }
    }
    let client = reqwest::Client::new();
    get_json(|| client.get(&url)).await
}

/// POST /ops/case/classify-credentials —— 做一次智能归类并自动挂载。
/// 未命中关键词时会走一次 LLM 批量判定，超时放宽到 120s。
#[tauri::command]
pub async fn ops_case_classify_credentials(
    body: serde_json::Value,
    state: State<'_, AppState>,
) -> CmdResult<serde_json::Value> {
    let url = agent_url(&state, "/ops/case/classify-credentials");
    let client = reqwest::Client::new();
    let resp = client
        .post(&url)
        .timeout(std::time::Duration::from_secs(120))
        .json(&body)
        .send()
        .await
        .map_err(err)?;
    json_or_err(resp).await
}

/// GET /ops/case/credential-asset/{id} —— 影像字节 + 识别块坐标（一次拿全）。
/// 扫描件可达十几 MB，读文件耗时短但传输大，超时给 120s。
#[tauri::command]
pub async fn ops_credential_asset_get(
    asset_id: String,
    state: State<'_, AppState>,
) -> CmdResult<serde_json::Value> {
    let url = agent_url(
        &state,
        &format!("/ops/case/credential-asset/{}", urlencoding(&asset_id)),
    );
    let client = reqwest::Client::new();
    let resp = client
        .get(&url)
        .timeout(std::time::Duration::from_secs(120))
        .send()
        .await
        .map_err(err)?;
    json_or_err(resp).await
}

/// PUT /ops/case/credential-asset/{id} —— 手动改归属（面板拖动 / 下拉）。
#[tauri::command]
pub async fn ops_credential_asset_assign(
    asset_id: String,
    body: serde_json::Value,
    state: State<'_, AppState>,
) -> CmdResult<serde_json::Value> {
    let url = agent_url(
        &state,
        &format!("/ops/case/credential-asset/{}", urlencoding(&asset_id)),
    );
    let client = reqwest::Client::new();
    let resp = client.put(&url).json(&body).send().await.map_err(err)?;
    json_or_err(resp).await
}

/// POST /ops/case/credential-asset/{id}/recognize —— 「立即识别影像」。
/// RapidOCR 冷启动 + 逐行识别可遮数十秒，超时与 OCR 填入同样给 300s。
#[tauri::command]
pub async fn ops_credential_asset_recognize(
    asset_id: String,
    state: State<'_, AppState>,
) -> CmdResult<serde_json::Value> {
    let url = agent_url(
        &state,
        &format!(
            "/ops/case/credential-asset/{}/recognize",
            urlencoding(&asset_id)
        ),
    );
    let client = reqwest::Client::new();
    let resp = client
        .post(&url)
        .timeout(std::time::Duration::from_secs(300))
        .send()
        .await
        .map_err(err)?;
    json_or_err(resp).await
}

/// DELETE /ops/case/credential-asset/{id} —— 删除凭证资产（材料或识别底图）。
#[tauri::command]
pub async fn ops_credential_asset_delete(
    asset_id: String,
    state: State<'_, AppState>,
) -> CmdResult<serde_json::Value> {
    let url = agent_url(
        &state,
        &format!("/ops/case/credential-asset/{}", urlencoding(&asset_id)),
    );
    let client = reqwest::Client::new();
    let resp = client.delete(&url).send().await.map_err(err)?;
    json_or_err(resp).await
}
