//! 文件树右键编译命令（2026-08-19 用户要求）。
//!
//! 能力：
//!   - `compile_files`：接收一组文件 / 目录（目录递归展开），按扩展名分组，
//!     `.java → javac`（.class 输出到指定目录）、`.py → py_compile`（语法编译）、
//!     `.c/.cpp → gcc/g++ -c`（.o 输出到指定目录）。
//!   - `compile_config_get / compile_config_save`：编译配置持久化
//!     （安装目录 compile.json；编译器目录手动选择，留空自动探测 PATH）。
//!
//! 输出目录解析由前端完成（配置目录 → workspace → 兜底），Rust 侧收到空串时
//! 兜底到 `安装目录/workspace/compiled`。
//!
//! 安全说明：本命令由用户在文件树显式触发（非 Agent 自主行为），编译器路径
//! 由用户在设置页手动选择，不走 HITL 审批闸门（与 list_dir_entries 同级 UI 命令）。

use std::path::{Path, PathBuf};
use std::process::Command;

use serde::{Deserialize, Serialize};

use crate::agent_manager::get_app_data_dir;

/// 编译配置（设置页「编译配置」面板维护）。
#[derive(Debug, Clone, Default, PartialEq, Serialize, Deserialize)]
pub struct CompileConfig {
    /// JDK bin 目录或 javac 可执行文件路径；空 = 自动探测 PATH。
    #[serde(default)]
    pub javac_dir: String,
    /// Python 目录或解释器路径；空 = 自动探测 PATH。
    #[serde(default)]
    pub python_dir: String,
    /// gcc/g++ 目录或可执行文件路径；空 = 自动探测 PATH。
    #[serde(default)]
    pub gcc_dir: String,
    /// 编译产物输出目录；空 = workspace（前端解析后传入）。
    #[serde(default)]
    pub output_dir: String,
}

/// 待编译条目（文件 / 目录）。
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct CompileItem {
    pub path: String,
    pub is_dir: bool,
}

/// 单文件编译结果。
#[derive(Debug, Clone, Serialize)]
pub struct CompileEntry {
    pub path: String,
    pub ok: bool,
    pub message: String,
}

/// 编译汇总报告（前端弹窗展示）。
#[derive(Debug, Clone, Serialize)]
pub struct CompileReport {
    pub output_dir: String,
    pub total: usize,
    pub ok_count: usize,
    pub failed_count: usize,
    /// 源文件数超过单次上限时是否被截断。
    pub truncated: bool,
    pub entries: Vec<CompileEntry>,
    /// 实际执行的编译器命令摘要（便于用户在自己的终端复现）。
    pub commands: Vec<String>,
}

/// 递归展开时跳过的噪音目录。
const IGNORED_DIRS: &[&str] = &[
    "node_modules", ".git", "target", "dist", "build", "out", "bin", "obj",
    "__pycache__", ".venv", ".idea", ".vs",
];

/// 单次编译最多收录的源文件数（防巨型目录卡死）。
const MAX_SOURCE_FILES: usize = 2000;
/// 报告 entries 上限（防 payload 过大）。
const MAX_REPORT_ENTRIES: usize = 200;
/// 单条 stderr 摘要截断长度（javac 错误详情要留够，中文报错靠它定位）。
const MAX_ERR_LEN: usize = 1200;
/// 编译器超时（秒）。
const COMPILE_TIMEOUT_HINT: &str = "编译超时或编译器异常退出";

// ---------- 配置持久化 ----------

fn config_path() -> PathBuf {
    get_app_data_dir().join("compile.json")
}

fn load_config_from(path: &Path) -> CompileConfig {
    std::fs::read_to_string(path)
        .ok()
        .and_then(|text| serde_json::from_str(&text).ok())
        .unwrap_or_default()
}

