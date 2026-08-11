"""Phase 7 V0 · 导出测试 —— CSV BOM / 水印 / PII 脱敏 / MD5 审计。

验收硬门槛（design §11）：
  - 导出文件嵌入隐形水印（操作人 + 时间 + IP）
  - 导出前必过 PII 脱敏
  - 导出审计（文件 MD5）
"""

import tempfile
from pathlib import Path

from agent.dataexpert.export.csv import export_csv
from agent.dataexpert.export.watermark import (
    _mask_value,
    embed_watermark_metadata,
    generate_watermark_text,
    mask_pii_columns,
)

# ---- CSV 导出：UTF-8-BOM 防乱码 ------------------------------------------------

_COLUMNS = ["账户ID", "姓名", "余额"]
_ROWS = [
    ["A001", "张三", 10000],
    ["A002", "李四", -500],
    ["A003", "王五", 88888],
]


def test_csv_export_creates_file():
    """CSV 导出创建文件。"""
    with tempfile.TemporaryDirectory() as tmp:
        path = str(Path(tmp) / "test.csv")
        result = export_csv(_COLUMNS, _ROWS, title="测试报表", output_path=path)
        assert Path(result["path"]).exists()


def test_csv_export_utf8_bom():
    """CSV 文件以 UTF-8-BOM 开头（\\xef\\xbb\\xbf）。"""
    with tempfile.TemporaryDirectory() as tmp:
        path = str(Path(tmp) / "bom.csv")
        export_csv(_COLUMNS, _ROWS, output_path=path)
        raw = Path(path).read_bytes()
        assert raw[:3] == b"\xef\xbb\xbf", "CSV 必须以 UTF-8-BOM 开头"


def test_csv_export_no_garbled_chinese():
    """CSV 中文内容无乱码。"""
    with tempfile.TemporaryDirectory() as tmp:
        path = str(Path(tmp) / "cn.csv")
        export_csv(_COLUMNS, _ROWS, output_path=path)
        content = Path(path).read_text(encoding="utf-8-sig")
        assert "张三" in content
        assert "账户ID" in content


def test_csv_export_md5_present():
    """导出结果包含 MD5 哈希。"""
    with tempfile.TemporaryDirectory() as tmp:
        path = str(Path(tmp) / "md5.csv")
        result = export_csv(_COLUMNS, _ROWS, output_path=path)
        assert "md5" in result
        assert len(result["md5"]) == 32  # MD5 hex = 32 chars


def test_csv_export_row_count():
    """导出结果包含正确行数。"""
    with tempfile.TemporaryDirectory() as tmp:
        path = str(Path(tmp) / "cnt.csv")
        result = export_csv(_COLUMNS, _ROWS, output_path=path)
        assert result["row_count"] == 3


# ---- 水印 ------------------------------------------------------------------------


def test_watermark_text_format():
    """水印文本包含操作人 + 时间 + IP。"""
    wm = generate_watermark_text("admin", "192.168.1.100")
    assert "admin" in wm
    assert "192.168.1.100" in wm
    assert "EAIDE" in wm


def test_watermark_embedded_in_export():
    """导出元数据包含水印字段。"""
    with tempfile.TemporaryDirectory() as tmp:
        path = str(Path(tmp) / "wm.csv")
        result = export_csv(_COLUMNS, _ROWS, operator="test_op", output_path=path)
        assert "watermark" in result
        assert "test_op" in result["watermark"]
        assert "watermark_hash" in result
        assert len(result["watermark_hash"]) == 16


def test_embed_watermark_metadata():
    """embed_watermark_metadata 追加水印字段。"""
    meta = {"path": "/tmp/x.csv", "md5": "abc123", "row_count": 5, "format": "csv"}
    result = embed_watermark_metadata(meta, "operator_x")
    assert "watermark" in result
    assert "operator_x" in result["watermark"]
    assert "watermark_hash" in result


# ---- PII 脱敏 --------------------------------------------------------------------


def test_mask_pii_phone():
    """手机号列被脱敏。"""
    cols = ["name", "phone", "amount"]
    rows = [["张三", "13812345678", 1000]]
    masked = mask_pii_columns(cols, rows)
    # phone 列应被遮罩
    assert masked[0][1] != "13812345678"
    assert "*" in masked[0][1]
    # name 和 amount 不变
    assert masked[0][0] == "张三"
    assert masked[0][2] == 1000


def test_mask_pii_id_card():
    """身份证列被脱敏。"""
    cols = ["id_card", "name"]
    rows = [["110101199001011234", "李四"]]
    masked = mask_pii_columns(cols, rows)
    assert masked[0][0] != "110101199001011234"
    assert "*" in masked[0][0]


def test_mask_pii_no_pii_columns():
    """无 PII 列时数据不变。"""
    cols = ["id", "amount", "status"]
    rows = [["001", 500, "SUC"]]
    masked = mask_pii_columns(cols, rows)
    assert masked == rows


def test_mask_value_long():
    """长值遮罩：保留前 3 后 4。"""
    result = _mask_value("13812345678")
    assert result.startswith("138")
    assert result.endswith("5678")
    assert "*" in result


def test_mask_value_short():
    """短值遮罩：保留前 1，其余 *。"""
    result = _mask_value("12345")
    assert result[0] == "1"
    assert "*" in result


def test_csv_export_with_pii_masking():
    """CSV 导出时 PII 列被脱敏。"""
    cols = ["name", "phone"]
    rows = [["张三", "13812345678"]]
    with tempfile.TemporaryDirectory() as tmp:
        path = str(Path(tmp) / "pii.csv")
        export_csv(cols, rows, output_path=path)
        content = Path(path).read_text(encoding="utf-8-sig")
        # 手机号不应完整出现
        assert "13812345678" not in content
        assert "138" in content  # 前 3 位保留
