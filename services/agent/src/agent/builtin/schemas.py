"""Phase 1B · 内置工具 JSON Schema（供 planner / 动态工具目录使用）。

LLM 只能看到这里定义的参数（allowed_roots 等内部参数不出现在 schema 中，
由 dispatcher / 路径沙箱注入）。
"""

from __future__ import annotations

BUILTIN_TOOL_SCHEMAS: dict[str, dict] = {
    # ---- 文件（V0 Python）----
    "read_file": {
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "文件绝对路径"},
            "start_line": {"type": "integer", "description": "起始行（0 起）", "default": 0},
            "max_lines": {"type": "integer", "description": "最多读取行数（null = 全部）"},
            "encoding": {"type": "string", "description": "文件编码", "default": "utf-8"},
        },
        "required": ["path"],
    },
    "write_file": {
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "目标文件绝对路径"},
            "content": {"type": "string", "description": "写入内容"},
            "encoding": {"type": "string", "description": "文件编码", "default": "utf-8"},
            "overwrite": {"type": "boolean", "description": "已存在时是否覆盖", "default": False},
        },
        "required": ["path", "content"],
    },
    "edit_file": {
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "文件绝对路径"},
            "search_text": {
                "type": "string",
                "description": "要替换的原文（必须唯一匹配，除非 replace_all）",
            },
            "replace_text": {"type": "string", "description": "替换后的文本"},
            "encoding": {"type": "string", "description": "文件编码", "default": "utf-8"},
            "replace_all": {"type": "boolean", "description": "是否替换所有匹配", "default": False},
        },
        "required": ["path", "search_text", "replace_text"],
    },
    "list_dir": {
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "目录绝对路径"},
            "max_entries": {"type": "integer", "description": "最多返回条目数", "default": 1000},
        },
        "required": ["path"],
    },
    "grep": {
        "type": "object",
        "properties": {
            "pattern": {"type": "string", "description": "搜索模式"},
            "path": {"type": "string", "description": "搜索目录或文件", "default": "."},
            "is_regex": {"type": "boolean", "description": "pattern 是否为正则", "default": False},
            "case_insensitive": {"type": "boolean", "description": "忽略大小写", "default": False},
            "context_lines": {"type": "integer", "description": "上下文行数", "default": 0},
        },
        "required": ["pattern"],
    },
    # ---- 轻量（V1 Python）----
    "calculator": {
        "type": "object",
        "properties": {
            "expression": {
                "type": "string",
                "description": "算术表达式（仅 + - * / // % ** 与数字字面量）",
            },
        },
        "required": ["expression"],
    },
    "json_parse": {
        "type": "object",
        "properties": {
            "text": {"type": "string", "description": "待解析 JSON 文本"},
            "strict": {"type": "boolean", "description": "严格模式", "default": True},
        },
        "required": ["text"],
    },
    "json_format": {
        "type": "object",
        "properties": {
            "value": {"description": "任意可 JSON 序列化对象"},
            "indent": {"type": "integer", "description": "缩进", "default": 2},
            "sort_keys": {"type": "boolean", "description": "按键排序", "default": False},
            "ensure_ascii": {
                "type": "boolean",
                "description": "是否转义非 ASCII",
                "default": False,
            },
        },
        "required": ["value"],
    },
    "regex_match": {
        "type": "object",
        "properties": {
            "pattern": {"type": "string", "description": "正则模式（长度 ≤ 1024）"},
            "text": {"type": "string", "description": "匹配文本"},
            "flags": {"type": "integer", "description": "re 标志位", "default": 0},
            "max_matches": {"type": "integer", "description": "最多返回匹配数", "default": 1000},
            "return_groups": {"type": "boolean", "description": "返回捕获组", "default": True},
        },
        "required": ["pattern", "text"],
    },
    "url_parse": {
        "type": "object",
        "properties": {
            "url": {"type": "string", "description": "完整 URL"},
        },
        "required": ["url"],
    },
    # ---- Rust 工具（V1/V2；LLM 侧参数与 Rust 端一致）----
    "stat_file": {
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "文件 / 目录绝对路径"},
        },
        "required": ["path"],
    },
    "mkdir": {
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "要创建的目录"},
            "parents": {"type": "boolean", "description": "递归创建父目录", "default": False},
        },
        "required": ["path"],
    },
    "delete_file": {
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "要删除的文件 / 目录"},
            "recursive": {"type": "boolean", "description": "递归删除目录", "default": False},
        },
        "required": ["path"],
    },
    "move_file": {
        "type": "object",
        "properties": {
            "src": {"type": "string", "description": "源路径"},
            "dest": {"type": "string", "description": "目标路径"},
            "overwrite": {"type": "boolean", "description": "覆盖已存在目标", "default": False},
        },
        "required": ["src", "dest"],
    },
    "find": {
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "搜索根目录"},
            "pattern": {"type": "string", "description": "文件名模式（regex 或 glob）"},
            "is_regex": {"type": "boolean", "description": "pattern 是否为正则", "default": False},
            "max_depth": {"type": "integer", "description": "最大深度", "default": 10},
        },
        "required": ["path", "pattern"],
    },
    "glob": {
        "type": "object",
        "properties": {
            "pattern": {"type": "string", "description": "glob 模式，如 **/*.py"},
            "base_dir": {"type": "string", "description": "基准目录", "default": "."},
        },
        "required": ["pattern"],
    },
    "hash": {
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "文件路径"},
            "algorithm": {
                "type": "string",
                "enum": ["md5", "sha1", "sha256", "blake2b"],
                "default": "sha256",
            },
        },
        "required": ["path"],
    },
    "base64": {
        "type": "object",
        "properties": {
            "mode": {"type": "string", "enum": ["encode", "decode"], "description": "编码 / 解码"},
            "data": {"type": "string", "description": "文本数据（mode 为 encode/decode 时用）"},
            "path": {"type": "string", "description": "文件路径（对文件内容操作时用）"},
        },
        "required": ["mode"],
    },
    "shell": {
        "type": "object",
        "properties": {
            "command": {"type": "string", "description": "要执行的 shell 命令"},
            "allowed_prefixes": {
                "type": "array",
                "items": {"type": "string"},
                "description": "允许的命令前缀白名单",
            },
            "timeout_sec": {"type": "integer", "description": "超时秒数", "default": 30},
        },
        "required": ["command"],
    },
    # ---- 新增常用工具（纯 Python）----
    "datetime_now": {
        "type": "object",
        "properties": {
            "iso": {"type": "boolean", "description": "ISO 8601 格式输出", "default": True},
            "tz_offset_hours": {
                "type": "number",
                "description": "UTC 偏移小时数（如东八区 = 8）；省略时用系统本地时区",
            },
            "include_lunar": {
                "type": "boolean",
                "description": "是否返回农历日期（中文，如「正月初一」）",
                "default": True,
            },
        },
        "required": [],
    },
    "date_parse": {
        "type": "object",
        "properties": {
            "expression": {
                "type": "string",
                "description": (
                    "中文相对时间表达，如：今天 / 明天 / 后天 / 昨天 / 三天前 / "
                    "下周一 / 周末 / 最近三天 / 本月底；也接受 YYYY-MM-DD 透传"
                ),
            },
            "base_date": {
                "type": "string",
                "description": "基准日期 YYYY-MM-DD（缺省用系统本地今天）",
            },
        },
        "required": ["expression"],
    },
    "uuid4": {
        "type": "object",
        "properties": {},
        "required": [],
    },
    "http_get": {
        "type": "object",
        "properties": {
            "url": {"type": "string", "description": "http / https URL"},
            "timeout_sec": {"type": "number", "description": "超时秒数"},
            "max_bytes": {"type": "integer", "description": "响应体大小上限"},
            "headers": {"type": "object", "description": "额外请求头"},
        },
        "required": ["url"],
    },
    "csv_parse": {
        "type": "object",
        "properties": {
            "text": {"type": "string", "description": "CSV 文本"},
            "delimiter": {"type": "string", "description": "分隔符", "default": ","},
            "has_header": {"type": "boolean", "description": "首行为表头", "default": False},
            "max_rows": {"type": "integer", "description": "最多解析行数", "default": 1000},
        },
        "required": ["text"],
    },
    "text_split": {
        "type": "object",
        "properties": {
            "text": {"type": "string", "description": "要切分的文本"},
            "max_chars": {"type": "integer", "description": "每段最大字符数", "default": 2000},
            "separator": {"type": "string", "description": "优先按分隔符切分（如换行）"},
        },
        "required": ["text"],
    },
    # ---- V4 扩展工具（CODE/WORK 模式能力补齐，2026-08-04）----
    "http_post": {
        "type": "object",
        "properties": {
            "url": {"type": "string", "description": "http / https URL"},
            "json_body": {"type": "object", "description": "JSON 请求体（与 form_data 二选一）"},
            "form_data": {"type": "object", "description": "表单请求体（与 json_body 二选一）"},
            "headers": {"type": "object", "description": "额外请求头"},
            "timeout_sec": {"type": "number", "description": "超时秒数"},
            "max_bytes": {"type": "integer", "description": "响应体大小上限"},
        },
        "required": ["url"],
    },
    "git_status": {
        "type": "object",
        "properties": {
            "repo": {"type": "string", "description": "仓库目录绝对路径"},
        },
        "required": ["repo"],
    },
    "git_diff": {
        "type": "object",
        "properties": {
            "repo": {"type": "string", "description": "仓库目录绝对路径"},
            "staged": {"type": "boolean", "description": "True 看暂存区 vs HEAD", "default": False},
            "path_filter": {"type": "string", "description": "限定到某个路径（可选）"},
        },
        "required": ["repo"],
    },
    "git_log": {
        "type": "object",
        "properties": {
            "repo": {"type": "string", "description": "仓库目录绝对路径"},
            "limit": {"type": "integer", "description": "最多返回条数（上限 200）", "default": 20},
        },
        "required": ["repo"],
    },
    "git_commit": {
        "type": "object",
        "properties": {
            "repo": {"type": "string", "description": "仓库目录绝对路径"},
            "message": {"type": "string", "description": "提交信息（中文 / 英文均可）"},
        },
        "required": ["repo", "message"],
    },
    # ---- V4 内部能力只读入口（codenav / biznav）----
    "symbol_search": {
        "type": "object",
        "properties": {
            "name": {"type": "string", "description": "符号名（精确 + 模糊）"},
            "kind": {
                "type": "string",
                "description": "符号类型过滤（function / class / variable，可选）",
            },
            "limit": {"type": "integer", "description": "最多返回条数（上限 50）", "default": 10},
        },
        "required": ["name"],
    },
    "file_symbols": {
        "type": "object",
        "properties": {
            "file_path": {"type": "string", "description": "源文件路径"},
        },
        "required": ["file_path"],
    },
    "biznav_features": {
        "type": "object",
        "properties": {
            "project_name": {"type": "string", "description": "项目名"},
            "category": {"type": "string", "description": "分类过滤（可选）"},
        },
        "required": ["project_name"],
    },
    # ---- V5 文件转换工具（markitdown，2026-08-10）----
    "file_to_markdown": {
        "type": "object",
        "properties": {
            "path": {
                "type": "string",
                "description": "要转换的文件绝对路径（docx / pdf / pptx / xlsx / html / epub / 图片等）",
            },
            "timeout_sec": {"type": "number", "description": "转换超时秒数（默认 60）"},
        },
        "required": ["path"],
    },
    # ---- V6 文档处理工具族（Excel / PDF / Word，2026-08-10）----
    "excel_query": {
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "xlsx / csv 文件绝对路径"},
            "action": {
                "type": "string",
                "enum": ["sheets", "rows", "aggregate"],
                "description": "sheets=列 sheet 名；rows=取行；aggregate=分组聚合",
                "default": "rows",
            },
            "sheet": {"type": "string", "description": "sheet 名（缺省第一个）"},
            "columns": {"type": "array", "items": {"type": "string"}, "description": "列投影"},
            "where": {
                "type": "object",
                "description": "等值过滤（{列名: 值}，多条件 AND，忽略大小写）",
            },
            "group_by": {"type": "string", "description": "aggregate 分组列名"},
            "agg_column": {"type": "string", "description": "aggregate 聚合列名（count 可省略）"},
            "agg_op": {
                "type": "string",
                "enum": ["count", "sum", "avg", "min", "max"],
                "default": "sum",
            },
            "limit": {"type": "integer", "description": "最多返回行数（上限 500）", "default": 100},
            "offset": {"type": "integer", "description": "跳过的行数", "default": 0},
        },
        "required": ["path"],
    },
    "excel_export": {
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "输出 xlsx 绝对路径"},
            "rows": {
                "type": "array",
                "description": "list[dict]（表头=键并集）或 list[list]（首行=表头）",
            },
            "sheet_name": {"type": "string", "description": "sheet 名", "default": "Sheet1"},
            "overwrite": {"type": "boolean", "description": "已存在时是否覆盖", "default": False},
        },
        "required": ["path", "rows"],
    },
    "pdf_merge": {
        "type": "object",
        "properties": {
            "inputs": {
                "type": "array",
                "items": {"type": "string"},
                "description": "待合并 PDF 路径列表（按顺序）",
            },
            "output": {"type": "string", "description": "输出 PDF 绝对路径"},
            "overwrite": {"type": "boolean", "description": "已存在时是否覆盖", "default": False},
        },
        "required": ["inputs", "output"],
    },
    "pdf_split": {
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "源 PDF 绝对路径"},
            "output_dir": {"type": "string", "description": "输出目录绝对路径"},
            "pages": {
                "type": "string",
                "description": "抽取页码区间（如 '1-3,5'）；省略则逐页拆分",
            },
            "output_name": {"type": "string", "description": "抽取模式输出文件名"},
            "overwrite": {"type": "boolean", "description": "已存在时是否覆盖", "default": False},
        },
        "required": ["path", "output_dir"],
    },
    "word_generate": {
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "输出 docx 绝对路径"},
            "markdown": {
                "type": "string",
                "description": "Markdown 子集文本（# 标题 / - 列表 / 表格 / 代码块）",
            },
            "title": {"type": "string", "description": "文档标题（可选）"},
            "overwrite": {"type": "boolean", "description": "已存在时是否覆盖", "default": False},
        },
        "required": ["path", "markdown"],
    },
    # ---- V7 大文件查看与搜索（klogg 式只读，2026-08-10）----
    "log_read_lines": {
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "大文件绝对路径（日志 / dump / 任意文本）"},
            "start_line": {"type": "integer", "description": "起始行（0-based）", "default": 0},
            "max_lines": {"type": "integer", "description": "最多读取行数（上限 2000）", "default": 200},
            "tail_lines": {
                "type": "integer",
                "description": "传 N 时返回文件最后 N 行（忽略 start_line/max_lines）",
            },
            "encoding": {"type": "string", "description": "文件编码", "default": "utf-8"},
        },
        "required": ["path"],
    },
    "log_search": {
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "大文件绝对路径（日志 / dump / 任意文本）"},
            "pattern": {"type": "string", "description": "搜索模式（字面量或正则，≤1024 字符）"},
            "is_regex": {"type": "boolean", "description": "pattern 是否为正则", "default": False},
            "case_insensitive": {"type": "boolean", "description": "忽略大小写", "default": False},
            "context_lines": {"type": "integer", "description": "上下文行数（上限 20）", "default": 0},
            "max_results": {"type": "integer", "description": "最多命中数（上限 1000）", "default": 200},
            "encoding": {"type": "string", "description": "文件编码", "default": "utf-8"},
        },
        "required": ["path", "pattern"],
    },
}


def get_builtin_schema(name: str) -> dict | None:
    """返回工具 JSON Schema；未知工具返回 None。"""
    return BUILTIN_TOOL_SCHEMAS.get(name)