fn save_config_to(path: &Path, config: &CompileConfig) -> Result<(), String> {
    if let Some(parent) = path.parent() {
        std::fs::create_dir_all(parent).map_err(|e| format!("创建配置目录失败：{e}"))?;
    }
    let text = serde_json::to_string_pretty(config).map_err(|e| format!("序列化失败：{e}"))?;
    std::fs::write(path, text).map_err(|e| format!("写入 compile.json 失败：{e}"))
}

#[tauri::command]
pub fn compile_config_get() -> CompileConfig {
    load_config_from(&config_path())
}

#[tauri::command]
pub fn compile_config_save(config: CompileConfig) -> Result<CompileConfig, String> {
    save_config_to(&config_path(), &config)?;
    Ok(config)
}

// ---------- 编译器解析 ----------

#[cfg(target_os = "windows")]
fn with_exe(name: &str) -> String {
    if name.ends_with(".exe") || name.ends_with(".cmd") || name.ends_with(".bat") {
        name.to_string()
    } else {
        format!("{name}.exe")
    }
}

#[cfg(not(target_os = "windows"))]
fn with_exe(name: &str) -> String {
    name.to_string()
}

/// 从用户配置解析编译器：配置值是文件 → 直接用；是目录 → 拼可执行文件名。
fn resolve_from_config(configured: &str, exe_base: &str) -> Option<PathBuf> {
    let trimmed = configured.trim();
    if trimmed.is_empty() {
        return None;
    }
    let p = PathBuf::from(trimmed);
    if p.is_file() {
        return Some(p);
    }
    if p.is_dir() {
        let candidate = p.join(with_exe(exe_base));
        if candidate.is_file() {
            return Some(candidate);
        }
    }
    None
}

/// PATH 探测（纯 std，不 spawn `where`）。
fn find_on_path(exe_base: &str) -> Option<PathBuf> {
    let path_var = std::env::var_os("PATH")?;
    let target = with_exe(exe_base);
    for dir in std::env::split_paths(&path_var) {
        if dir.as_os_str().is_empty() {
            continue;
        }
        let candidate = dir.join(&target);
        if candidate.is_file() {
            return Some(candidate);
        }
    }
    None
}

/// 配置目录 → PATH，两级解析。
fn resolve_compiler(configured: &str, exe_base: &str) -> Option<PathBuf> {
    resolve_from_config(configured, exe_base).or_else(|| find_on_path(exe_base))
}

// ---------- 源文件收集 ----------

/// 判断扩展名是否为受支持的源码。
fn is_supported_source(path: &Path) -> bool {
    matches!(
        path.extension().and_then(|e| e.to_str()).map(|e| e.to_ascii_lowercase()).as_deref(),
        Some("java" | "py" | "c" | "cpp" | "cc" | "cxx")
    )
}

fn collect_sources_rec(dir: &Path, out: &mut Vec<PathBuf>, truncated: &mut bool) {
    let Ok(rd) = std::fs::read_dir(dir) else { return };
    let mut entries: Vec<_> = rd.filter_map(|e| e.ok()).collect();
    entries.sort_by_key(|e| e.file_name().to_ascii_lowercase());
    for entry in entries {
        if out.len() >= MAX_SOURCE_FILES {
            *truncated = true;
            return;
        }
        let path = entry.path();
        let is_dir = entry.file_type().map(|t| t.is_dir()).unwrap_or(false);
        if is_dir {
            let name = entry.file_name().to_string_lossy().to_string();
            if IGNORED_DIRS.contains(&name.as_str()) {
                continue;
            }
            collect_sources_rec(&path, out, truncated);
        } else if is_supported_source(&path) {
            out.push(path);
        }
    }
}

/// 展开 items：文件直接收录，目录递归收集（跳过噪音目录）。
fn collect_sources(items: &[CompileItem]) -> (Vec<PathBuf>, bool, Vec<String>) {
    let mut files: Vec<PathBuf> = Vec::new();
    let mut truncated = false;
    let mut errors: Vec<String> = Vec::new();
    for item in items {
        let p = PathBuf::from(&item.path);
        if !p.exists() {
            errors.push(format!("路径不存在：{}", item.path));
            continue;
        }
        if item.is_dir && p.is_dir() {
            collect_sources_rec(&p, &mut files, &mut truncated);
        } else if p.is_file() {
            if is_supported_source(&p) {
                files.push(p);
            } else {
                errors.push(format!("不支持的文件类型：{}", item.path));
            }
        } else {
            errors.push(format!("路径类型与声明不符：{}", item.path));
        }
    }
    files.sort();
    files.dedup();
    (files, truncated, errors)
}

