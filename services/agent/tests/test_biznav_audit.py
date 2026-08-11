"""test_biznav_audit.py —— biznav.audit 5 个常量测试（Phase 2G V1.1）。

测试矩阵（2 个）：
- test_5_audit_events_distinct_strings
- test_module_exports_5_constants
"""

from __future__ import annotations


def test_5_audit_events_distinct_strings():
    from agent.biznav.audit import (
        EVT_FEATURE_DELETE,
        EVT_FEATURE_EXTRACT,
        EVT_FEATURE_IMPORT,
        EVT_FEATURE_UPDATE,
        EVT_YAML_RELOAD,
    )

    constants = [
        EVT_FEATURE_EXTRACT,
        EVT_FEATURE_UPDATE,
        EVT_FEATURE_DELETE,
        EVT_FEATURE_IMPORT,
        EVT_YAML_RELOAD,
    ]
    # 5 个都是 string
    assert all(isinstance(c, str) for c in constants)
    # 5 个都非空
    assert all(c for c in constants)
    # 5 个互不相等
    assert len(set(constants)) == 5
    # 字符串值符合设计文档 §11.1
    assert EVT_FEATURE_EXTRACT == "FEATURE_EXTRACT"
    assert EVT_FEATURE_UPDATE == "FEATURE_UPDATE"
    assert EVT_FEATURE_DELETE == "FEATURE_DELETE"
    assert EVT_FEATURE_IMPORT == "FEATURE_IMPORT"
    assert EVT_YAML_RELOAD == "YAML_RELOAD"


def test_module_exports_5_constants():
    import agent.biznav.audit as audit_mod

    # __all__ 应包含这 5 个变量名（Python 语义）
    expected_names = [
        "EVT_FEATURE_EXTRACT",
        "EVT_FEATURE_UPDATE",
        "EVT_FEATURE_DELETE",
        "EVT_FEATURE_IMPORT",
        "EVT_YAML_RELOAD",
    ]
    assert sorted(audit_mod.__all__) == sorted(expected_names)
    # 模块顶层也确实定义了，且值都是字符串
    for name in expected_names:
        assert hasattr(audit_mod, name)
        assert isinstance(getattr(audit_mod, name), str)
    # 并校验字符串值（与 design spec 一致）
    assert audit_mod.EVT_FEATURE_EXTRACT == "FEATURE_EXTRACT"
    assert audit_mod.EVT_FEATURE_UPDATE == "FEATURE_UPDATE"
    assert audit_mod.EVT_FEATURE_DELETE == "FEATURE_DELETE"
    assert audit_mod.EVT_FEATURE_IMPORT == "FEATURE_IMPORT"
    assert audit_mod.EVT_YAML_RELOAD == "YAML_RELOAD"
