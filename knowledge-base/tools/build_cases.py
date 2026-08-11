"""汇总四个风险维度的案例，按可核实性分级，生成 90-案例库.md"""

import os
import re
import sys

sys.stdout.reconfigure(encoding="utf-8")
os.chdir(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))

SRC = [
    ("01-合规风险.md", "合规风险"),
    ("02-法律风险.md", "法律风险"),
    ("03-数据安全风险.md", "数据安全风险"),
    ("04-资金风险.md", "资金风险"),
]


def split_cases(txt):
    """按 ### 案例 切块"""
    parts = re.split(r"\n(?=###\s*案例)", txt)
    out = []
    for p in parts:
        if not re.match(r"###\s*案例", p):
            continue
        title = p.split("\n", 1)[0].lstrip("#").strip()
        # 截断到下一个 ## 级标题
        body = re.split(r"\n##\s", p)[0]
        out.append((title, body))
    return out


def field(body, *names):
    for n in names:
        m = re.search(r"\*\*" + n + r"\*\*\s*[：:]\s*(.+)", body)
        if m:
            return m.group(1).strip()
    return ""


VAGUE = ("某", "待核实", "未载明", "未公开")

# 真正的案号/文号形态：(2021)最高法民终123号 / 〔2025〕1号 / 指导案例第24号 / 沪市监处字第X号
DOCNO = re.compile(
    r"[（(〔]\s*\d{4}\s*[）)〕]|〔\s*\d{4}\s*〕|指导案例第\s*\d+\s*号|第\s*\d+\s*号|\d{4}\s*〕\s*\d+\s*号"
)


def has_real_docno(doc):
    """'最高人民法院指导性案例'这类是类别标签，不是案号，必须排除"""
    if not doc or "待核实" in doc:
        return False
    return bool(DOCNO.search(doc))


def grade(title, body):
    doc = field(body, "文号", "案号", "案号/文号")
    subj = field(body, "主体", "当事方", "当事人")
    full = title + " " + body
    has_doc = has_real_docno(doc)
    named = bool(subj) and not any(v in subj for v in VAGUE)
    # 公开可查证的重大事件：当事人为具名实体且事件本身广泛见诸官方通报
    famous = any(
        k in full
        for k in ["滴滴出行", "Meta", "亚马逊", "郭兵", "杭州野生动物世界", "江苏省消保委"]
    )
    if has_doc and named:
        return "A"
    if has_doc or named or famous:
        return "B"
    return "C"


GRADE_DESC = {
    "A": ("A · 可直接引用", "主体名称与文号/案号齐全，可回官方渠道逐字核对"),
    "B": ("B · 引用需附提示", '主体或文号其一可查，或属公开重大事件；引用时须提示"部分字段未核实"'),
    "C": (
        "C · 仅作模式参考",
        "主体脱敏且文号未核到；**禁止**作为处罚依据或结论支撑，仅可参考其识别模式",
    ),
}

buckets = {"A": [], "B": [], "C": []}
total = 0
for fn, dim in SRC:
    p = os.path.join("knowledge-base", fn)
    if not os.path.exists(p):
        continue
    with open(p, encoding="utf-8") as f:
        txt = f.read()
    for title, body in split_cases(txt):
        g = grade(title, body)
        buckets[g].append((dim, title, body))
        total += 1

lines = []
lines.append(
    "<!-- 由 build_cases.py 自动汇总自各风险维度文件，请勿手工编辑；改动请改源文件后重新生成。 -->\n"
)
lines.append("# 文档风险审查知识库 · 90 案例库\n")
lines.append(f"> 汇总自 `01`–`04` 四个风险维度文件，共 **{total}** 个案例。\n")
lines.append("## ⚠️ 案例可核实性分级（引用前必读）\n")
lines.append(
    "真实案例是本知识库最容易被下游模型「当作权威事实复述」的部分，也是幻觉传播风险最高的部分。"
    "本库对每个案例标注可核实性等级，**下游模型引用时必须同时传递该等级**。\n"
)
lines.append("| 等级 | 数量 | 含义 | 使用限制 |")
lines.append("|------|------|------|----------|")
for g in "ABC":
    name, desc = GRADE_DESC[g]
    lines.append(
        f"| **{name}** | {len(buckets[g])} | {desc} | "
        + {
            "A": "可作为依据引用",
            "B": '可引用，须附"部分字段待核实"提示',
            "C": "**不得**作为依据，仅可复用其识别模式",
        }[g]
        + " |"
    )
lines.append("")
lines.append(
    "> **给下游模型的硬性纪律**：\n"
    "> 1. 引用 C 级案例时，**禁止**输出具体处罚金额、文号、当事人名称——这些字段在本库中即为未核实状态。\n"
    "> 2. 案例中的「对审查的启示 / 可复用机器信号」部分**不受等级限制**，因为那是从条款文本中提炼的识别模式，与案例真伪无关，可放心使用。\n"
    '> 3. 若用户要求提供"判例支持"，只能给 A 级；无 A 级可给时，应如实告知"本库无可直接引用的判例"，而不是降级拿 B/C 充数。\n'
)
lines.append("\n---\n")

for g in "ABC":
    if not buckets[g]:
        continue
    name, desc = GRADE_DESC[g]
    lines.append(f"\n# {name}\n\n> {desc}\n")
    for dim, title, body in buckets[g]:
        b = re.sub(r"\A###\s*", "", body).split("\n", 1)
        rest = b[1] if len(b) > 1 else ""
        lines.append(f"\n## [{dim}] {title}\n")
        lines.append(rest.strip() + "\n")

with open("knowledge-base/90-案例库.md", "w", encoding="utf-8") as f:
    f.write("\n".join(lines))
print(
    f"总案例 {total} | A级 {len(buckets['A'])} | B级 {len(buckets['B'])} | C级 {len(buckets['C'])}"
)
for g in "ABC":
    print(f"\n--- {g} 级 ---")
    for dim, t, _ in buckets[g]:
        print(f"  [{dim}] {t[:60]}")