// ---------- Java：sourcepath / classpath 推导 ----------

/// 推导 Java 源码根：路径含 src/main/java → 该段；否则最深的 src 段；否则父目录。
fn java_source_root(file: &Path) -> PathBuf {
    let components: Vec<_> = file.components().collect();
    let names: Vec<String> = components
        .iter()
        .map(|c| c.as_os_str().to_string_lossy().to_string())
        .collect();
    for i in 0..names.len() {
        if names[i] == "src"
            && names.get(i + 1).map(String::as_str) == Some("main")
            && names.get(i + 2).map(String::as_str) == Some("java")
        {
            return components[..i + 3].iter().collect::<PathBuf>();
        }
    }
    for (i, n) in names.iter().enumerate().rev() {
        if *n == "src" {
            return components[..i + 1].iter().collect::<PathBuf>();
        }
    }
    file.parent().map(Path::to_path_buf).unwrap_or_else(|| file.to_path_buf())
}

/// 从源码根向上找 Maven 模块的 target/classes（最多 4 层：src/main/java 需上 3 层到模块根）。
fn maven_target_classes(source_root: &Path) -> Option<PathBuf> {
    let mut cur = source_root.to_path_buf();
    for _ in 0..4 {
        let candidate = cur.join("target").join("classes");
        if candidate.is_dir() {
            return Some(candidate);
        }
        cur = cur.parent()?.to_path_buf();
    }
    None
}

/// 从源码根向上找项目根：含 pom.xml/build.gradle 的最近祖先（最多 6 层）。
/// 找到后用 maven_project_classpath 汇总全项目产物，解决多模块互相引用。
fn find_project_root(source_root: &Path) -> Option<PathBuf> {
    let mut cur = source_root.to_path_buf();
    for _ in 0..6 {
        if cur.join("pom.xml").is_file() || cur.join("build.gradle").is_file() {
            return Some(cur);
        }
        cur = cur.parent()?.to_path_buf();
    }
    None
}

/// 项目级 classpath 收集：递归（限深 4 层）找所有 target/classes 目录，
/// 并把其中的 .jar（Maven dependency 插件落盘）一并收录；跳过 .git/node_modules。
fn maven_project_classpath(project_root: &Path) -> Vec<PathBuf> {
    let mut out: Vec<PathBuf> = Vec::new();
    fn walk(dir: &Path, depth: u32, out: &mut Vec<PathBuf>) {
        if depth > 4 {
            return;
        }
        let classes = dir.join("target").join("classes");
        if classes.is_dir() {
            out.push(classes.clone());
            // target/classes 里可能放着 mvn dependency:copy-dependencies 拷的依赖 jar
            if let Ok(rd) = std::fs::read_dir(&classes) {
                for e in rd.flatten() {
                    let p = e.path();
                    if p.is_file() && p.extension().map(|x| x == "jar").unwrap_or(false) {
                        out.push(p);
                    }
                }
            }
        }
        let Ok(rd) = std::fs::read_dir(dir) else { return };
        for e in rd.flatten() {
            let p = e.path();
            if !p.is_dir() {
                continue;
            }
            let name = e.file_name().to_string_lossy().to_string();
            if matches!(name.as_str(), ".git" | "node_modules" | "target" | ".idea") {
                continue;
            }
            walk(&p, depth + 1, out);
        }
    }
    walk(project_root, 0, &mut out);
    out.sort();
    out.dedup();
    out
}

// ---------- 编译执行 ----------

