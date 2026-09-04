//! 设置页「一键导出全部日志」—— BUGFIX #193（2026-09-04）。
//!
//! 背景：macOS 上 Rust 侧日志因 .app 包只读而静默丢失（已修 get_app_data_dir），
//! 但用户排查问题时仍需要把分散在两路（Rust eaide.log/crash.log + Python
//! agent.log/cot.log/orchestrator-*.jsonl）的日志一次性打包发给支持人员。
//!
//! 设计：
//! - 数据根 = `get_app_data_dir()`（macOS 修复后与 Python 子进程 cwd 对齐）。
//! - 收集 `<data>/logs/` 下所有 `.log` / `.jsonl` 文件 + 数据根下的 `db_mcp.log`。
//! - 打包成 zip，内部按 `rust/` `python/` `other/` 分目录，附 `MANIFEST.txt`。
//! - 单文件读取失败不阻断整体导出（best-effort），失败项记入 manifest。
//! - 不导出任何配置文件（environments.json / llm-config.json 可能含密钥）。

use std::fs::File;
use std::io::{BufWriter, Read, Write};
use std::path::{Path, PathBuf};

use serde::Serialize;
use zip::write::SimpleFileOptions;
use zip::ZipWriter;

use crate::agent_manager::{get_app_data_dir, get_log_dir};

/// Tauri command 统一返回类型（与 router.rs 等保持一致：Err 走 String）。
type CmdResult<T> = Result<T, String>;

/// 导出结果元数据（返回给前端展示）。
#[derive(Serialize)]
pub struct ExportLogsResult {
    pub ok: bool,
    /// 用户选定的 zip 落盘路径
    pub path: String,
    /// 成功打包的文件数
    pub file_count: usize,
    /// zip 总字节数
    pub total_bytes: u64,
    /// 每个被打包文件的明细
    pub files: Vec<ExportedFile>,
    /// 期望但缺失/读取失败的来源（不影响 ok=true）
    pub missing: Vec<String>,
    /// 数据根目录（供用户核对）
    pub data_dir: String,
}

#[derive(Serialize)]
pub struct ExportedFile {
    /// zip 内相对路径（如 `python/agent.log`）
    pub name: String,
    /// 原始绝对路径
    pub source: String,
    /// 字节数
    pub size: u64,
}

/// 一键导出全部日志到用户选定的 zip 路径。
///
/// 前端流程：`save()` 对话框拿到路径 → `invoke("export_all_logs", { destPath })`。
#[tauri::command]
pub async fn export_all_logs(dest_path: String) -> CmdResult<ExportLogsResult> {
    // zip 创建是阻塞 IO，放 spawn_blocking 避免卡住 async runtime
    tokio::task::spawn_blocking(move || export_all_logs_sync(&dest_path))
        .await
        .map_err(|e| format!("export task join failed: {}", e))?
}

