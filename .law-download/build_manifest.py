# -*- coding: utf-8 -*-
"""批量拉取 law-book 各法规 md 的 front matter，建立本地索引 manifest.json。

通道：httpx 长连接池（HTTP/1.1 keep-alive）+ Range 只取头部 3KB，支持断点续跑。
"""
from __future__ import annotations

import json
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import quote

import httpx

TREE = ".law-download/tree.json"
OUT = ".law-download/manifest.json"
RAW = "https://raw.githubusercontent.com/qundao/law-book/main"
HDR = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) law-index"}
DEFAULT_GROUPS = ["法律", "行政法规", "宪法", "监察法规", "司法解释", "appendix"]


def parse_header(txt: str) -> dict:
    """极简 YAML front matter 解析（只取标量与两空格缩进列表）。"""
    parts = txt.split("---", 2)
    if len(parts) < 3:
        return {}
    out: dict = {}
    list_key = None
    for line in parts[1].splitlines():
        if not line.strip():
            continue
        if line.startswith("  - ") and list_key:
            out.setdefault(list_key, []).append(line[4:].strip())
            continue
        if ":" in line and not line.startswith(" "):
            k, _, v = line.partition(":")
            k, v = k.strip(), v.strip()
            if v == "":
                list_key = k
                out[k] = []
            else:
                list_key = None
                out[k] = v.strip("'\"")
    return out


def main() -> None:
    groups = sys.argv[1:] or DEFAULT_GROUPS
    tree = json.load(open(TREE, encoding="utf-8"))
    sizes = {r["path"]: r.get("size", 0) for r in tree}
    done: dict = {}
    if os.path.exists(OUT):
        done = json.load(open(OUT, encoding="utf-8"))
    targets = []
    for r in tree:
        p = r["path"]
        if not p.endswith(".md") or not p.startswith("content/") or "/images/" in p:
            continue
        if p.split("/")[1] not in groups:
            continue
        if p in done:
            continue
        targets.append(p)
    targets.sort(key=lambda p: (p.split("/")[1], p))
    limit = int(os.environ.get("LM_LIMIT", "0"))
    if limit:
        targets = targets[:limit]
    conc = int(os.environ.get("LM_CONC", "10"))
    print(f"groups={groups} to fetch={len(targets)} already={len(done)} conc={conc}", flush=True)
    if not targets:
        return

    client = httpx.Client(timeout=60, headers=HDR, limits=httpx.Limits(max_connections=conc * 2))

    def work(path: str):
        url = RAW + "/" + "/".join(quote(seg) for seg in path.split("/"))
        for attempt in range(4):
            try:
                r = client.get(url, headers={"Range": "bytes=0-3000"})
                if r.status_code in (200, 206) and r.text.lstrip().startswith("---"):
                    meta = parse_header(r.text)
                    meta["_path"] = path
                    meta["_size"] = sizes.get(path, 0)
                    return path, meta
                if r.status_code == 404:
                    return path, None
            except Exception:
                pass
            time.sleep(1.0 + attempt * 2)
        return path, None

    cnt, fail, t0 = 0, 0, time.time()
    try:
        with ThreadPoolExecutor(max_workers=conc) as pool:
            for path, meta in pool.map(work, targets):
                cnt += 1
                if meta:
                    done[path] = meta
                else:
                    fail += 1
                if cnt % 50 == 0:
                    json.dump(done, open(OUT, "w", encoding="utf-8"), ensure_ascii=False)
                    rate = cnt / (time.time() - t0)
                    eta = (len(targets) - cnt) / rate / 60 if rate else 0
                    print(f"  {cnt}/{len(targets)} fail={fail} {rate:.1f}req/s eta={eta:.0f}m", flush=True)
    finally:
        client.close()
        json.dump(done, open(OUT, "w", encoding="utf-8"), ensure_ascii=False)
    print(f"DONE indexed={len(done)} fail={fail} in {(time.time()-t0)/60:.1f}min", flush=True)


if __name__ == "__main__":
    sys.exit(main())
