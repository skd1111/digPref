# -*- coding: utf-8 -*-
"""校验已下载法规：front matter 完整、正文有附则/结尾条款、无截断、无模板残留。"""
import os
import re

ROOT = "legal-docs/法规库"
bad, warn, ok = [], [], 0
rows = []
for domain in sorted(os.listdir(ROOT)):
    ddir = os.path.join(ROOT, domain)
    if not os.path.isdir(ddir):
        continue
    for fn in sorted(os.listdir(ddir)):
        if not fn.endswith(".md"):
            continue
        p = os.path.join(ddir, fn)
        txt = open(p, encoding="utf-8").read()
        size = len(txt.encode())
        issues = []
        if not txt.startswith("---"):
            issues.append("无 front matter")
        else:
            parts = txt.split("---", 2)
            if len(parts) < 3:
                issues.append("front matter 未闭合")
            else:
                if "title:" not in parts[1]:
                    issues.append("缺 title")
                if "status:" not in parts[1]:
                    issues.append("缺 status")
        body = txt.split("---", 2)[-1]
        if len(body) < 200:
            issues.append(f"正文过短({len(body)})")
        if "{{<" in body or "{{%" in body:
            issues.append("残留模板短代码")
        if re.search(r"第[一二三四五六七八九十百千]+条", body) is None:
            issues.append("未见条款号")
        tail = body.strip()[-60:].replace("\n", " ")
        if not re.search(r"(附则|第[一二三四五六七八九十百千]+条|施行|公布|生效|备案)$|本.*自.*施行", tail):
            warn.append((fn, "结尾非典型条款", tail[-30:]))
        rows.append((domain, fn, size))
        if issues:
            bad.append((fn, issues))
        else:
            ok += 1

print(f"共 {len(rows)} 篇，完全正常 {ok}，异常 {len(bad)}，结尾存疑 {len(warn)}")
for fn, issues in bad:
    print("  BAD", fn, issues)
for fn, why, tail in warn[:12]:
    print("  WARN", fn, why, repr(tail))
by = {}
for domain, fn, size in rows:
    a = by.setdefault(domain, [0, 0, 0])
    a[0] += 1
    a[1] += size
    a[2] = min(a[2], size) if a[2] else size
for d, (n, tot, mn) in sorted(by.items()):
    print(f"{d}: {n} 篇 {tot//1024}KB 最小 {mn}B")
