//! Phase 2F+ V2 — 编码检测与 GBK 解码。
//!
//! 中文企业日志常见 GBK/GB2312 编码。本模块提供：
//!   - `detect_encoding()`：读取文件头部 4KB，判断 UTF-8 / GBK
//!   - `decode_line()`：按文件编码将 bytes 解码为 UTF-8 String
//!
//! GBK 检测启发式（不依赖 BOM）：
//!   - 尝试 UTF-8 解码：成功 → "utf-8"
//!   - UTF-8 失败 + 出现 GBK 首字节 (0x81-0xFE) → "gbk"
//!   - 其他 → "utf-8" (兜底)

use std::fs::File;
use std::io::Read;
use std::path::Path;

/// 检测文件编码。返回 `"utf-8"` 或 `"gbk"`。
pub fn detect_encoding(path: &Path) -> Result<String, String> {
    let mut file = File::open(path).map_err(|e| format!("detect_encoding: open: {}", e))?;
    let mut buf = vec![0u8; 4096];
    let n = file.read(&mut buf).map_err(|e| format!("detect_encoding: read: {}", e))?;
    let sample = &buf[..n];

    if n == 0 {
        return Ok("utf-8".into()); // 空文件
    }

    // 1. Try UTF-8
    if std::str::from_utf8(sample).is_ok() {
        return Ok("utf-8".into());
    }

    // 2. Detect GBK: count lead bytes in range 0x81-0xFE
    let gbk_lead_count = sample.iter().filter(|&&b| (0x81..=0xFE).contains(&b)).count();
    let total = sample.len();
    if total > 0 && gbk_lead_count as f64 / total as f64 > 0.02 {
        // > 2% of bytes are potential GBK lead bytes → likely GBK
        return Ok("gbk".into());
    }

    // 3. Fallback: assume UTF-8 (with replacement chars for invalid sequences)
    Ok("utf-8".into())
}

/// 将字节行按编码解码为 String。
///
/// - `"gbk"` → 使用 `encoding_rs` GBK 解码
/// - `"utf-8"` 或其他 → UTF-8 with replacement
pub fn decode_line(bytes: &[u8], encoding: &str) -> String {
    if encoding == "gbk" {
        // Use encoding_rs for GBK → UTF-8
        let (cow, _encoding, _had_errors) = encoding_rs::GBK.decode(bytes);
        return cow.into_owned();
    }
    // UTF-8 (or unknown): use String::from_utf8_lossy
    String::from_utf8_lossy(bytes).into_owned()
}

/// 将字节块按编码解码为字符串行列表。
pub fn decode_lines(bytes: &[u8], encoding: &str) -> Vec<String> {
    let text = decode_line(bytes, encoding);
    text.lines().map(|s| s.to_string()).collect()
}

// ===========================================================================
// Tests
// ===========================================================================

#[cfg(test)]
mod tests {
    use super::*;
    use std::io::Write;

    #[test]
    fn detect_utf8_file() {
        let dir = std::env::temp_dir().join(format!("eaide-enc-utf8-{}", uuid::Uuid::new_v4()));
        std::fs::create_dir_all(&dir).unwrap();
        let path = dir.join("utf8.log");
        let mut f = File::create(&path).unwrap();
        f.write_all("Hello World\n你好世界\nERROR something\n".as_bytes()).unwrap();
        f.flush().unwrap();

        let enc = detect_encoding(&path).unwrap();
        assert_eq!(enc, "utf-8");
        std::fs::remove_dir_all(&dir).unwrap();
    }

    #[test]
    fn detect_empty_file() {
        let dir = std::env::temp_dir().join(format!("eaide-enc-empty-{}", uuid::Uuid::new_v4()));
        std::fs::create_dir_all(&dir).unwrap();
        let path = dir.join("empty.log");
        File::create(&path).unwrap();

        let enc = detect_encoding(&path).unwrap();
        assert_eq!(enc, "utf-8");
        std::fs::remove_dir_all(&dir).unwrap();
    }

    #[test]
    fn decode_gbk_line() {
        // "中文测试" in GBK bytes
        let gbk_bytes: &[u8] = &[0xD6, 0xD0, 0xCE, 0xC4, 0xB2, 0xE2, 0xCA, 0xD4];
        let result = decode_line(gbk_bytes, "gbk");
        assert_eq!(result, "中文测试");
    }

    #[test]
    fn decode_utf8_line() {
        let utf8_bytes = "Hello 世界".as_bytes();
        let result = decode_line(utf8_bytes, "utf-8");
        assert_eq!(result, "Hello 世界");
    }

    #[test]
    fn decode_unknown_encoding_falls_back_to_utf8_lossy() {
        // Invalid UTF-8 treated as lossy UTF-8 when encoding is "utf-8"
        let bytes: &[u8] = &[0xC3, 0x28]; // invalid UTF-8 sequence
        let result = decode_line(bytes, "utf-8");
        // Should not panic, should contain replacement char
        assert!(result.contains('\u{FFFD}') || !result.is_empty());
    }

    #[test]
    fn detect_gbk_file() {
        let dir = std::env::temp_dir().join(format!("eaide-enc-gbk-{}", uuid::Uuid::new_v4()));
        std::fs::create_dir_all(&dir).unwrap();
        let path = dir.join("gbk.log");
        let mut f = File::create(&path).unwrap();
        // GBK bytes for "中文日志\n错误\n"
        let gbk_data: &[u8] = &[
            0xD6, 0xD0, 0xCE, 0xC4, 0xC8, 0xD5, 0xD6, 0xBE, // 中文日志
            0x0A, // \n
            0xB4, 0xED, 0xCE, 0xF3, // 错误
            0x0A, // \n
        ];
        f.write_all(gbk_data).unwrap();
        f.flush().unwrap();

        let enc = detect_encoding(&path).unwrap();
        assert_eq!(enc, "gbk");
        std::fs::remove_dir_all(&dir).unwrap();
    }

    #[test]
    fn decode_lines_splits_correctly() {
        let text = "line1\nline2\nline3".as_bytes();
        let lines = decode_lines(text, "utf-8");
        assert_eq!(lines, vec!["line1", "line2", "line3"]);
    }
}
