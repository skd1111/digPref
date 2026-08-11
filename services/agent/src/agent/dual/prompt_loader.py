"""Phase 18 双模式执行规范 —— 提示词资产加载与精简注入版。

完整版：`prompts/code_work_system_prompt.md`（角色定义 / 核心原则 / 路由规则 /
CODE·WORK·HYBRID 执行逻辑 / 工具调用规范 / 安全红线 / 完成标准）。

`CONDENSED_DUAL_RULES` 是注入动态工具循环 system prompt 的精简版：
保留硬约束（不伪造结果、最小化修改、验证义务、高风险确认、失败分类），
避免每轮工具决策都携带全文造成 token 浪费。
推理性能模式（inference_mode="performance"）下改为注入完整版全文。

打包提示：PyInstaller 需把本目录 .md 加入 --add-data（同 schema.sql 教训）。
"""

from __future__ import annotations

import logging
from pathlib import Path

logger = logging.getLogger(__name__)

_PROMPT_FILE = Path(__file__).parent / "prompts" / "code_work_system_prompt.md"
_MODE_CLASSIFY_FILE = Path(__file__).parent / "prompts" / "mode_route_classify.md"

_cache: str | None = None
_classify_cache: str | None = None

# 分类提示词内置兑底（资产文件缺失时降级，不阻塞路由）
_CLASSIFY_FALLBACK = (
    "判断下面的用户请求属于哪类任务，只输出一个词：coding（编程/改代码/修bug）、"
    "work（业务操作/查数据/报表/审批）、mixed（两者兼有）。\n请求：{prompt}\n类别："
)


def get_code_work_prompt() -> str:
    """读取完整版双模式系统提示词（带缓存）。文件缺失时返回空串（降级不阻塞）。"""
    global _cache
    if _cache is None:
        try:
            _cache = _PROMPT_FILE.read_text(encoding="utf-8")
        except OSError:
            _cache = ""
    return _cache


def get_mode_classify_prompt() -> str:
    """读取 ModeRouter LLM 分类提示词模板（带缓存，含 {prompt} 占位符）。"""
    global _classify_cache
    if _classify_cache is None:
        try:
            _classify_cache = _MODE_CLASSIFY_FILE.read_text(encoding="utf-8")
        except OSError:
            _classify_cache = _CLASSIFY_FALLBACK
    return _classify_cache


# 精简版执行纪律 —— 随 mode_router 写入 state.dual_rules_addon，
# 由动态工具循环在每轮编排决策时注入 system prompt。
CONDENSED_DUAL_RULES = """\
【Code/Work 双模式执行纪律】
优先级：安全 > 正确性 > 可验证性 > 可控性 > 效率。
1. 不伪造结果：未实际执行不得声称已执行；无法确认时明确说"当前无法确认执行结果"。
2. CODE 子任务：先读代码再改；最小化修改（不重构无关代码、不升级依赖、不改公共接口）；
   改后必须验证（测试/语法检查/validate_command）；修复循环最多 3 轮，超限停止并报告。
3. WORK 子任务：固定流程按步骤执行；正式外部动作前优先生成中间产物（草稿/预览）供确认；
   失败先分类（参数/权限/网络/数据/需人工），不要无脑重试。
4. 高风险操作（删除、覆盖、不可逆命令、发送消息、生产写操作、审批提交）必须走审批闸门；
   自动模式下按推荐项执行且全程留痕，硬阻断操作（DROP/TRUNCATE）任何模式都不得执行。
5. 混合任务严格按阶段：Code → 验证 → Work → 人工确认 → 汇总，不得混合执行。
6. 完成标准：CODE = 验证通过（或明确说明未验证）；WORK = 步骤成功且确认完成。
"""


def dual_rules_for(routing: str | None, *, performance: bool = False) -> str:
    """按路由结果返回注入片段（无路由时也给通用纪律）。

    performance=True（推理性能模式）：注入完整版 code_work_system_prompt.md
    （角色定义 / 执行逻辑 / 工具规范 / 安全红线全文）；完整版文件缺失或
    为空时降级回精简版，保证执行纪律不断档。正常模式始终用精简版省 token。
    """
    header = {
        "coding": "【当前任务路由：CODE（编程框架）】\n",
        "work": "【当前任务路由：WORK（业务框架）】\n",
        "mixed": "【当前任务路由：HYBRID（混合框架：Code→验证→Work→确认）】\n",
    }.get(routing or "", "")
    if performance:
        full = get_code_work_prompt().strip()
        if full:
            return header + full + "\n"
        logger.warning("性能模式请求完整提示词但资产为空，降级精简版")
    return header + CONDENSED_DUAL_RULES


# 最终回答风格 —— 摘自完整版 code_work_system_prompt.md，
# 注入 summarise 的 system prompt，保证终答与双模式风格要求一致。
FINAL_ANSWER_STYLE = """\
【最终回答风格（必须遵守）】
1. 用中文回答；先结论后过程。
2. 明确区分“已执行 / 建议执行”与“已验证 / 未验证”，不得把未验证说成已验证。
3. 代码类结果给出文件路径与关键变更；业务类结果给出执行状态与确认状态。
4. 失败时如实说明原因与下一步建议，不得隐瞒或淡化。
5. 不输出无依据的猜测；工具结果被截断时如实说明。
6. 需要用户确认/选择/补充信息时，禁止用正文直接反问。必须在回复末尾输出
   如下格式的选项块（前端会渲染成可点选卡片）：每个问题 3-5 个选项，
   每个选项必须写明选择理由，恰好一个选项标 recommended:true（推荐项），
   多个待确认问题就输出多个对象；选项措辞面向业务用户，通俗易懂。
   不需要确认时绝不输出该块；除该块外回复不得包含其它 JSON。
```clarify
[
  {
    "question": "<需要确认的问题>",
    "options": [
      {"text": "<选项简述>", "reason": "<为什么选它>", "recommended": true},
      {"text": "<选项简述>", "reason": "<为什么选它>", "recommended": false}
    ]
  }
]
```
"""