fn truncate_msg(msg: String) -> String {
    let trimmed = msg.trim().to_string();
    if trimmed.len() <= MAX_ERR_LEN {
        trimmed
    } else {
        format!("{}…", &trimmed[..MAX_ERR_LEN])
    }
}

/// 编译器输出解码：优先 UTF-8；非法（中文 Windows 上 javac 输出是 GBK）回落系统 ANSI 代码页。
fn decode_compiler_output(bytes: &[u8]) -> String {
    match std::str::from_utf8(bytes) {
        Ok(s) => s.to_string(),
        Err(_) => {
            #[cfg(target_os = "windows")]
            {
                // CP_ACP —— 中文 Windows 即 GBK/cp936，javac 错误信息的实际编码
                if let Some(enc) = windows_ansi() {
                    return enc.decode(bytes).0.into_owned();
                }
            }
            String::from_utf8_lossy(bytes).into_owned()
        }
    }
}

#[cfg(target_os = "windows")]
fn windows_ansi() -> Option<&'static encoding_rs::Encoding> {
    // 通过系统代码页找 encoding_rs 对应编码器（936=GBK / 950 / 932 等）
    let cp = unsafe { windows_codepage() };
    encoding_rs::Encoding::for_label(
        match cp {
            936 => "gbk",
            950 => "big5",
            932 => "shift_jis",
            949 => "euc-kr",
            _ => return None,
        }
        .as_bytes(),
    )
}

#[cfg(target_os = "windows")]
unsafe fn windows_codepage() -> u32 {
    unsafe extern "system" {
        fn GetACP() -> u32;
    }
    unsafe { GetACP() }
}

fn run_compiler(cmd: &mut Command) -> Result<(i32, String), String> {
    let output = cmd
        .output()
        .map_err(|e| format!("启动编译器失败：{e}"))?;
    let stdout = decode_compiler_output(&output.stdout);
    let stderr = decode_compiler_output(&output.stderr);
    let combined = if stderr.trim().is_empty() { stdout } else { stderr };
    Ok((output.status.code().unwrap_or(-1), combined))
}

fn default_output_dir() -> PathBuf {
    get_app_data_dir().join("workspace").join("compiled")
}

