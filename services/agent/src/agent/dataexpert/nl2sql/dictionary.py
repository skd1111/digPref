"""Phase 7 V0 · 业务字典知识库 —— 消除 NL2SQL 幻觉。

把用户自然语言中的业务术语翻译成数据库实际值：
  - '成功' → status='SUC' 或 status='1'
  - '消费' → txn_code='1001'
  - '损失类' → five_class='5'

V0：硬编码金融常见字典。V1：从 data_sources.schema_cache 字段注释自动提取。
"""

from __future__ import annotations

# 金融业务字典（source_id → {自然语言 → SQL 条件片段}）
_DEFAULT_DICTIONARY: dict[str, dict[str, str]] = {
    # 通用
    "_global": {
        "成功": "status='SUC'",
        "失败": "status='FAIL'",
        "处理中": "status='PEND'",
        "正常": "status='1'",
        "冻结": "status='0'",
        "销户": "status='9'",
    },
    # 信贷系统
    "ds_credit": {
        "正常类": "five_class='1'",
        "关注类": "five_class='2'",
        "次级类": "five_class='3'",
        "可疑类": "five_class='4'",
        "损失类": "five_class='5'",
        "坏账": "five_class IN ('4','5')",
        "逾期": "overdue_days > 0",
    },
    # 支付网关
    "ds_pay": {
        "消费": "txn_code='1001'",
        "退款": "txn_code='1002'",
        "撤销": "txn_code='1003'",
        "微信": "channel='WECHAT'",
        "支付宝": "channel='ALIPAY'",
        "银联": "channel='UNIONPAY'",
    },
}


def translate(question: str, source_id: str = "") -> str:
    """业务字典替换：把自然语言中的业务术语翻译成 SQL 条件上下文。

    返回注入 prompt 的前置上下文字符串（告诉 LLM 这些映射关系）。

    Args:
        question: 用户自然语言问题。
        source_id: 数据源 ID（用于加载特定字典）。

    Returns:
        业务字典上下文字符串（注入 prompt）。
    """
    mappings: list[str] = []

    # 加载全局 + 特定数据源字典
    dicts = [_DEFAULT_DICTIONARY.get("_global", {})]
    if source_id and source_id in _DEFAULT_DICTIONARY:
        dicts.append(_DEFAULT_DICTIONARY[source_id])

    for d in dicts:
        for term, sql_frag in d.items():
            if term in question:
                mappings.append(f"  - 「{term}」→ {sql_frag}")

    if not mappings:
        return ""

    return (
        "【业务字典映射】（以下术语在 SQL 中必须使用对应的编码值，禁止使用中文）：\n"
        + "\n".join(mappings)
    )


def get_dictionary_context(source_id: str = "") -> str:
    """获取数据源的完整业务字典上下文（不论问题内容）。

    用于 prompt 前置注入，让 LLM 知道所有可用映射。
    """
    lines: list[str] = []
    dicts = [_DEFAULT_DICTIONARY.get("_global", {})]
    if source_id and source_id in _DEFAULT_DICTIONARY:
        dicts.append(_DEFAULT_DICTIONARY[source_id])

    for d in dicts:
        for term, sql_frag in d.items():
            lines.append(f"  - 「{term}」→ {sql_frag}")

    if not lines:
        return ""
    return "【业务字典】：\n" + "\n".join(lines)
