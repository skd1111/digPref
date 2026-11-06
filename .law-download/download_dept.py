# -*- coding: utf-8 -*-
"""抓取 gov.cn 政策文件库 / 国务院公报 上的部门规章全文，转成 md 落到 legal-docs/法规库/。

- 正文容器：div#UCAP-CONTENT（政策文件库）或 div#gongbao_content / .xxgk_content（公报）
- 第X章/第X条 行转 markdown 标题，配合知识库分块器的标题感知切分
"""
from __future__ import annotations

import json
import os
import re
import sys
import time

import httpx
import requests
from bs4 import BeautifulSoup

HDR = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120 Safari/537.36",
    "Accept-Language": "zh-CN,zh;q=0.9",
}
OUT_ROOT = "legal-docs/法规库"
CHAPTER_RE = re.compile(r"^(第[一二三四五六七八九十百零〇\d]+[章节篇编])$")
ARTICLE_RE = re.compile(r"^(第[一二三四五六七八九十百零〇\d]+条)(.*)$")


def pick_container(soup: BeautifulSoup) -> str | None:
    for sel in ["div#UCAP-CONTENT", "div#zoom", "div.zoom", "div.pages_content",
                "div#con_txt", "div.xxgk_content", "article"]:
        node = soup.select_one(sel)
        if node and len(node.get_text(strip=True)) > 800:
            return node.get_text("\n", strip=True)
    best, blen = None, 0
    for node in soup.select("div"):
        t = node.get_text("\n", strip=True)
        if len(t) > blen and "第一条" in t:
            best, blen = t, len(t)
    return best


def to_markdown(raw: str, title: str) -> str:
    out: list[str] = []
    for line in (l.strip() for l in raw.splitlines()):
        if not line:
            continue
        if CHAPTER_RE.match(line):
            out.append(f"## {line}")
            continue
        m = ARTICLE_RE.match(line)
        if m and len(line) < 40:
            out.append(f"**{m.group(1)}** {m.group(2)}".strip())
            continue
        out.append(line)
    body = "\n\n".join(out)
    # 公报页常见「第X条」单独成行、正文在下一行 → 合并为单行，避免分块时条号与内容分离
    body = re.sub(r"\*\*(第[^*\n]{1,12}条)\*\*\n+", r"**\1** ", body)
    body = re.sub(r"\n{3,}", "\n\n", body)
    return body.strip()


def sanitize(name: str) -> str:
    name = re.sub(r'[\\/:*?"<>|]', "", name)
    return re.sub(r"\s+", " ", name).strip()[:80]


def main() -> None:
    cfg = json.load(open(".law-download/dept_links.json", encoding="utf-8"))
    client = httpx.Client(timeout=60, headers=HDR, follow_redirects=True)
    sess = requests.Session()
    sess.headers.update(HDR)
    errs = []
    ok, bad = [], []
    for it in cfg["items"]:
        url, title, domain = it["url"], it["title"], it.get("domain", "06_部门规章与准则")
        txt = None
        for attempt in range(3):
            try:
                r = client.get(url)
                if r.status_code == 200:
                    txt = r.text
                    break
                errs.append((title, f"httpx {r.status_code}"))
            except Exception as e:
                errs.append((title, f"httpx {type(e).__name__} {str(e)[:60]}"))
            time.sleep(1 + attempt)
        if txt is None:  # 再退到 requests（本机对 gov.cn 实测更稳）
            try:
                r = sess.get(url, timeout=45)
                r.encoding = r.apparent_encoding or "utf-8"
                if r.status_code == 200:
                    txt = r.text
            except Exception as e:
                errs.append((title, f"requests {type(e).__name__} {str(e)[:60]}"))
        if txt is None:
            bad.append((title, url, "抓取失败"))
            continue
        soup = BeautifulSoup(txt, "html.parser")
        raw = pick_container(soup)
        if not raw:
            bad.append((title, url, "未定位到正文容器"))
            continue
        body = to_markdown(raw, title)
        if title.replace(" ", "") not in body.replace(" ", "") and "第一条" not in body:
            bad.append((title, url, f"正文疑似不含全文（{len(body)} 字）"))
            continue
        meta = "\n".join([
            "---",
            f"title: {title}",
            f"author: {it.get('org', '')}",
            "status: 有效",
            "group: 部门规章",
            f"urls:",
            f"  - {url}",
            "---",
        ])
        d = os.path.join(OUT_ROOT, domain)
        os.makedirs(d, exist_ok=True)
        p = os.path.join(d, sanitize(title) + ".md")
        with open(p, "w", encoding="utf-8", newline="\n") as f:
            f.write(meta + "\n# " + title + "\n\n" + body + "\n")
        ok.append((title, p, len(body)))
        print(f"OK {title} {len(body)}字 → {p}", flush=True)
    client.close()
    print(f"\n成功 {len(ok)}，失败 {len(bad)}")
    for e in errs[:8]:
        print("  ERR", e)
    for b in bad:
        print("  FAIL", b)
    json.dump({"ok": ok, "bad": bad}, open(".law-download/dept_result.json", "w", encoding="utf-8"),
              ensure_ascii=False, indent=1)


if __name__ == "__main__":
    sys.exit(main())