#[tauri::command]
pub async fn compile_files(
    items: Vec<CompileItem>,
    output_dir: String,
) -> Result<CompileReport, String> {
    if items.is_empty() {
        return Err("没有可编译的文件 / 目录".into());
    }
    let config = load_config_from(&config_path());

    // 输出目录：前端传入优先；空 → 安装目录/workspace/compiled 兜底
    let out_dir = if output_dir.trim().is_empty() {
        default_output_dir()
    } else {
        PathBuf::from(output_dir.trim())
    };
    std::fs::create_dir_all(&out_dir)
        .map_err(|e| format!("创建输出目录失败（{}）：{e}", out_dir.display()))?;

    let (files, truncated, mut pre_errors) = collect_sources(&items);
    let mut report = CompileReport {
        output_dir: out_dir.display().to_string(),
        total: files.len(),
        ok_count: 0,
        failed_count: 0,
        truncated,
        entries: Vec::new(),
        commands: Vec::new(),
    };
    for msg in pre_errors.drain(..) {
        report.entries.push(CompileEntry { path: String::new(), ok: false, message: msg });
    }

    let java_files: Vec<&PathBuf> = files.iter().filter(|f| f.extension().map(|e| e == "java").unwrap_or(false)).collect();
    let py_files: Vec<&PathBuf> = files.iter().filter(|f| f.extension().map(|e| e == "py").unwrap_or(false)).collect();
    let c_files: Vec<&PathBuf> = files.iter().filter(|f| {
        matches!(f.extension().and_then(|e| e.to_str()).map(|e| e.to_ascii_lowercase()).as_deref(), Some("c" | "cpp" | "cc" | "cxx"))
    }).collect();

    // ---- Java：javac -encoding UTF-8 -d <out> -sourcepath ... -cp ... ----
    if !java_files.is_empty() {
        match resolve_compiler(&config.javac_dir, "javac") {
            Some(javac) => {
                let mut source_roots: Vec<PathBuf> = java_files.iter().map(|f| java_source_root(f)).collect();
                source_roots.sort();
                source_roots.dedup();
                // classpath：输出目录 + 项目内所有模块的 target/classes（含依赖 jar）。
                // 项目根 = 源码根向上最近的 pom.xml/build.gradle 祖先；多模块工程互引不再报「找不到符号」。
                let mut cp_entries: Vec<PathBuf> = vec![out_dir.clone()];
                let mut project_roots: Vec<PathBuf> = Vec::new();
                for root in &source_roots {
                    if let Some(pr) = find_project_root(root) {
                        if !project_roots.contains(&pr) {
                            project_roots.push(pr);
                        }
                    } else if let Some(tc) = maven_target_classes(root) {
                        cp_entries.push(tc);
                    }
                }
                for pr in &project_roots {
                    cp_entries.extend(maven_project_classpath(pr));
                }
                cp_entries.sort();
                cp_entries.dedup();

                let sourcepath = std::env::join_paths(&source_roots)
                    .map_err(|e| format!("sourcepath 拼接失败：{e}"))?;
                let classpath = std::env::join_paths(&cp_entries)
                    .map_err(|e| format!("classpath 拼接失败：{e}"))?;

                let mut cmd = Command::new(&javac);
                cmd.arg("-encoding").arg("UTF-8")
                    .arg("-d").arg(&out_dir)
                    .arg("-sourcepath").arg(&sourcepath)
                    .arg("-cp").arg(&classpath);
                for f in &java_files {
                    cmd.arg(f);
                }
                report.commands.push(format!(
                    "{} -encoding UTF-8 -d {} -sourcepath {} -cp {} （{} 个 .java）",
                    javac.display(), out_dir.display(),
                    sourcepath.to_string_lossy(), classpath.to_string_lossy(),
                    java_files.len()
                ));

                match run_compiler(&mut cmd) {
                    Ok((0, _)) => {
                        report.ok_count += java_files.len();
                        for f in &java_files {
                            report.entries.push(CompileEntry {
                                path: f.display().to_string(), ok: true,
                                message: ".class 已输出".into(),
                            });
                        }
                    }
                    Ok((code, output_text)) => {
                        report.failed_count += java_files.len();
                        let msg = truncate_msg(format!(
                            "javac 退出码 {code}：{output_text}\n（提示：依赖其他模块类时请先 mvn compile 生成 target/classes）"
                        ));
                        for f in &java_files {
                            report.entries.push(CompileEntry {
                                path: f.display().to_string(), ok: false, message: msg.clone(),
                            });
                        }
                    }
                    Err(e) => {
                        report.failed_count += java_files.len();
                        let msg = format!("{COMPILE_TIMEOUT_HINT}：{e}");
                        for f in &java_files {
                            report.entries.push(CompileEntry {
                                path: f.display().to_string(), ok: false, message: msg.clone(),
                            });
                        }
                    }
                }
            }
            None => {
                report.failed_count += java_files.len();
                let msg = "未找到 javac：请在 设置 → 编译配置 手动选择 JDK 的 bin 目录（或确认 PATH 含 javac）".to_string();
                for f in &java_files {
                    report.entries.push(CompileEntry {
                        path: f.display().to_string(), ok: false, message: msg.clone(),
                    });
                }
            }
        }
    }

    // ---- Python：py_compile 语法编译（字节码落源码旁 __pycache__）----
    if !py_files.is_empty() {
        match resolve_compiler(&config.python_dir, "python") {
            Some(py) => {
                let mut cmd = Command::new(&py);
                cmd.arg("-m").arg("py_compile");
                for f in &py_files {
                    cmd.arg(f);
                }
                report.commands.push(format!(
                    "{} -m py_compile （{} 个 .py）", py.display(), py_files.len()
                ));
                match run_compiler(&mut cmd) {
                    Ok((0, _)) => {
                        report.ok_count += py_files.len();
                        for f in &py_files {
                            report.entries.push(CompileEntry {
                                path: f.display().to_string(), ok: true,
                                message: "语法编译通过（.pyc 落源码旁 __pycache__）".into(),
                            });
                        }
                    }
                    Ok((code, output_text)) => {
                        report.failed_count += py_files.len();
                        let msg = truncate_msg(format!("python 退出码 {code}：{output_text}"));
                        for f in &py_files {
                            report.entries.push(CompileEntry {
                                path: f.display().to_string(), ok: false, message: msg.clone(),
                            });
                        }
                    }
                    Err(e) => {
                        report.failed_count += py_files.len();
                        let msg = format!("{COMPILE_TIMEOUT_HINT}：{e}");
                        for f in &py_files {
                            report.entries.push(CompileEntry {
                                path: f.display().to_string(), ok: false, message: msg.clone(),
                            });
                        }
                    }
                }
            }
            None => {
                report.failed_count += py_files.len();
                let msg = "未找到 python：请在 设置 → 编译配置 手动选择 Python 目录（或确认 PATH 含 python）".to_string();
                for f in &py_files {
                    report.entries.push(CompileEntry {
                        path: f.display().to_string(), ok: false, message: msg.clone(),
                    });
                }
            }
        }
    }

    // ---- C / C++：gcc / g++ -c -o <out>/<name>.o ----
    if !c_files.is_empty() {
        for f in &c_files {
            let ext = f.extension().and_then(|e| e.to_str()).map(|e| e.to_ascii_lowercase()).unwrap_or_default();
            let base = if ext == "c" { "gcc" } else { "g++" };
            match resolve_compiler(&config.gcc_dir, base) {
                Some(cc) => {
                    let stem = f.file_stem().map(|s| s.to_string_lossy().to_string()).unwrap_or_else(|| "out".into());
                    let obj = out_dir.join(format!("{stem}.o"));
                    let mut cmd = Command::new(&cc);
                    cmd.arg("-c").arg(f).arg("-o").arg(&obj);
                    report.commands.push(format!("{} -c {} -o {}", cc.display(), f.display(), obj.display()));
                    match run_compiler(&mut cmd) {
                        Ok((0, _)) => {
                            report.ok_count += 1;
                            report.entries.push(CompileEntry {
                                path: f.display().to_string(), ok: true,
                                message: format!(".o 已输出：{}", obj.display()),
                            });
                        }
                        Ok((code, output_text)) => {
                            report.failed_count += 1;
                            report.entries.push(CompileEntry {
                                path: f.display().to_string(), ok: false,
                                message: truncate_msg(format!("{base} 退出码 {code}：{output_text}")),
                            });
                        }
                        Err(e) => {
                            report.failed_count += 1;
                            report.entries.push(CompileEntry {
                                path: f.display().to_string(), ok: false,
                                message: format!("{COMPILE_TIMEOUT_HINT}：{e}"),
                            });
                        }
                    }
                }
                None => {
                    report.failed_count += 1;
                    report.entries.push(CompileEntry {
                        path: f.display().to_string(), ok: false,
                        message: format!("未找到 {base}：请在 设置 → 编译配置 手动选择编译器目录"),
                    });
                }
            }
        }
    }

    // entries 截断（保留前 MAX_REPORT_ENTRIES 条）
    if report.entries.len() > MAX_REPORT_ENTRIES {
        report.entries.truncate(MAX_REPORT_ENTRIES);
    }
    Ok(report)
}

