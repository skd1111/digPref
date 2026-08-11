"""校验知识库中所有正则：能否编译 + 在正/反例上的行为"""

import glob
import os
import re
import sys

sys.stdout.reconfigure(encoding="utf-8")
os.chdir(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))

# 正例：真实高危条款表述；反例：无辜文本（不应命中）
POS = [
    "本公司享有本活动的最终解释权。",
    "一切解释权归主办方所有。",
    "商品一经售出，概不退换。",
    "甲方有权单方面变更本协议内容，无需事先通知乙方。",
    "在任何情况下本公司不承担任何间接损失责任。",
    "乙方逾期付款的，按每日 0.5% 支付滞纳金。",
    "一切后果由用户自行承担。",
    "经销商零售价不得低于官方指导价。",
    "购买本机必须同时购买延保服务方可享受保修。",
    "发生争议的，由甲方所在地法院管辖。",
]
NEG = [
    "本合同的解释权由双方协商确定，争议提交仲裁委员会裁决。",
    "商品自签收之日起七日内可无理由退换。",
    "任何一方变更本协议均须经对方书面同意，并提前三十日通知。",
    "双方各自承担因自身过错造成的损失。",
    "乙方逾期付款的，按全国银行间同业拆借中心公布的一年期贷款市场报价利率计付利息。",
    "经销商可自主决定零售价格。",
    "争议由被告住所地人民法院管辖。",
]

pats = []
for f in sorted(glob.glob("knowledge-base/0[1-5]-*.md")):
    with open(f, encoding="utf-8") as fh:
        t = fh.read()
    for b in re.findall(r"```(?:regex|text|python)?\n(.*?)```", t, re.S):
        for line in b.split("\n"):
            line = line.strip()
            if not line or line.startswith("#") or line.startswith("//"):
                continue
            if not any(c in line for c in [".{", r"\d", "(?", "|"]):
                continue
            if len(line) > 400 or line.startswith(
                ("def ", "return ", "import ", "print", "if ", "for ")
            ):
                continue
            pats.append((os.path.basename(f), line))

bad, ok = [], 0
pos_hit = set()
neg_hit = []
for src, p in pats:
    try:
        c = re.compile(p)
    except re.error as e:
        bad.append((src, p[:90], str(e)))
        continue
    ok += 1
    for s in POS:
        if c.search(s):
            pos_hit.add(s)
    for s in NEG:
        if c.search(s):
            neg_hit.append((p[:70], s[:40]))

print(f"提取正则 {len(pats)} 条 | 可编译 {ok} 条 | 编译失败 {len(bad)} 条\n")
if bad:
    print("=== 编译失败（下游模型直接使用会抛异常）===")
    for s, p, e in bad[:15]:
        print(f"  [{s}] {p}\n      -> {e}")
print(f"\n=== 正例覆盖：{len(pos_hit)}/{len(POS)} ===")
for s in POS:
    print(("  ✅ " if s in pos_hit else "  ❌ 无规则命中 ") + s)
print(f"\n=== 反例误报：{len(neg_hit)} 次 ===")
seen = set()
for p, s in neg_hit:
    if s in seen:
        continue
    seen.add(s)
    print(f'  ⚠️ "{s}"\n      被规则命中: {p}')
