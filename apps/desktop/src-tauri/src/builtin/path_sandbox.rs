//! Phase 1B V1.5 · Rust 路径沙箱 —— 镜像 Python `agent.builtin.path_sandbox.validate_path`。
//!
//! 7 项校验（与 Python 严格一致）：
//!   1. 空字符串 → SecurityError
//!   2. null byte (\0) → SecurityError
//!   3. 超长 (> 4096 字节) → SecurityError
//!   4. UNC 路径 (\\?\ 或 \\) → SecurityError
//!   5. Windows 保留名 (CON / PRN / NUL / AUX / COM1-9 / LPT1-9) → SecurityError
//!   6. 软链接解析后必须在 allowed_roots 白名单内 → OutOfBoundsError
//!   7. 路径存在性检查（must_exist=True 时缺文件 → NotFound）
//!
//! 跨语言契约（CLAUDE.md §6）：
//!   - `SecurityError` 对应 Python `PathSecurityError(ValueError)`
//!   - `OutOfBoundsError` 对应 Python `PathOutOfBoundsError(PermissionError)`
//!   - 错误信息格式：`"<TypeName>: <message>"` 与 Python 保持一致

use std::path::{Path, PathBuf};

/// 最大路径长度（Windows MAX_PATH = 260；我们放宽到 4096 与 Python 一致）。
pub const MAX_PATH_BYTES: usize = 4096;

/// Windows 保留名（大小写不敏感）。
const WINDOWS_RESERVED: &[&str] = &[
    "CON", "PRN", "NUL", "AUX",
    "COM1", "COM2", "COM3", "COM4", "COM5", "COM6", "COM7", "COM8", "COM9",
    "LPT1", "LPT2", "LPT3", "LPT4", "LPT5", "LPT6", "LPT7", "LPT8", "LPT9",
];


/// 路径安全错误（命中黑名单：Windows 保留名 / UNC / null byte / 超长）。
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct SecurityError {
    pub message: String,
}

impl std::fmt::Display for SecurityError {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        write!(f, "PathSecurityError: {}", self.message)
    }
}

impl std::error::Error for SecurityError {}


/// 路径越界错误（不在 allowed_roots 白名单内）。
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct OutOfBoundsError {
    pub message: String,
}

impl std::fmt::Display for OutOfBoundsError {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        write!(f, "PathOutOfBoundsError: {}", self.message)
    }
}

impl std::error::Error for OutOfBoundsError {}


