"""BUGFIX #136 —— 自由文本选项枚举 → clarify 选项卡（确定性硬兜底）。

真实翻车场景（2026-08-25 会话 2ba1a776 / 截图）：
  - 模型追问给「选项A：精简版 / 选项B：标准版 / 选项C：详细版」自由文本，
    不走 ASK_USER 结构路径 → 前端 ClarifyCard 无卡可点；
  - 工具循环候选方案「- o1. 创建空白演示文稿… 回复编号即可继续（例如：o2）」
    同样只是纯文本，用户只能手打编号（用户反馈：想要可点选项卡）。
透传/直答出口必须确定性补 ```clarify 块；解析不出选项一律不动原文。
"""

from __future__ import annotations

import json

import pytest
from agent.graph.nodes.responder import (
    _attach_clarify_from_text,
    responder_node,
)

# 真实会话 id=57 原文（精简）：选项A/B/C 与数字编号题干混排
ASK_OPTIONS_ABC = (
    "做这个 PPT 之前想跟您确认两点：\n"
    "\n"
    "1. **保存路径**：文件存到哪里？例如 `~/Desktop/自我介绍.pptx`。\n"
    "2. **页数与风格**：希望多详细？\n"
    "   - 选项A：**精简版**（约 5 页）：基本信息 + 核心能力，适合快速分享\n"
    "   - 选项B：**标准版**（约 8-10 页）：增加技术特点、适用场景等\n"
    "   - 选项C：**详细版**（12+ 页）：增加功能说明、案例对比、Q&A 等\n"
    "\n"
    "请告诉我路径和选项（A/B/C）。"
)

# 截图候选方案原文（精简）：o1/o2/o3 编号 + 回复编号引导语
CANDIDATES_O123 = (
    "候选方案（我无法直接创建文件，只能提供命令）：\n"
    "\n"
    "- o1. 创建空白演示文稿：生成后内容为空，需完全手动填充（不执行）\n"
    "- o2. 创建带自我介绍结构的演示文稿：占位文本为通用示例（推荐）\n"
    "- o3. 仅提供命令模板：您可自行调整（不执行）\n"
    "\n"
    "回复编号即可继续（例如：o2）"
)


def _parse_clarify(answer: str) -> list[dict]:
    assert "```clarify" in answer, f"未附加 clarify 块：{answer[:200]}"
    block = answer.split("```clarify", 1)[1].rsplit("```", 1)[0]
    return json.loads(block)


@pytest.mark.asyncio
async def test_passthrough_abc_options_get_clarify_card():
    """选项A/B/C 自由文本 → 透传终答带 3 选项卡片（选项格式优先于数字题干）。"""
    state = {"final_answer": ASK_OPTIONS_ABC, "user_prompt": "做一个介绍你自己的ppt"}
    out = await responder_node(state, llm=None)  # type: ignore[arg-type]
    items = _parse_clarify(out["final_answer"])
    options = items[0]["options"]
    assert len(options) == 3
    assert options[0]["text"].startswith("精简版")
    assert options[2]["text"].startswith("详细版")
    # 题干取选项前最后一段正文（页数与风格那一条）
    assert "页数与风格" in items[0]["question"]


@pytest.mark.asyncio
async def test_passthrough_o123_candidates_get_clarify_card():
    """o1/o2/o3 编号 + 回复编号引导语 → 卡片 3 选项，「推荐」字样标推荐项。"""
    state = {"final_answer": CANDIDATES_O123, "user_prompt": "AI自我介绍PPT制作"}
    out = await responder_node(state, llm=None)  # type: ignore[arg-type]
    items = _parse_clarify(out["final_answer"])
    options = items[0]["options"]
    assert len(options) == 3
    assert options[0]["text"].startswith("创建空白演示文稿")
    recommended = [o for o in options if o["recommended"]]
    assert len(recommended) == 1
    assert recommended[0]["text"].startswith("创建带自我介绍结构")


def test_plain_prose_untouched():
    """普通正文（无选项枚举）不加卡片。"""
    answer = "已为您生成报告，包含三个部分：概况、风险与建议。请查收。"
    assert _attach_clarify_from_text(answer) == answer