#[cfg(test)]
mod tests {
    use super::*;

    fn tmp_dir(name: &str) -> PathBuf {
        let dir = std::env::temp_dir().join(format!(
            "eaide_compile_test_{name}_{}", std::process::id()
        ));
        let _ = std::fs::remove_dir_all(&dir);
        std::fs::create_dir_all(&dir).unwrap();
        dir
    }

    #[test]
    fn config_roundtrip() {
        let dir = tmp_dir("config");
        let path = dir.join("compile.json");
        let cfg = CompileConfig {
            javac_dir: r"C:\jdk\bin".into(),
            python_dir: String::new(),
            gcc_dir: String::new(),
            output_dir: r"D:\ws\compiled".into(),
        };
        save_config_to(&path, &cfg).unwrap();
        assert_eq!(load_config_from(&path), cfg);
        // 文件不存在 → 默认空配置
        assert_eq!(load_config_from(&dir.join("nope.json")), CompileConfig::default());
    }

    #[test]
    fn resolve_from_config_accepts_file_and_dir() {
        let dir = tmp_dir("resolve");
        let exe = dir.join(with_exe("javac"));
        std::fs::write(&exe, b"").unwrap();
        // 目录 → 拼可执行名
        assert_eq!(resolve_from_config(dir.to_str().unwrap(), "javac"), Some(exe.clone()));
        // 直接给文件
        assert_eq!(resolve_from_config(exe.to_str().unwrap(), "javac"), Some(exe));
        // 空 / 不存在 → None
        assert_eq!(resolve_from_config("", "javac"), None);
        assert_eq!(resolve_from_config(r"Z:\not-exist-dir", "javac"), None);
    }

