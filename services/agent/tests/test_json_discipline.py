"""test_json_discipline —— 共享 JSON 纪律与容错解析（spec §4.5 第三层）。"""

from __future__ import annotations

from agent.llm.json_discipline import (
    extract_json,
    extract_sql,
    json_instructions,
    strip_think_blocks,
)


def test_json_instructions_markdown_contains_schema():
    out = json_instructions('{"a": "string"}')
    assert '{"a": "string"}' in out
    assert "不要 ``` 代码块" in out


def test_json_instructions_none_is_empty():
    assert json_instructions("{}", style="none") == ""


def test_json_instructions_xml_wraps_schema():
    out = json_instructions('{"a": 1}', style="xml")
    assert "<output_constraints>" in out
    assert "<schema>" in out


def test_strip_think_blocks_upper_and_lower():
    raw = '<THINK>plan</THINK>{"intent": "query"}'
    assert strip_think_blocks(raw) == '{"intent": "query"}'
    raw2 = "```think\nreasoning\n```\n[1, 2]"
    assert strip_think_blocks(raw2) == "[1, 2]"


def test_extract_json_fenced():
    assert extract_json('```json\n{"a": 1}\n```') == {"a": 1}


def test_extract_json_with_prefix_and_suffix():
    raw = '好的，这是结果：{"intent": "query"} 请查收'
    assert extract_json(raw) == {"intent": "query"}


def test_extract_json_trailing_comma():
    assert extract_json('{"a": 1,}') == {"a": 1}


def test_extract_json_unescaped_quotes_and_bare_newline():
    raw = '{"description": "他说"你好"然后\n换行", "ok": true}'
    assert extract_json(raw)["ok"] is True


def test_extract_json_truncated_balanced():
    assert extract_json('{"findings": [{"title": "单方解除权') == {
        "findings": [{"title": "单方解除权"}]
    }


def test_extract_json_array_want():
    raw = '请参考：```json\n[{"id": 1}, {"id": 2}]\n```'
    assert extract_json(raw, want="array") == [{"id": 1}, {"id": 2}]


def test_extract_json_invalid_returns_none():
    assert extract_json("完全不是 JSON") is None
    assert extract_json("") is None
    assert extract_json(None) is None


def test_extract_json_think_block_before_json():
    raw = '<THINK>先分析</THINK>{"a": [1, 2]}'
    assert extract_json(raw) == {"a": [1, 2]}


def test_extract_sql_fence_and_prefix():
    raw = "好的，SQL 如下：\n```sql\nSELECT * FROM t WHERE id = 1\n```"
    sql = extract_sql(raw)
    assert "SELECT * FROM t WHERE id = 1" in sql
    assert "```" not in sql


async def test_parse_with_retry_recovers():
    from agent.llm.json_discipline import parse_with_retry

    calls: list[str] = []

    async def call(hint: str, last: str) -> str:
        calls.append(hint)
        if len(calls) == 1:
            return '{"a": }'  # 值缺失，容错修复链无法修复 → 必须触发重试
        return '{"a": 1}'

    out = await parse_with_retry(call, lambda t: extract_json(t))
    assert out == {"a": 1}
    assert len(calls) == 2
    assert "无法被解析" in calls[1]
