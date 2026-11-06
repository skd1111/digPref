# -*- coding: utf-8 -*-
"""生成 legal-docs/法规库/INDEX.md 总清单（按域分表：名称/层级/制定机关/公布/施行/效力/来源）。"""
import json
import os

ROOT = "legal-docs/法规库"
DOMAIN_LABEL = {
    "00_通用基础": "通用基础（宪法/民法典/刑法/行政法）",
    "01_财税": "财税（预算/会计/审计/税收/海关/招投标/政府采购）",
    "02_企业": "企业（公司/合伙/破产/劳动/反垄断/质量安全/营商环境）",
    "03_金融": "金融（银行/证券/保险/信托/票据/反洗钱/支付/私募）",
    "04_科技与数据": "科技与数据（数据安全/网络安全/个人信息/知识产权/科技进步）",
    "05_司法解释": "核心司法解释（合同/公司/破产/劳动/借贷/知产/金融）",
    "06_部门规章与准则": "部门规章（网信/市场监管/证监会，来自 gov.cn 政策文件库）",
}
KB_CATEGORY = {
    "00_通用基础": "不分类（通用，始终可召回）",
    "01_财税": "finance（财务/财税）",
    "02_企业": "legal（法务/合同）",
    "03_金融": "finance（财务/财税）",
    "04_科技与数据": "data_security（数据安全）",
    "05_司法解释": "legal（法务/合同）",
    "06_部门规章与准则": "compliance（合规/制度）",
}


def read_meta(path):
    with open(path, encoding="utf-8") as f:
        txt = f.read()
    meta = {}
    if txt.startswith("---"):
        for line in txt.split("---", 2)[1].splitlines():
            if ":" in line and not line.startswith(" "):
                k, _, v = line.partition(":")
                meta[k.strip()] = v.strip().strip("'\"")
    return meta


lines = ["# 法规库总清单", ""]
total = 0
for domain in sorted(os.listdir(ROOT)):
    ddir = os.path.join(ROOT, domain)
    if not os.path.isdir(ddir):
        continue
    files = sorted(f for f in os.listdir(ddir) if f.endswith(".md"))
    total += len(files)
    lines += [f"## {DOMAIN_LABEL.get(domain, domain)}（{len(files)} 篇）", "",
              f"知识库上传建议分类：{KB_CATEGORY.get(domain, '')}", "",
              "| 法规 | 层级 | 制定机关 | 公布 | 施行 | 效力 | 来源 |",
              "| --- | --- | --- | --- | --- | --- | --- |"]
    for fn in files:
        m = read_meta(os.path.join(ddir, fn))
        name = m.get("LinkTitle") or m.get("title") or fn[:-3]
        link = ""
        with open(os.path.join(ddir, fn), encoding="utf-8") as f:
            head = f.read(2000)
        for line in head.splitlines():
            s = line.strip()
            if s.startswith("- http"):
                link = s[2:].strip()
                break
        src = "国家法律法规数据库" if "flk.npc.gov.cn" in link else ("中国政府网" if "gov.cn" in link else "")
        lines.append(f"| [{name}]({domain}/{fn}) | {m.get('group','')} | {m.get('author','')} | "
                     f"{m.get('publication_date','')[:10]} | {m.get('effective_date','')[:10]} | {m.get('status','')} | {src} |")
    lines.append("")

lines.insert(1, f"\n共 {total} 篇，全部为现行有效（有效/已修改）文本；抓取通道与需人工补录清单见 [README.md](README.md)。\n")
with open(os.path.join(ROOT, "INDEX.md"), "w", encoding="utf-8", newline="\n") as f:
    f.write("\n".join(lines) + "\n")
print(f"INDEX.md 写入完成，共 {total} 篇")
json.dump({"total": total}, open(".law-download/index_stat.json", "w", encoding="utf-8"))
