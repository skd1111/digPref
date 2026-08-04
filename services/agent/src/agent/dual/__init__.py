"""Phase 18 双框架公共模块 —— Coding Agent vs Work Agent 的策略层。

主图拓扑不变；本包提供：
- router.py   模式路由器（关键词 → 模式先验 → LLM 兜底）
- policy.py   子任务级 ExecutionPolicy
- autonomy.py HITL 自动模式决策矩阵
- options.py  推荐选项生成
- repair.py   Auto-Repair 循环纯逻辑
"""