    #[test]
    fn collect_sources_recurses_and_skips_noise() {
        let dir = tmp_dir("collect");
        let src = dir.join("src").join("main").join("java");
        std::fs::create_dir_all(&src).unwrap();
        std::fs::create_dir_all(dir.join("target")).unwrap();
        std::fs::write(src.join("Foo.java"), "class Foo {}").unwrap();
        std::fs::write(dir.join("run.py"), "print(1)").unwrap();
        std::fs::write(dir.join("note.txt"), "skip me").unwrap();
        std::fs::write(dir.join("target").join("Gen.java"), "class Gen {}").unwrap();

        let items = vec![CompileItem { path: dir.display().to_string(), is_dir: true }];
        let (files, truncated, errors) = collect_sources(&items);
        assert!(!truncated);
        assert!(errors.is_empty());
        let names: Vec<String> = files.iter().map(|f| f.file_name().unwrap().to_string_lossy().to_string()).collect();
        assert!(names.contains(&"Foo.java".to_string()));
        assert!(names.contains(&"run.py".to_string()));
        assert!(!names.contains(&"note.txt".to_string()));
        assert!(!names.contains(&"Gen.java".to_string()), "target/ 下产物不应被收录");
    }

    #[test]
    fn collect_sources_dedups_and_reports_missing() {
        let dir = tmp_dir("dedup");
        let f = dir.join("A.java");
        std::fs::write(&f, "class A {}").unwrap();
        let items = vec![
            CompileItem { path: f.display().to_string(), is_dir: false },
            CompileItem { path: f.display().to_string(), is_dir: false },
            CompileItem { path: dir.join("Ghost.java").display().to_string(), is_dir: false },
        ];
        let (files, _, errors) = collect_sources(&items);
        assert_eq!(files.len(), 1);
        assert_eq!(errors.len(), 1);
        assert!(errors[0].contains("路径不存在"));
    }

    #[test]
    fn java_source_root_prefers_maven_layout() {
        let file = PathBuf::from("/proj/mod/src/main/java/com/x/Foo.java");
        assert_eq!(java_source_root(&file), PathBuf::from("/proj/mod/src/main/java"));
        let plain = PathBuf::from("/proj/src/Foo.java");
        assert_eq!(java_source_root(&plain), PathBuf::from("/proj/src"));
        let bare = PathBuf::from("/proj/Foo.java");
        assert_eq!(java_source_root(&bare), PathBuf::from("/proj"));
    }

    #[test]
    fn maven_target_classes_found_within_three_levels() {
        let dir = tmp_dir("maven");
        let src_root = dir.join("src").join("main").join("java");
        let classes = dir.join("target").join("classes");
        std::fs::create_dir_all(&src_root).unwrap();
        std::fs::create_dir_all(&classes).unwrap();
        assert_eq!(maven_target_classes(&src_root), Some(classes));
        // 没有 target → None
        let dir2 = tmp_dir("maven2");
        let src2 = dir2.join("src");
        std::fs::create_dir_all(&src2).unwrap();
        assert_eq!(maven_target_classes(&src2), None);
    }

