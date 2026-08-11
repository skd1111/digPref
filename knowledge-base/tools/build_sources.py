"""扫描 sources/ 生成 99-原始材料清单.md，含文件真实类型校验"""

import os
import sys

sys.stdout.reconfigure(encoding="utf-8")
os.chdir(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))


def sniff(p):
    with open(p, "rb") as f:
        h = f.read(8)
    if h[:4] == b"%PDF":
        return "PDF"
    if h[:4] == b"\xd0\xcf\x11\xe0":
        return "DOC (OLE2)"
    if h[:2] == b"PK":
        return "DOCX/ZIP"
    if h[:5].lower() in (b"<!doc", b"<html"):
        return "HTML"
    if h[:1] == b"<":
        return "HTML/XML"
    return "TEXT"


def looks_like_error_page(p, kind):
    """HTML 文件若过小或含典型错误标识，视为无效"""
    if kind not in ("HTML", "HTML/XML", "TEXT"):
        return False
    sz = os.path.getsize(p)
    if sz < 2048:
        return True
    with open(p, "rb") as f:
        raw = f.read()
    s = None
    for e in ("utf-8", "gbk"):
        try:
            s = raw.decode(e)
            break
        except Exception:
            pass
    if not s:
        return False
    bad = ["404", "页面不存在", "Not Found", "访问被拒绝", "系统繁忙", "Forbidden"]
    head = s[:1500]
    return any(b in head for b in bad) and len(s) < 8000


DIRNAME = {
    "01-合规": "合规风险",
    "02-法律": "法律风险",
    "03-数据安全": "数据安全风险",
    "04-资金": "资金风险",
    "90-案例": "文档样本（合同/公告/招投标/制度）",
}

rows, total, suspect = [], 0, 0
for d in sorted(os.listdir("sources")):
    dp = os.path.join("sources", d)
    if not os.path.isdir(dp):
        continue
    for r, _, fs in os.walk(dp):
        for f in sorted(fs):
            p = os.path.join(r, f)
            rel = p.replace(os.sep, "/")
            kind = sniff(p)
            sz = os.path.getsize(p)
            ok = not looks_like_error_page(p, kind)
            if not ok:
                suspect += 1
            total += 1
            rows.append((DIRNAME.get(d, d), rel, kind, sz, ok))

lines = []
lines.append("<!-- 由 build_sources.py 自动生成，请勿手工编辑 -->\n")
lines.append("# 文档风险审查知识库 · 99 原始材料清单\n")
lines.append(f"> 本次共下载存档 **{total}** 份原始材料，存放于 `../sources/`。\n")
lines.append("## 说明\n")
lines.append(
    "- **类型**列为按文件头魔数（magic bytes）实测的真实格式，**不是**按扩展名推断。"
    "采集过程中曾出现「扩展名标为 .pdf、实际为 .doc」的情况，已按实测类型统一修正。\n"
)
lines.append(
    "- **有效性**列为自动校验结果：HTML 文件若体积过小或首部含 404/页面不存在等标识，标记为可疑。\n"
)
lines.append(f"- 自动校验结果：有效 **{total - suspect}** 份，可疑 **{suspect}** 份。\n")
lines.append(
    "\n> ⚠️ **重要局限**：本清单只覆盖「成功下载到本地」的材料。知识库正文引用的法规远多于此——"
    "详见各风险维度文件开头的「核验状态」小节。凡未出现在本清单中的法规，其条款号均**未经原文核验**。\n"
)

for dim in [
    "合规风险",
    "法律风险",
    "数据安全风险",
    "资金风险",
    "文档样本（合同/公告/招投标/制度）",
]:
    sub = [r for r in rows if r[0] == dim]
    if not sub:
        continue
    lines.append(f"\n## {dim}（{len(sub)} 份）\n")
    lines.append("| 文件 | 类型 | 大小 | 有效性 |")
    lines.append("|------|------|------|--------|")
    for _, rel, kind, sz, ok in sub:
        name = rel.split("/")[-1]
        size = f"{sz / 1024:.0f} KB" if sz < 1024 * 1024 else f"{sz / 1024 / 1024:.1f} MB"
        flag = "✅" if ok else "⚠️ 可疑"
        lines.append(f"| [`{name}`](../{rel}) | {kind} | {size} | {flag} |")

lines.append("\n---\n")
lines.append("## 采集失败 / 未能获取的重要材料\n")
lines.append(
    "以下材料在本次采集环境中**未能下载**，知识库中对它们的引用均属「未经原文核验」，"
    "使用前必须自行回查：\n"
)
lines.append("""
| 材料 | 失败原因 | 建议获取途径 |
|------|----------|--------------|
| 《中华人民共和国民法典》全文 | `npc.gov.cn` 文章 URL 302 跳转回首页；`gov.cn` 对应页面 404；国家法律法规数据库（flk.npc.gov.cn）为 Vue SPA，检索接口 `/law-search/search/list` 需未公开的参数结构，实测返回 500 | 人工访问 flk.npc.gov.cn 检索下载，或使用商业法规库 |
| 《民法典合同编通则司法解释》（法释〔2023〕13号） | 最高法官网对 curl 返回 Cloudflare 验证页 | 最高人民法院官网 / 中国裁判文书网 |
| 《反不正当竞争法》现行有效版本 | **本次下载到的是 1993 年版本**，非现行版；该法此后经多次修正 | ⚠️ **务必核实现行版本后再引用**，本库中相关条款号存疑 |
| 《中华人民共和国网络安全法》 | `npc.gov.cn` 返回首页框架，无正文 | cac.gov.cn 或法规数据库 |
| GB/T 35273《个人信息安全规范》等国家标准 | `openstd.samr.gov.cn` 需付费/授权下载 | 国家标准全文公开系统在线阅读；或购买标准文本 |
| JR/T 0171《个人金融信息保护技术规范》等金融行业标准 | 需付费获取 | 全国金融标准化技术委员会 |
| 最高法指导性案例、公报案例原文 | 裁判文书网、最高法官网对 curl 返回验证页 | 人工访问；或使用商业判例库 |
| 《电信和互联网用户个人信息保护规定》 | `miit.gov.cn` 在本环境被网络策略阻断 | 工信部官网 |
""")
lines.append(
    "\n> **采集环境限制说明**：本次采集在受限网络环境下进行，`WebSearch` 配额已用尽（200/200），"
    "`WebFetch` 对 `gov.cn` 等域名返回「无法验证域名安全性」，多个部委站点对 `curl` 返回 302/404/验证页。"
    "因此原文存档率偏低，这是本知识库当前最主要的短板。\n"
)

with open("knowledge-base/99-原始材料清单.md", "w", encoding="utf-8") as f:
    f.write("\n".join(lines))
print(f"共 {total} 份，可疑 {suspect} 份")
for dim in [
    "合规风险",
    "法律风险",
    "数据安全风险",
    "资金风险",
    "文档样本（合同/公告/招投标/制度）",
]:
    print(f"  {dim}: {len([r for r in rows if r[0] == dim])}")
for r in rows:
    if not r[4]:
        print("  ⚠️ 可疑:", r[1], r[3], "bytes")