def test_existing_clarify_untouched():
    """已有 clarify 围栏不重复附加。"""
    answer = '请选择\n\n```clarify\n[{"question": "x", "options": []}]\n```'
    assert _attach_clarify_from_text(answer) == answer


def test_numbered_steps_without_cue_untouched():
    """普通步骤列表（无选择引导语）不误加卡片。"""
    answer = "操作步骤如下：\n1. 打开设置页\n2. 点击模型管理\n3. 启用云端后端"
    assert _attach_clarify_from_text(answer) == answer


def test_single_option_line_untouched():
    """仅一条编号行不构成枚举，不动原文。"""
    answer = "注意：\n1. 凭证只经系统 keyring 存取。"
    assert _attach_clarify_from_text(answer) == answer


# ---- BUGFIX #149（2026-08-25）：引导语盲区与多选标记 ----------------

# 真实会话原文（精简）：「任一信息」类引导语 + 编号选项，此前因引导语集合缺失漏卡
ANY_INFO_OPTIONS = (
    "收到，风格定为「对外宣传/路演」。\n"
    "\n"
    "请提供以下任一信息即可推进：\n"
    "1. 产品/项目名称（一句话即可，如「XX 智能客服平台」）\n"
    "2. 或直接说明是否需要我先帮您梳理一份路演 PPT 大纲？\n"
    "\n"
    "请问您要宣传的产品/项目是什么，或者需要先出大纲？"
)

# 真实会话原文（精简）：题干带「可多选」的编号枚举，此前因缺引导语漏卡、且无多选交互
MULTI_SELECT_OPTIONS = (
    "关于这个介绍 PPT，您希望侧重哪些内容？（可多选或补充）\n"
    "1. **基础身份**：我是谁、开发公司等\n"
    "2. **核心能力**：我能做什么（编程、写作、分析等）\n"
    "3. **使用场景**：典型应用案例\n"
    "\n"
    "您可以回复例如：基础身份+核心能力。"
)


def test_any_info_cue_gets_clarify_card():
    """「提供以下任一信息」引导语 + 编号选项 → 补卡（#149 引导语盲区修复）。"""
    items = _parse_clarify(_attach_clarify_from_text(ANY_INFO_OPTIONS))
    options = items[0]["options"]
    assert len(options) == 2
    assert options[0]["text"].startswith("产品/项目名称")
    assert items[0].get("multi") is False


def test_multi_select_cue_marks_multi():
    """题干含「可多选」→ 补卡且 multi=true（前端渲染复选框，#149）。"""
    items = _parse_clarify(_attach_clarify_from_text(MULTI_SELECT_OPTIONS))
    assert items[0]["multi"] is True
    assert len(items[0]["options"]) == 3
    # 题干保留多选提示字样（前端据此另加「可多选」提示）
    assert "多选" in items[0]["question"]


def test_multi_cue_not_in_stem_stays_single():
    """「多选」字样不在题干（选项行之后才出现）不误标 multi。"""
    answer = (
        "请选择一种风格：\n"
        "1. 简洁实用：页面干净\n"
        "2. 商务高级：突出专业\n"
        "\n"
        "说明：本题单选，多选需求请自定义输入说明。"
    )
    items = _parse_clarify(_attach_clarify_from_text(answer))
    assert items[0]["multi"] is False


# ---- BUGFIX #150（2026-08-26）：粗体选项标记 + 口语化引导语 ----------------

# 真实会话原文（精简，sessions.db 2026-08-26）：云端模型高频输出 **A. xxx**
# 粗体选项 + 「从下面挑一个」口语引导，此前正则不识别粗体前缀 → 漏卡，
# 用户手打「A」回复后模型丢上下文（只收到「A」→ 回「信息不完整」）。
BOLD_OPTIONS_ABC = (
    "收到～在我开始动笔之前，需要先确认一下介绍的对象和用途，避免方向跑偏。\n"
    "\n"
    "请您从下面挑一个（或自行补充），告诉我后我立刻起草：\n"
    "\n"
    "**A. 介绍 EAIDE 企业 AI IDE 本身**\n"
    "   - 用途示例：① 对客户宣讲的开场白 ② 给领导汇报的项目概述\n"
    "\n"
    "**B. 介绍贵公司某个具体产品/平台**\n"
    "   - 请补充：产品名称、核心定位\n"
    "\n"
    "**C. 介绍某个内部项目/系统**\n"
    "   - 请补充：项目代号、所属业务线\n"
    "\n"
    "您回我一个 **A/B/C + 用途编号**，我直接出草稿给您过目 ✅"
)