    #[test]
    fn is_supported_source_by_extension() {
        assert!(is_supported_source(Path::new("a/Foo.java")));
        assert!(is_supported_source(Path::new("a/run.PY")));
        assert!(is_supported_source(Path::new("a/main.cpp")));
        assert!(!is_supported_source(Path::new("a/pom.xml")));
        assert!(!is_supported_source(Path::new("a/readme")));
    }

    #[test]
    fn truncate_msg_keeps_short_and_cuts_long() {
        assert_eq!(truncate_msg("  ok  ".into()), "ok");
        let long = "x".repeat(MAX_ERR_LEN + 10);
        let cut = truncate_msg(long);
        assert_eq!(cut.len(), MAX_ERR_LEN + "…".len());
        assert!(cut.ends_with('…'));
    }

    #[test]
    fn find_project_root_locates_nearest_pom() {
        let dir = tmp_dir("projroot");
        let module_src = dir.join("mod-a").join("src").join("main").join("java");
        std::fs::create_dir_all(&module_src).unwrap();
        std::fs::write(dir.join("pom.xml"), "<project/>").unwrap();
        // 从 src/main/java 向上 3 层到 mod-a，再 1 层到项目根（有 pom.xml）
        assert_eq!(find_project_root(&module_src), Some(dir.clone()));
        // 模块自身有 pom → 停在模块层
        std::fs::write(dir.join("mod-a").join("pom.xml"), "<project/>").unwrap();
        assert_eq!(find_project_root(&module_src), Some(dir.join("mod-a")));
        // 无 pom → None
        let dir2 = tmp_dir("projroot2");
        let src2 = dir2.join("src");
        std::fs::create_dir_all(&src2).unwrap();
        assert_eq!(find_project_root(&src2), None);
    }

    #[test]
    fn maven_project_classpath_collects_all_modules_and_jars() {
        let dir = tmp_dir("projcp");
        let mod_a_classes = dir.join("mod-a").join("target").join("classes");
        let mod_b_classes = dir.join("mod-b").join("target").join("classes");
        std::fs::create_dir_all(&mod_a_classes).unwrap();
        std::fs::create_dir_all(&mod_b_classes).unwrap();
        // 依赖 jar 落在 target/classes 里（dependency:copy-dependencies 习惯位置）
        std::fs::write(mod_b_classes.join("spring-core.jar"), b"").unwrap();
        // 源码目录不该被扫进来
        std::fs::create_dir_all(dir.join("mod-a").join("src").join("main").join("java")).unwrap();

        let cp = maven_project_classpath(&dir);
        assert!(cp.contains(&mod_a_classes));
        assert!(cp.contains(&mod_b_classes));
        assert!(cp.contains(&mod_b_classes.join("spring-core.jar")));
        // 去重生效
        let mut dup = cp.clone();
        dup.sort();
        dup.dedup();
        assert_eq!(dup.len(), cp.len());
    }

    #[test]
    fn decode_compiler_output_utf8_passthrough() {
        let s = "错误：找不到符号\nerror: cannot find symbol";
        assert_eq!(decode_compiler_output(s.as_bytes()), s);
    }

    #[cfg(target_os = "windows")]
    #[test]
    fn decode_compiler_output_gbk_fallback_on_zh_windows() {
        // 中文 Windows（cp936）上 javac 报错是 GBK 字节；非 UTF-8 → 走 ANSI 回落
        let (gbk_bytes, _, _) = encoding_rs::GBK.encode("错误：找不到符号");
        let decoded = decode_compiler_output(&gbk_bytes);
        // 系统代码页非 936 时回落 lossy，不断言内容；936 时必须还原中文
        if unsafe { windows_codepage() } == 936 {
            assert!(decoded.contains("找不到符号"), "GBK 回落解码失败：{decoded}");
        }
    }
}
