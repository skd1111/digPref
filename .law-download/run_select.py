# -*- coding: utf-8 -*-
"""按 selection.py 规则从 manifest 里挑选法规，输出 selected.json + 概览。

注意：本文件不能命名为 select.py —— 会遮蔽标准库 select，导致 httpx 等网络库全线报错。
"""
from __future__ import annotations

import json
import sys
from collections import defaultdict

sys.path.insert(0, ".law-download")
from selection import base_title, best_versions_per_base, eligible, pick_domain  # noqa: E402

man = json.load(open(".law-download/manifest.json", encoding="utf-8"))
buckets: dict[str, list[dict]] = defaultdict(list)
status_cnt: dict[str, int] = defaultdict(int)
group_cnt: dict[str, int] = defaultdict(int)

for path, m in man.items():
    if not m.get("title"):
        continue
    status_cnt[m.get("status", "?")] += 1
    group_cnt[m.get("group", "?")] += 1
    if not eligible(m):
        continue
    d = pick_domain(m)
    if not d:
        continue
    buckets[d].append({
        "path": path,
        "title": m.get("LinkTitle") or m.get("title"),
        "base": base_title(m.get("title")),
        "status": m.get("status"),
        "group": m.get("group", path.split("/")[1]),
        "org": m.get("author", ""),
        "date": m.get("publication_date") or m.get("date", ""),
        "effective": m.get("effective_date", ""),
        "size": m.get("_size", 0),
        "src": (m.get("urls") or [""])[0],
    })

final: dict[str, list[dict]] = {}
for d, rows in buckets.items():
    kept = best_versions_per_base(rows)
    kept.sort(key=lambda r: (r["base"], r["date"]))
    final[d] = kept

print("status 分布:", dict(status_cnt))
print("group 分布:", dict(group_cnt))
total = 0
for d in sorted(final):
    before = len(buckets[d])
    total += len(final[d])
    print(f"\n### {d}: {len(final[d])} 篇（去重前 {before}）")
    for r in final[d]:
        print(f"  [{r['group']:5s}|{r['status']:3s}|{r['date'][:10]}] {r['title']} ({r['size']//1024}KB) <{r['org']}>")

json.dump(final, open(".law-download/selected.json", "w", encoding="utf-8"), ensure_ascii=False, indent=1)
print(f"\n总计 {total} 篇 → .law-download/selected.json")
