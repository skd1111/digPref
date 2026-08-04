"""Phase 18 ModeRouter —— 双框架智能路由（V1）。

三级判定：
    1. 关键词表（零延迟、可解释）：coding/work 双侧命中 → mixed
    2. LLM 兜底：关键词未命中时由 LLM 分类（提示词见
       prompts/mode_route_classify.md；失败回退先验）
    3. 模式先验：LLM 不可用/失败时按 WorkMode 默认（full→coding，其他→work）

路由结果与模式默认不一致时生成偏离声明（responder 引用 + 审计留痕）。
红线：路由只决定"策略先验"，不改变 HITL 风险闸门行为。
"""
from __future__ import annotations

import logging
from typing import Any

from agent.graph.state import AgentState, record_trace

logger = logging.getLogger(__name__)

# ---- 关键词表（对齐 Code/Work 双模式系统提示词的路由规则；黄金集回归驱动扩充）----
CODING_KEYWORDS: list[str] = [
    "写代码", "修改代码", "改代码", "重构", "修复", "修一下", "修 bug", "修bug",
    "bug", "报错", "分析报错", "单元测试", "补测试", "编写测试", "运行测试",
    "写个函数", "实现一个", "脚本", "编译", "lint", "处理依赖", "创建项目",
    "修改配置", "提交代码", "diff", "解释代码", "添加功能",
]
WORK_KEYWORDS: list[str] = [
    "查询", "报表", "审批", "部署", "通知", "工单", "生产库", "对账",
    "数据库里跑", "跑一下报表", "导出报表", "月度", "日报", "周报",
    "整理", "汇总", "邮件", "日程", "总结",
]

_WORK_MODE_NAMES: dict[str, str] = {
    "full": "开发",
    "operator": "运营",
    "auditor": "审计",
    "analyst": "分析",
}
_FRAMEWORK_NAMES: dict[str, str] = {"coding": "编程流程", "work": "工作流程"}


def _classify_prompt() -> str:
    """LLM 分类提示词模板（资产文件优先，含 {prompt} 占位符）。"""
    from agent.dual.prompt_loader import get_mode_classify_prompt

    return get_mode_classify_prompt()


class ModeRouter:
    """三级路由器。llm 可为 None（测试/降级：只走关键词 + 先验）。"""

    def __init__(self, llm: Any | None = None):
        self._llm = llm

    # ---- 第 1 级：关键词 ----
    def keyword_route(self, prompt: str) -> str | None:
        text = prompt.lower()
        hit_coding = any(k.lower() in text for k in CODING_KEYWORDS)
        hit_work = any(k.lower() in text for k in WORK_KEYWORDS)
        if hit_coding and hit_work:
            return "mixed"
        if hit_coding:
            return "coding"
        if hit_work:
            return "work"
        return None

    # ---- 第 2 级：模式先验 ----
    def prior_route(self, prompt: str, work_mode: str) -> str:
        return "coding" if work_mode == "full" else "work"

    # ---- 第 3 级：LLM 兜底 ----
    async def llm_route(self, prompt: str, work_mode: str) -> str:
        if self._llm is None:
            return self.prior_route(prompt, work_mode)
        try:
            raw = await self._llm.route(
                task="query", prompt=_classify_prompt().format(prompt=prompt)
            )
            text = (raw or "").strip().lower()
            # hybrid 是提示词体系对 mixed 的等价表述
            for token, canonical in (
                ("mixed", "mixed"),
                ("hybrid", "mixed"),
                ("coding", "coding"),
                ("work", "work"),
            ):
                if token in text:
                    return canonical
        except Exception as exc:  # noqa: BLE001
            logger.warning("mode_router LLM classify failed, fallback to prior: %s", exc)
        return self.prior_route(prompt, work_mode)

    # ---- 组合入口 ----
    async def route(self, prompt: str, work_mode: str) -> tuple[str, bool]:
        """返回 (routing, overridden)；overridden = 路由结果偏离模式先验。"""
        prior = self.prior_route(prompt, work_mode)
        routing = self.keyword_route(prompt)
        if routing is None:
            # 关键词未命中 → LLM 语义分类（llm 为 None / 失败时自动回退先验）
            routing = await self.llm_route(prompt, work_mode)
        return routing, routing != prior


def _declaration(work_mode: str, routing: str) -> str:
    mode_name = _WORK_MODE_NAMES.get(work_mode, work_mode)
    fw_name = _FRAMEWORK_NAMES.get(routing, routing)
    return f"当前为{mode_name}模式，但本任务将按{fw_name}执行。"


async def mode_router_node(state: AgentState, llm: Any | None) -> dict:
    """LangGraph 节点：START 之后的第一个节点，写 routing 相关状态。"""
    prompt = state.get("user_prompt", "")
    work_mode = state.get("work_mode", "full")
    router = ModeRouter(llm=llm)
    routing, overridden = await router.route(prompt, work_mode)
    declaration = _declaration(work_mode, routing) if overridden else None

    # Phase 18 审计：路由决策留痕（best-effort）
    try:
        from agent.audit.store import audit

        await audit(
            "MODE_ROUTED",
            {
                "routing": routing,
                "work_mode": work_mode,
                "overridden": overridden,
                "declaration": declaration,
            },
            run_id=state.get("run_id"),
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("audit MODE_ROUTED failed: %s", exc)

    # Code/Work 双模式执行纪律：精简版注入工具循环 system prompt
    from agent.dual.prompt_loader import dual_rules_for

    return {
        "routing": routing,
        "routing_overridden": overridden,
        "routing_declaration": declaration,
        "dual_rules_addon": dual_rules_for(routing),
        "trace": [record_trace(
            "mode_router", "ok",
            routing=routing, work_mode=work_mode, overridden=overridden,
        )],
    }
