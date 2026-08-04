"""DSpark 推测解码契约（Pydantic 镜像）。

跨语言协议类型：TS ⇄ Rust ⇄ FastAPI ⇄ MCP servers。
本文件定义所有 DSpark 配置的字段 + 校验上下界，前后端共用。

校验上下界是 Phase 13 V1 llama.cpp 实际取值范围（设计文档 §3.1）。
前端 DSparkSettingsPanel 不得私自收紧，否则多客户端行为不一致（问题 4）。

文档：[docs/design/phase-13-dspark.md](../../../docs/design/phase-13-dspark.md)
"""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


# === 校验边界常量（前后端共用唯一真源） ======================================

# 上下文窗口大小
DSPARK_CONTEXT_SIZE_MIN = 512
DSPARK_CONTEXT_SIZE_MAX = 262144
DSPARK_CONTEXT_SIZE_DEFAULT = 4096

# GPU 层数（llama.cpp n_gpu_layers）
DSPARK_GPU_LAYERS_MIN = -1  # -1 = 自动全部
DSPARK_GPU_LAYERS_MAX = 999
DSPARK_GPU_LAYERS_DEFAULT = 0  # 纯 CPU

# 短输出阈值
DSPARK_SHORT_OUTPUT_MIN = 1
DSPARK_SHORT_OUTPUT_DEFAULT = 20

# 推测解码 K（每轮草稿 token 数）
DSPARK_N_DRAFT_MIN = 1
DSPARK_N_DRAFT_MAX = 16

# 草稿模型接受概率阈值
DSPARK_DRAFT_P_MIN_DEFAULT_AGGRESSIVE = 0.75
DSPARK_DRAFT_P_MIN_DEFAULT_STANDARD = 0.85
DSPARK_DRAFT_P_MIN_DEFAULT_CONSERVATIVE = 0.90
DSPARK_DRAFT_P_MIN_DEFAULT_OFF = 1.0


# === 字面量类型 ==============================================================

# 4 档预设（设计文档 §2.2 置信度阈值对照表）
SpeculativeMode = Literal["aggressive", "standard", "conservative", "off"]

# 决策原因常量（5 关铁律的输出 reason）
# TS 端 DSparkDecisionReason 必须与此处一一对应。
DSparkDecisionReason = Literal[
    "applied",
    "applied-default",
    "off-global",
    "off-local-only",
    "off-short",
    "off-no-draft",
    "off-no-runtime",
]


# === 模型 ====================================================================


class SpeculativePolicy(BaseModel):
    """单任务类别的 DSpark 策略。

    `mode` 决定 4 档预设模板（aggressive / standard / conservative / off）；
    也可直接覆盖 n_draft / draft_p_min（YAML `profiles.<name>.n_draft`）。
    """

    task_category: str = Field(min_length=1)
    mode: SpeculativeMode = "off"
    n_draft: int = Field(
        default=1,
        ge=DSPARK_N_DRAFT_MIN,
        le=DSPARK_N_DRAFT_MAX,
    )
    draft_p_min: float = Field(default=1.0, ge=0.0, le=1.0)

    @property
    def enabled(self) -> bool:
        """off 模式永远 disabled；其它模式要求 n_draft > 1。

        draft_p_min >= 1.0 也视为关闭（接受率为 1.0 = 无确认需求 = 没意义）。
        """
        if self.mode == "off":
            return False
        if self.n_draft <= 1:
            return False
        if self.draft_p_min >= 1.0:
            return False
        return True


class DSparkConfig(BaseModel):
    """DSpark 全局配置。

    V0 持久化到 `dspark.json`；V1 接 llama.cpp 时按本模型加载。
    `profiles` 字段是业务默认表（在业务层 `dspark/config.py` 添加），
    不属于跨语言协议层。
    """

    draft_model_path: str | None = Field(
        default=None,
        description="草稿模型 GGUF 路径；None 或空串 = 全局禁用 DSpark",
    )
    short_output_threshold: int = Field(
        default=DSPARK_SHORT_OUTPUT_DEFAULT,
        ge=DSPARK_SHORT_OUTPUT_MIN,
        description="max_tokens < 此值时跳过 DSpark（避免负优化）",
    )
    enable_global: bool = Field(
        default=True,
        description="总开关；False 时所有任务类别都关闭 DSpark",
    )
    context_size: int = Field(
        default=DSPARK_CONTEXT_SIZE_DEFAULT,
        ge=DSPARK_CONTEXT_SIZE_MIN,
        le=DSPARK_CONTEXT_SIZE_MAX,
        description="上下文窗口大小（tokens）",
    )
    gpu_layers: int = Field(
        default=DSPARK_GPU_LAYERS_DEFAULT,
        ge=DSPARK_GPU_LAYERS_MIN,
        le=DSPARK_GPU_LAYERS_MAX,
        description="GPU 层数：0=纯 CPU，-1=自动全部，N>0=指定层数",
    )


class DSparkDecisionRecord(BaseModel):
    """单次决策记录（写入 engine deque，UI 通过 GET /dspark/recent 读）。

    V0 内存存储，重启归零（UI 文案必须显示"本次会话"而非"累计"）。
    """

    ts: float = Field(description="决策时间戳（unix epoch seconds）")
    task_category: str
    speculative_enabled: bool
    n_draft: int
    draft_p_min: float
    backend: str
    reason: DSparkDecisionReason
    max_tokens: int