fn export_all_logs_sync(dest_path: &str) -> CmdResult<ExportLogsResult> {
    let data_dir = get_app_data_dir();
    let log_dir = get_log_dir();

    // ---- 1. 收集候选日志文件 -------------------------------------------
    // (zip 内相对路径, 磁盘绝对路径, 分类标签)
    let mut candidates: Vec<(String, PathBuf, &'static str)> = Vec::new();

    // Rust 侧日志（get_log_dir 下固定文件名）
    for name in ["eaide.log", "crash.log"] {
        let p = log_dir.join(name);
        candidates.push((format!("rust/{}", name), p, "rust"));
    }

    // Python 侧日志（同一 logs/ 目录，子进程 cwd = data_dir）
    for name in ["agent.log", "cot.log"] {
        let p = log_dir.join(name);
        candidates.push((format!("python/{}", name), p, "python"));
    }

    // orchestrator-YYYYMMDD.jsonl（结构化事件日志，可能多个）
    if let Ok(entries) = std::fs::read_dir(&log_dir) {
        for entry in entries.flatten() {
            let p = entry.path();
            if p.is_file() {
                if let Some(fname) = p.file_name().and_then(|s| s.to_str()) {
                    if fname.starts_with("orchestrator-") && fname.ends_with(".jsonl") {
                        candidates.push((format!("python/{}", fname), p, "python"));
                    }
                }
            }
        }
    }

    // 数据根下的散落日志（db_mcp.log 等）
    if let Ok(entries) = std::fs::read_dir(&data_dir) {
        for entry in entries.flatten() {
            let p = entry.path();
            if p.is_file() {
                if let Some(fname) = p.file_name().and_then(|s| s.to_str()) {
                    if fname.ends_with(".log") && fname != "eaide.log" && fname != "crash.log" {
                        candidates.push((format!("other/{}", fname), p, "other"));
                    }
                }
            }
        }
    }

    // ---- 2. 创建 zip ---------------------------------------------------
    let dest = Path::new(dest_path);
    if let Some(parent) = dest.parent() {
        std::fs::create_dir_all(parent)
            .map_err(|e| format!("create parent dir failed: {}", e))?;
    }
    let file = File::create(dest)
        .map_err(|e| format!("create zip file failed: {}", e))?;
    let mut zip = ZipWriter::new(BufWriter::new(file));
    let opts: SimpleFileOptions = SimpleFileOptions::default()
        .compression_method(zip::CompressionMethod::Deflated);

    let mut exported: Vec<ExportedFile> = Vec::new();
    let mut missing: Vec<String> = Vec::new();
    let mut manifest_lines: Vec<String> = Vec::new();

    manifest_lines.push("EAIDE 日志导出清单".to_string());
    manifest_lines.push(format!("导出时间: {}", chrono::Local::now().format("%Y-%m-%d %H:%M:%S %z")));
    manifest_lines.push(format!("平台: {} {}", std::env::consts::OS, std::env::consts::ARCH));
    manifest_lines.push(format!("数据根: {}", data_dir.display()));
    manifest_lines.push(format!("日志目录: {}", log_dir.display()));
    manifest_lines.push(String::new());
    manifest_lines.push("---- 已打包文件 ----".to_string());

    for (zip_name, src_path, _category) in &candidates {
        if !src_path.exists() {
            missing.push(src_path.display().to_string());
            manifest_lines.push(format!("[MISSING] {} (期望路径: {})", zip_name, src_path.display()));
            continue;
        }
        let size = std::fs::metadata(src_path).map(|m| m.len()).unwrap_or(0);
        match File::open(src_path) {
            Ok(mut f) => {
                // 读入内存再写 zip（日志文件通常 < 50MB，可接受；
                // 若未来超大可改 io::copy 流式，但 zip crate 要求 Seek）
                let mut buf = Vec::with_capacity(size as usize);
                if let Err(e) = f.read_to_end(&mut buf) {
                    missing.push(format!("{} (read error: {})", src_path.display(), e));
                    manifest_lines.push(format!("[READ-ERR] {} ({})", zip_name, e));
                    continue;
                }
                if let Err(e) = zip.start_file(zip_name.clone(), opts) {
                    missing.push(format!("{} (zip start_file error: {})", zip_name, e));
                    manifest_lines.push(format!("[ZIP-ERR] {} ({})", zip_name, e));
                    continue;
                }
                if let Err(e) = zip.write_all(&buf) {
                    missing.push(format!("{} (zip write error: {})", zip_name, e));
                    manifest_lines.push(format!("[ZIP-ERR] {} ({})", zip_name, e));
                    continue;
                }
                exported.push(ExportedFile {
                    name: zip_name.clone(),
                    source: src_path.display().to_string(),
                    size: buf.len() as u64,
                });
                manifest_lines.push(format!("[OK] {} ({} bytes) <- {}", zip_name, buf.len(), src_path.display()));
            }
            Err(e) => {
                missing.push(format!("{} (open error: {})", src_path.display(), e));
                manifest_lines.push(format!("[OPEN-ERR] {} ({})", zip_name, e));
            }
        }
    }

    manifest_lines.push(String::new());
    manifest_lines.push("---- 汇总 ----".to_string());
    manifest_lines.push(format!("成功打包: {} 个文件", exported.len()));
    manifest_lines.push(format!("缺失/失败: {} 个来源", missing.len()));

    // 写入 MANIFEST.txt
    let manifest = manifest_lines.join("\n");
    zip.start_file("MANIFEST.txt", opts)
        .map_err(|e| format!("zip manifest start failed: {}", e))?;
    zip.write_all(manifest.as_bytes())
        .map_err(|e| format!("zip manifest write failed: {}", e))?;

    let mut finished = zip
        .finish()
        .map_err(|e| format!("zip finish failed: {}", e))?;
    finished
        .flush()
        .map_err(|e| format!("zip flush failed: {}", e))?;
    drop(finished);

    let total_bytes = std::fs::metadata(dest).map(|m| m.len()).unwrap_or(0);
    let file_count = exported.len();

    Ok(ExportLogsResult {
        ok: true,
        path: dest_path.to_string(),
        file_count,
        total_bytes,
        files: exported,
        missing,
        data_dir: data_dir.display().to_string(),
    })
}

#[cfg(test)]
mod tests {
    use super::*;

    /// 验证候选收集逻辑不 panic（真实路径依赖运行环境，单测只跑结构性断言）。
    #[test]
    fn test_export_creates_zip_with_manifest() {
        // 用临时目录做 dest，数据根用 get_app_data_dir()（开发机 = exe 父目录，
        // 至少 logs/ 子目录存在；即使全 missing 也应产出含 MANIFEST.txt 的 zip）。
        let tmp = std::env::temp_dir().join(format!("eaide-export-test-{}.zip", std::process::id()));
        let dest = tmp.to_string_lossy().into_owned();
        let result = export_all_logs_sync(&dest).expect("export should succeed structurally");
        assert!(result.ok);
        assert!(Path::new(&dest).exists(), "zip file should be created");
        assert!(result.total_bytes > 0, "zip should not be empty");

        // 校验 zip 内含 MANIFEST.txt
        let f = File::open(&dest).unwrap();
        let mut archive = zip::ZipArchive::new(f).unwrap();
        let mut found_manifest = false;
        for i in 0..archive.len() {
            if let Ok(file) = archive.by_index(i) {
                if file.name() == "MANIFEST.txt" {
                    found_manifest = true;
                }
            }
        }
        assert!(found_manifest, "zip must contain MANIFEST.txt");

        let _ = std::fs::remove_file(&dest);
    }
}
