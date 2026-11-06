# -*- coding: utf-8 -*-
"""在 manifest 中按关键词检索，用于补充白名单遗漏 / 确认某文件是否在库。"""
import json
import sys

man = json.load(open(".law-download/manifest.json", encoding="utf-8"))
keys = sys.argv[1:] or []
for p, m in sorted(man.items(), key=lambda kv: (kv[1].get("group", ""), kv[1].get("title", ""))):
    t = (m.get("title") or "") + " " + " ".join(m.get("tags") or []) + " " + " ".join(m.get("categories") or [])
    if any(k in t for k in keys):
        print(f"[{m.get('group','?'):5s}|{m.get('status','?'):3s}|{(m.get('publication_date') or m.get('date') or '')[:10]}] "
              f"{m.get('LinkTitle') or m.get('title')} <{m.get('author','')}> {m.get('_size',0)//1024}KB")