def test_bold_lettered_options_get_clarify_card():
    """**A. xxx** 粗体选项 + 「挑一个」引导语 → 补卡（#150 真实翻车回归）。"""
    items = _parse_clarify(_attach_clarify_from_text(BOLD_OPTIONS_ABC))
    options = items[0]["options"]
    assert len(options) == 3
    assert options[0]["text"].startswith("介绍 EAIDE 企业 AI IDE 本身")
    assert options[2]["text"].startswith("介绍某个内部项目")
    # 粗体星号被清洗，选项文本不带 **
    assert all("**" not in o["text"] for o in options)


def test_bold_numbered_options_get_clarify_card():
    """**1. xxx** 粗体数字编号 + 引导语同样成卡。"""
    answer = (
        "请提供以下任一信息：\n"
        "**1. 产品/项目名称**（一句话即可）\n"
        "**2. 或先出大纲**（确认后再生成）"
    )
    items = _parse_clarify(_attach_clarify_from_text(answer))
    assert len(items[0]["options"]) == 2
    assert items[0]["options"][0]["text"].startswith("产品/项目名称")
    assert items[0]["multi"] is False


# ---- 2026-08-27：多维参数确认式追问 → 「按默认执行 / 自定义」确认卡 ----
#
# 真实翻车场景（用户截图）：PPT 参数确认终答「请直接回复例如：'10 页 / …'，
# 缺省我会按 '…' 执行」，编号行是多维参数非互斥选项 → 枚举路径不采信，
# 前端无卡可点（用户反馈：为什么不是选项卡）。
PPT_PARAMS_CONFIRM = (
    "给 EAIDE 智能体（你自己）做一份介绍 PPT 的几项关键参数，我直接据此动手做，不再追问：\n"
    "\n"
    "1. 页数：8 / 10 / 12 页（推荐 10 页，平衡完整性与时长）\n"
    "2. 使用场合：公司内部分享 / 给客户介绍 / 培训用 / 其他\n"
    "3. 风格基调：\n"
    "   A. 现代简约 + 工程感（蓝灰主调，信息密度适中，最稳）\n"
    "   B. Storytelling（深色背景，一条主线串到底，氛围强）\n"
    "   C. 数据/能力导向（卡片网格，强调能力清单与示例）\n"
    "4. 版式库：A. 内置 presentation_core 16:9 版式库 / B. 自由设计\n"
    "5. 备注：是否需要备注/讲稿旁白：要 / 不要\n"
    "\n"
    "请直接回复例如：'10 页 / 客户介绍 / A / A / 不要' 即可，"
    "缺省我会按 '10 页 / 内部分享 / A / A / 不要' 执行。"
)


def test_param_confirm_answer_gets_default_combo_card():
    """参数确认式终答 → 二元确认卡（默认组合标推荐），不把多维参数当互斥选项。"""
    items = _parse_clarify(_attach_clarify_from_text(PPT_PARAMS_CONFIRM))
    assert len(items) == 1
    options = items[0]["options"]
    assert len(options) == 2
    assert options[0]["recommended"] is True
    # 默认组合从「缺省我会按 '…' 执行」中抽出，引号/空白被清洗
    assert options[0]["text"] == "按默认配置执行：10 页 / 内部分享 / A / A / 不要"
    assert options[1]["recommended"] is False


def test_reply_cue_without_default_combo_untouched():
    """只有回复引导语、无缺省组合 → 不构成确认卡，不误伤。"""
    answer = "方案已就绪。\n\n请直接回复例如：'方案一' 即可，我马上开工。"
    assert _attach_clarify_from_text(answer) == answer


def test_default_combo_without_reply_cue_untouched():
    """只有缺省组合、无回复引导语 → 不加卡（宁可漏加不可误伤）。"""
    answer = "参数说明：\n1. 页数可选 8/10/12\n2. 风格可选 A/B\n\n缺省按 '10 页 / A' 执行。"
    assert _attach_clarify_from_text(answer) == answer