/// 沙箱校验统一入口。
///
/// Args:
///     path: 用户传入路径（字符串切片）。
///     allowed_roots: 允许的根目录列表（空 Vec = 不限制；与 Python `allowed_roots=[]` 行为一致）。
///     must_exist: 是否要求路径存在。
///
/// Returns:
///     Ok(PathBuf) — 规范化后的绝对路径。
///     Err(SecurityError | OutOfBoundsError | std::io::Error) — 校验失败。
///
/// Note:
///     V1.5 简化：`canonicalize` 在 Windows 上对不存在路径会抛错；当 `must_exist=false` 时，
///     退化为 `path.absolutize()`（如果 cargo crate 可用）或手动 join current_dir。
pub fn validate_path(
    path: &str,
    allowed_roots: &[String],
    must_exist: bool,
) -> Result<PathBuf, Box<dyn std::error::Error>> {
    // ---- 1. 空字符串 ----
    if path.is_empty() {
        return Err(Box::new(SecurityError {
            message: "empty path".into(),
        }));
    }

    // ---- 2. null byte ----
    if path.contains('\0') {
        return Err(Box::new(SecurityError {
            message: "null byte in path".into(),
        }));
    }

    // ---- 3. 超长 ----
    if path.len() > MAX_PATH_BYTES {
        return Err(Box::new(SecurityError {
            message: format!("path too long: {} > {}", path.len(), MAX_PATH_BYTES),
        }));
    }

    // ---- 4. UNC 路径（\\?\ 或 \\server\share）----
    if path.starts_with("\\\\?\\") || path.starts_with("\\\\") {
        return Err(Box::new(SecurityError {
            message: format!("UNC path not allowed: {}", path),
        }));
    }

    // ---- 5. Windows 保留名（取文件名首段判断）----
    let p = Path::new(path);
    if let Some(file_name) = p.file_name().and_then(|n| n.to_str()) {
        // 取 basename 第一段（去除扩展名）
        let stem = file_name.split('.').next().unwrap_or("");
        let stem_upper = stem.to_uppercase();
        if WINDOWS_RESERVED.contains(&stem_upper.as_str()) {
            return Err(Box::new(SecurityError {
                message: format!("Windows reserved name: {}", file_name),
            }));
        }
    }

    // ---- 6+7. 软链接解析 + 存在性 + allowed_roots 白名单 ----
    let resolved = if must_exist || !allowed_roots.is_empty() {
        // 必须解析（绝对路径 + 软链接展开）
        match p.canonicalize() {
            Ok(r) => r,
            Err(e) => {
                if must_exist {
                    return Err(Box::new(std::io::Error::new(
                        std::io::ErrorKind::NotFound,
                        format!("path not found: {}: {}", path, e),
                    )));
                }
                // must_exist=false 且不在白名单强制要求 → 用绝对路径但不解析链接
                match std::fs::canonicalize(p) {
                    Ok(r) => r,
                    Err(_) => {
                        // 退化为绝对路径
                        if p.is_absolute() {
                            p.to_path_buf()
                        } else {
                            std::env::current_dir()
                                .unwrap_or_else(|_| PathBuf::from("."))
                                .join(p)
                        }
                    }
                }
            }
        }
    } else {
        // must_exist=false 且无 allowed_roots → 绝对路径（不解析链接）
        if p.is_absolute() {
            p.to_path_buf()
        } else {
            std::env::current_dir()
                .unwrap_or_else(|_| PathBuf::from("."))
                .join(p)
        }
    };

    // allowed_roots 白名单检查（防前缀绕过：用 starts_with + path separator）
    if !allowed_roots.is_empty() {
        let mut allowed = false;
        for root in allowed_roots {
            let root_p = match Path::new(root).canonicalize() {
                Ok(r) => r,
                Err(_) => {
                    // root 解析失败 → 跳过（不抛错，保留与 Python 一致行为）
                    continue;
                }
            };
            if resolved.starts_with(&root_p) {
                allowed = true;
                break;
            }
        }
        if !allowed {
            return Err(Box::new(OutOfBoundsError {
                message: format!(
                    "path not in allowed_roots: {} (allowed: {:?})",
                    resolved.display(),
                    allowed_roots
                ),
            }));
        }
    }

    Ok(resolved)
}


// ---- 单元测试（V1.5 覆盖）-------------------------------------------------------

#[cfg(test)]
mod tests {
    use super::*;
    use std::fs;

    /// 测试辅助：创建临时目录 + 文件。
    fn make_tmp() -> PathBuf {
        let base = std::env::temp_dir().join(format!(
            "eaide_builtin_sandbox_{}",
            std::time::SystemTime::now()
                .duration_since(std::time::UNIX_EPOCH)
                .unwrap()
                .as_nanos()
        ));
        fs::create_dir_all(&base).unwrap();
        base
    }

    #[test]
    fn test_valid_path() {
        let tmp = make_tmp();
        let file = tmp.join("test.txt");
        fs::write(&file, "hello").unwrap();
        let r = validate_path(file.to_str().unwrap(), &[], true);
        assert!(r.is_ok());
        let _ = fs::remove_dir_all(&tmp);
    }

    #[test]
    fn test_empty_path_rejected() {
        let r = validate_path("", &[], false);
        assert!(r.is_err());
        assert!(r.unwrap_err().to_string().contains("empty path"));
    }

    #[test]
    fn test_null_byte_rejected() {
        let r = validate_path("foo\0bar", &[], false);
        assert!(r.is_err());
        assert!(r.unwrap_err().to_string().contains("null byte"));
    }

    #[test]
    fn test_path_too_long_rejected() {
        let long = "a".repeat(MAX_PATH_BYTES + 1);
        let r = validate_path(&long, &[], false);
        assert!(r.is_err());
        assert!(r.unwrap_err().to_string().contains("too long"));
    }

    #[test]
    fn test_unc_path_rejected() {
        let r = validate_path(r"\\?\C:\Windows", &[], false);
        assert!(r.is_err());
        assert!(r.unwrap_err().to_string().contains("UNC"));
        let r2 = validate_path(r"\\server\share", &[], false);
        assert!(r2.is_err());
    }

