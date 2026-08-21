"""Phase 7 V1 · 业务字典知识库 —— 消除 NL2SQL 幻觉。

把用户自然语言中的业务术语翻译成数据库实际值：
  - '成功' → status='SUC' 或 status='1'
  - '消费' → txn_code='1001'
  - '损失类' → five_class='5'

V1（YAML 外置，参照 Vanna train(documentation) 的可运营范式）：
  - 目录 ``settings.data_biz_dict_dir``（默认 config/biz_dict）：
    ``_global.yaml`` 全局术语 + ``{source_id}.yaml`` 源级术语
  - YAML 条目与内置默认字典**合并**（同键 YAML 覆盖）——内置条目是出厂兜底，
    运营方只需增改 YAML，不用碰代码
  - 目录缺失 / 文件解析失败 → 退化纯内置默认（best-effort，不阻断 NL2SQL）
  - 按目录内 *.yaml 的 mtime 签名缓存，改文件自动生效（与 fiscal_rules 同机制）
"""

from __future__ import annotations

import logging
from pathlib import Path

from agent.config import settings

logger = logging.getLogger(__name__)

# 金融业务字典内置默认（source_id → {自然语言 → SQL 条件片段}）——出厂兜底
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

# YAML 加载缓存：key = 目录路径，value = (mtime 签名, 合并后字典)
_DICT_CACHE: dict[str, tuple[str, dict[str, dict[str, str]]]] = {}


def _dir_signature(base: Path) -> str:
    """目录内 *.yaml 的 (文件名, mtime) 签名；目录缺失返空串。"""
    if not base.is_dir():
        return ""
    parts = [f"{fp.name}:{int(fp.stat().st_mtime)}" for fp in sorted(base.glob("*.yaml"))]
    return "|".join(parts)


def _load_yaml_terms(fp: Path) -> dict[str, str]:
    """单个 YAML 文件 → {术语: SQL 片段}；格式非法返 {}（记 warning 不抛）。

    YAML 结构：平铺 ``术语: SQL片段``（值含特殊字符时加引号）。
    """
    import yaml

    try:
        data = yaml.safe_load(fp.read_text(encoding="utf-8"))
    except Exception as exc:
        logger.warning("biz_dict yaml load failed %s: %s", fp, exc)
        return {}
    if not isinstance(data, dict):
        logger.warning("biz_dict yaml not a mapping: %s", fp)
        return {}
    out: dict[str, str] = {}
    for k, v in data.items():
        if isinstance(k, str) and isinstance(v, str) and k.strip() and v.strip():
            out[k.strip()] = v.strip()
    return out


def _resolve_dict_dir(dict_dir: str | None) -> Path:
    """字典目录解析：显式参数 > cwd 配置路径 > PyInstaller _MEIPASS 内置副本。

    打包后 exe 可能在任意工作目录启动，spec datas 已将 config/biz_dict
    打进 _MEIPASS；cwd 下配置路径不存在时回退到内置副本（与 doc_review 同策略）。
    """
    if dict_dir:
        return Path(dict_dir)
    base = Path(settings.data_biz_dict_dir)
    if base.is_dir():
        return base
    import sys

    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        bundled = Path(meipass) / settings.data_biz_dict_dir
        if bundled.is_dir():
            return bundled
    return base


def load_dictionary(dict_dir: str | None = None) -> dict[str, dict[str, str]]:
    """合并后的完整字典（内置默认 + YAML 外置覆盖/扩展）。

    Args:
        dict_dir: 字典目录（缺省按 cwd > _MEIPASS 解析；测试可注入）。

    Returns:
        {source_id 或 "_global": {术语: SQL 片段}}；YAML 缺席时等于内置默认。
    """
    base = _resolve_dict_dir(dict_dir)
    sig = _dir_signature(base)
    if not sig:
        return _DEFAULT_DICTIONARY  # 目录缺失/无 yaml → 纯内置

    cached = _DICT_CACHE.get(str(base))
    if cached is not None and cached[0] == sig:
        return cached[1]

    merged: dict[str, dict[str, str]] = {k: dict(v) for k, v in _DEFAULT_DICTIONARY.items()}
    for fp in sorted(base.glob("*.yaml")):
        key = fp.stem
        terms = _load_yaml_terms(fp)
        if not terms:
            continue
        merged.setdefault(key, {}).update(terms)

    _DICT_CACHE[str(base)] = (sig, merged)
    logger.info("biz_dict loaded dir=%s groups=%d", base, len(merged))
    return merged


def reset_dictionary_cache() -> None:
    """清空 YAML 缓存（测试隔离用）。"""
    _DICT_CACHE.clear()


def translate(question: str, source_id: str = "", dict_dir: str | None = None) -> str:
    """业务字典替换：把自然语言中的业务术语翻译成 SQL 条件上下文。

    返回注入 prompt 的前置上下文字符串（告诉 LLM 这些映射关系）。

    Args:
        question: 用户自然语言问题。
        source_id: 数据源 ID（用于加载特定字典）。
        dict_dir: 字典目录（缺省取 settings；测试可注入）。

    Returns:
        业务字典上下文字符串（注入 prompt）；无命中返空串。
    """
    dictionary = load_dictionary(dict_dir)
    mappings: list[str] = []

    # 加载全局 + 特定数据源字典
    dicts = [dictionary.get("_global", {})]
    if source_id and source_id in dictionary:
        dicts.append(dictionary[source_id])

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


def get_dictionary_context(source_id: str = "", dict_dir: str | None = None) -> str:
    """获取数据源的完整业务字典上下文（不论问题内容）。

    用于 prompt 前置注入，让 LLM 知道所有可用映射。
    """
    dictionary = load_dictionary(dict_dir)
    lines: list[str] = []
    dicts = [dictionary.get("_global", {})]
    if source_id and source_id in dictionary:
        dicts.append(dictionary[source_id])

    for d in dicts:
        for term, sql_frag in d.items():
            lines.append(f"  - 「{term}」→ {sql_frag}")

    if not lines:
        return ""
    return "【业务字典】：\n" + "\n".join(lines)
