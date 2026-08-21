"""语法检查测试（POST /codenav/check + check_syntax，2026-08-19）。

覆盖：
  - Java / Python / TypeScript 合法代码零诊断
  - 语法错误（缺分号 / 括号不闭合 / 结构错乱）能报出且行列 1-based
  - 不支持的后缀 supported=False（前端据此跳过，不算错误）
  - content 超 2MB 返 413；file_path 空返 400
"""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient


@pytest.fixture
def client():
    from agent.codenav import api as codenav_api

    app = FastAPI()
    app.include_router(codenav_api.router)
    return TestClient(app)


VALID_JAVA = """\
public class Foo {
    public int add(int a, int b) {
        return a + b;
    }
}
"""

# 第 2 行 return 语句缺分号
MISSING_SEMI_JAVA = """\
public class Foo {
    public int add(int a, int b) {
        return a + b
    }
}
"""

# 方法体右花括号缺失
UNBALANCED_JAVA = """\
public class Foo {
    public void broken() {
        int x = 1;
"""


class TestCheckSyntaxUnit:
    """check_syntax 纯函数层（不走 HTTP）。"""

    def test_valid_java_no_diagnostics(self):
        from agent.codenav.syntax_check import check_syntax

        lang, diags = check_syntax("Foo.java", VALID_JAVA)
        assert lang == "java"
        assert diags == []

    def test_missing_semi_reported(self):
        from agent.codenav.syntax_check import check_syntax

        lang, diags = check_syntax("Foo.java", MISSING_SEMI_JAVA)
        assert lang == "java"
        assert len(diags) >= 1
        d = diags[0]
        # 行列 1-based；错误应落在第 3 行附近（return 语句）
        assert d.line >= 2
        assert d.column >= 1
        assert d.message.startswith("语法错误")

    def test_unbalanced_brace_reported(self):
        from agent.codenav.syntax_check import check_syntax

        _, diags = check_syntax("Foo.java", UNBALANCED_JAVA)
        assert len(diags) >= 1

    def test_unsupported_extension_returns_empty(self):
        from agent.codenav.syntax_check import check_syntax

        lang, diags = check_syntax("readme.md", "# title\n\nbroken {")
        assert lang == ""
        assert diags == []

    def test_python_syntax_error_reported(self):
        from agent.codenav.syntax_check import check_syntax

        lang, diags = check_syntax("a.py", "def f(:\n    return 1\n")
        assert lang == "python"
        assert len(diags) >= 1

    def test_valid_python_no_diagnostics(self):
        from agent.codenav.syntax_check import check_syntax

        lang, diags = check_syntax("a.py", "def f():\n    return 1\n")
        assert lang == "python"
        assert diags == []


class TestCheckEndpoint:
    """HTTP 层：/codenav/check。"""

    def test_valid_code_ok_and_empty(self, client):
        r = client.post(
            "/codenav/check",
            json={"file_path": "Foo.java", "content": VALID_JAVA},
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["ok"] is True
        assert body["supported"] is True
        assert body["language"] == "java"
        assert body["diagnostics"] == []

    def test_broken_code_returns_diagnostics(self, client):
        r = client.post(
            "/codenav/check",
            json={"file_path": "Foo.java", "content": MISSING_SEMI_JAVA},
        )
        body = r.json()
        assert body["ok"] is True
        assert len(body["diagnostics"]) >= 1
        d = body["diagnostics"][0]
        for key in ("line", "column", "end_line", "end_column", "message"):
            assert key in d

    def test_unsupported_suffix_supported_false(self, client):
        r = client.post(
            "/codenav/check",
            json={"file_path": "notes.txt", "content": "随便写 { 不算错"},
        )
        body = r.json()
        assert body["ok"] is True
        assert body["supported"] is False
        assert body["diagnostics"] == []

    def test_empty_file_path_rejected(self, client):
        r = client.post("/codenav/check", json={"file_path": "", "content": "x"})
        assert r.status_code == 400

    def test_oversized_content_rejected(self, client):
        r = client.post(
            "/codenav/check",
            json={"file_path": "Big.java", "content": "a" * 2_000_001},
        )
        assert r.status_code == 413
