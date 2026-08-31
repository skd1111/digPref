"""agent.evolution —— Phase 19 V0 智能体自进化与自评测闭环。

设计文档：docs/design/phase-19-self-evolution.md

V0 范围（本次交付）：
    - 自评测信号归一：环境（轨迹结果）/ 用户 👍👎 反馈 → `evaluation_signals`
    - 任务签名：intent 细分类型 + active_skill + 工具指纹的归一化哈希
    - 经验学习闭环：失败轨迹 → `reflection` 任务提炼教训 → `experiences` 存储
      → 下一任务经 `extra_rules` 通道注入（见 agent.evolution.memory）

红线（设计文档 §8）：
    - `reflection` 任务接触用户原始内容 → `_LOCAL_ONLY_TASKS` 本地优先
    - 轨迹只存摘要 + 工具指纹，不存参数明文 / 凭证 / DSN
    - 全链路 best-effort：任何进化失败都不阻塞主对话链路
"""
