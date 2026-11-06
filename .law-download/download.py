# -*- coding: utf-8 -*-
"""下载 selected.json 中的法规全文到 legal-docs/法规库/<域>/。

- 源：qundao/law-book（镜像自国家法律法规数据库 flk.npc.gov.cn，每篇保留原库链接）
- 通道：raw.githubusercontent + httpx 长连接池，Range/整文两种模式
- 产物：.md（含 front matter 元信息，供审核知识库直接上传）
- 断点续跑：已存在且非空的文件跳过
"""
from __future__ import annotations

import json
import os
import re
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import quote

import httpx

RAW = "https://raw.githubusercontent.com/qundao/law-book/main/{p}"
MIRRORS = [
    RAW,
    "https://cdn.jsdelivr.net/gh/qundao/law-book@main/{p}",
    "https://fastly.jsdelivr.net/gh/qundao/law-book@main/{p}",
    "https://testingcf.jsdelivr.net/gh/qundao/law-book@main/{p}",
]
OUT_ROOT = "legal-docs/法规库"
HDR = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) law-download"}
# front matter 里保留的元信息键（其余丢弃，减少噪声）
KEEP_KEYS = ["title", "LinkTitle", "author", "publication_date", "effective_date",
             "status", "group", "categories", "tags", "urls"]


def sanitize(name: str) -> str:
    name = re.sub(r'[\\/:*?"<>|《》]', "", name)
    name = re.sub(r"\s+", " ", name).strip()
    return name[:80]


def parse_front_matter(txt: str) -> tuple[dict, str]:
    parts = txt.split("---", 2)
    fm_raw, body = (parts[1], parts[2]) if len(parts) >= 3 else ("", txt)
    meta: dict = {}
    list_key = None
    for line in fm_raw.splitlines():
        if not line.strip():
            continue
        if line.startswith("  - ") and list_key:
            meta.setdefault(list_key, []).append(line[4:].strip())
            continue
        if ":" in line and not line.startswith(" "):
            k, _, v = line.partition(":")
            k, v = k.strip(), v.strip()
            if v == "":
                list_key = k
                meta[k] = []
            else:
                list_key = None
                meta[k] = v.strip("'\"")
    return meta, body.strip()


def clean_body(body: str) -> str:
    """去掉 Hugo 短代码与图片引用，保留正文。"""
    body = re.sub(r"\{\{<[^>]*>\}\}", "", body)
    body = re.sub(r"!\[[^\]]*\]\([^)]*\)", "", body)
    body = re.sub(r"\n{3,}", "\n\n", body)
    return body.strip()


def render(meta: dict, body: str) -> str:
    lines = ["---"]
    for k in KEEP_KEYS:
        if k not in meta:
            continue
        v = meta[k]
        if isinstance(v, list):
            if not v:
                continue
            lines.append(f"{k}:")
            lines.extend(f"  - {x}" for x in v)
        else:
            lines.append(f"{k}: {v}")
    lines.append("---")
    lines.append("")
    return "\n".join(lines) + "\n" + body + "\n"


def main() -> None:
    sel = json.load(open(".law-download/selected.json", encoding="utf-8"))
    items = []
    for domain, rows in sel.items():
        for r in rows:
            r2 = dict(r)
            r2["_domain"] = domain
            items.append(r2)
    os.makedirs(OUT_ROOT, exist_ok=True)
    print(f"待下载 {len(items)} 篇 → {OUT_ROOT}", flush=True)

    client = httpx.Client(timeout=120, headers=HDR, limits=httpx.Limits(max_connections=20), follow_redirects=True)
    os.makedirs(OUT_ROOT, exist_ok=True)
    results = []

    def work(it: dict):
        domain, path = it["_domain"], it["path"]
        year = (it["date"] or "")[:4]
        stem = sanitize(f"{it['title']}_{year}") if year else sanitize(it["title"])
        out_dir = os.path.join(OUT_ROOT, domain)
        out = os.path.join(out_dir, stem + ".md")
        if os.path.exists(out) and os.path.getsize(out) > 500:
            return {"path": out, "ok": True, "cached": True, **{k: it[k] for k in ("title", "_domain", "status", "date", "src", "org", "size")}}
        path_q = "/".join(quote(seg) for seg in path.split("/"))
        txt = None
        last_url = ""
        # 镜像逐个轮询，每轮失败后指数退避（本机对 raw 有间歇性连接重置）
        for round_no in range(4):
            for tpl in MIRRORS:
                url = tpl.format(p=path_q)
                last_url = url
                try:
                    r = client.get(url)
                    if r.status_code == 200 and r.text.lstrip().startswith("---"):
                        txt = r.text
                        break
                except Exception:
                    continue
            if txt is not None:
                break
            time.sleep(3.0 * (round_no + 1))
        if txt is None:
            return {"path": out, "ok": False, "title": it["title"], "url": last_url}
        meta, body = parse_front_matter(txt)
        if len(txt.encode()) < it["size"] * 0.6:
            return {"path": out, "ok": False, "title": it["title"], "url": last_url,
                    "reason": f"内容偏短 {len(txt.encode())}B < 期望 {it['size']}B"}
        content = render(meta, clean_body(body))
        os.makedirs(out_dir, exist_ok=True)
        with open(out, "w", encoding="utf-8", newline="\n") as f:
            f.write(content)
        return {"path": out, "ok": True, "cached": False,
                "title": meta.get("LinkTitle") or it["title"], "_domain": domain,
                "status": meta.get("status", it["status"]), "date": it["date"],
                "effective": meta.get("effective_date", it.get("effective", "")),
                "src": it["src"], "org": it["org"], "size": len(content.encode())}

    t0, done_n = time.time(), 0
    with ThreadPoolExecutor(max_workers=8) as pool:
        for res in pool.map(work, items):
            done_n += 1
            results.append(res)
            if done_n % 20 == 0:
                left = sum(1 for x in results if not x["ok"])
                print(f"  {done_n}/{len(items)} fail={left} {done_n/(time.time()-t0):.2f}it/s", flush=True)
    client.close()

    ok = [r for r in results if r["ok"]]
    bad = [r for r in results if not r["ok"]]
    json.dump(results, open(".law-download/downloaded.json", "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    total_kb = sum(os.path.getsize(r["path"]) for r in ok if os.path.exists(r["path"]))
    print(f"\n成功 {len(ok)} 篇（{total_kb/1024:.0f}KB），失败 {len(bad)} 篇，耗时 {(time.time()-t0)/60:.1f}min")
    for b in bad:
        print("  FAIL", b["title"], b["url"][:100])


if __name__ == "__main__":
    sys.exit(main())