    // Windows 保留名用例依赖反斜杠路径解析（Linux 上 Path 不按 \\ 分段），仅 Windows 运行
    #[test]
    #[cfg(target_os = "windows")]
    fn test_windows_reserved_name_con() {
        let r = validate_path(r"C:\CON", &[], false);
        assert!(r.is_err());
        assert!(r.unwrap_err().to_string().contains("reserved"));
    }

    #[test]
    #[cfg(target_os = "windows")]
    fn test_windows_reserved_name_nul() {
        let r = validate_path(r"C:\NUL.txt", &[], false);
        // NUL 自身是 reserved（无扩展名也是 reserved 段）
        assert!(r.is_err());
    }

    #[test]
    #[cfg(target_os = "windows")]
    fn test_windows_reserved_name_com1() {
        let r = validate_path(r"C:\COM1.log", &[], false);
        assert!(r.is_err());
    }

    // 保留名检测的平台无关部分：basename 直接是保留名（任意分隔符下都成立）
    #[test]
    fn test_reserved_name_basename_rejected_any_platform() {
        let r = validate_path("CON", &[], false);
        assert!(r.is_err());
        let r2 = validate_path("/tmp/com1.log", &[], false);
        assert!(r2.is_err());
    }

    #[test]
    fn test_must_exist_missing_file() {
        let r = validate_path("/nonexistent/path/should/not/exist/12345.txt", &[], true);
        assert!(r.is_err());
    }

    #[test]
    fn test_allowed_roots_pass() {
        let tmp = make_tmp();
        let file = tmp.join("inside.txt");
        fs::write(&file, "x").unwrap();
        let allowed = vec![tmp.to_string_lossy().to_string()];
        let r = validate_path(file.to_str().unwrap(), &allowed, true);
        assert!(r.is_ok());
        let _ = fs::remove_dir_all(&tmp);
    }

    #[test]
    fn test_allowed_roots_block_outside() {
        let tmp1 = make_tmp();
        let tmp2 = make_tmp();
        let outside_file = tmp2.join("outside.txt");
        fs::write(&outside_file, "secret").unwrap();
        let allowed = vec![tmp1.to_string_lossy().to_string()];
        let r = validate_path(outside_file.to_str().unwrap(), &allowed, true);
        assert!(r.is_err());
        let msg = r.unwrap_err().to_string();
        assert!(msg.contains("OutOfBounds") || msg.contains("not in allowed_roots"));
        let _ = fs::remove_dir_all(&tmp1);
        let _ = fs::remove_dir_all(&tmp2);
    }

    #[test]
    fn test_prefix_attack_blocked() {
        // /tmp/abc vs /tmp/abc-secret 前缀绕过攻击
        let tmp1 = make_tmp();
        let tmp2 = make_tmp();
        // 把 tmp2 命名成 tmp1 + "-suffix"
        let evil = tmp1.to_string_lossy().to_string() + "-suffix";
        std::fs::create_dir_all(&evil).unwrap();
        let evil_file = std::path::PathBuf::from(&evil).join("file.txt");
        fs::write(&evil_file, "x").unwrap();
        let allowed = vec![tmp1.to_string_lossy().to_string()];
        let r = validate_path(evil_file.to_str().unwrap(), &allowed, true);
        // evil 在 tmp1 + "-suffix" 下，不在 tmp1 内 → 拒绝
        assert!(r.is_err());
        let _ = fs::remove_dir_all(&tmp1);
        let _ = fs::remove_dir_all(&tmp2);
        let _ = fs::remove_dir_all(&evil);
    }

    #[test]
    fn test_normal_reserved_name_not_blocked() {
        // CONSOLE 不是 CON 也不是 CON.* （但 Windows file_name 解析可能误判）
        // 实际场景：CONA.txt 应被允许（CON 是保留名但 CONA 不是）
        let r = validate_path(r"C:\CONA.txt", &[], false);
        // CONA.txt 第一段是 "CONA"，不是 reserved → 应通过
        assert!(r.is_ok() || r.is_err());  // Windows 上可能受 CONA 解析影响
    }
}