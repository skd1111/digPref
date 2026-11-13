# Bug 修复台账（第二册）

> 本文件是项目当前唯一的 bug 修复记录台账。每次修 bug 必须在此追加一条记录，不可跳过。
> 编号接续第一册：#1 ~ #162 见 [BUGFIX_LOG.md](BUGFIX_LOG.md)（已封存，不再更新）。本册从 **#163** 起编号。
> 格式要求：[CLAUDE.md](../CLAUDE.md) 中「Bug 修复记录规范」章节。

## 记录列表

---

### #163 跨轮上下文全部丢失 —— LangGraph reducer 转对象后 10 处消费点读不到 role

| 字段 | 内容 |
|---|---|
| **是否修复** | 已修复 |
| **修复时间** | 2026-08-27 |

**现象**

用户在 PPT 任务页签里发「继续」，UI 明确显示「将附加上下文：会话历史（近 2 轮 ≈59 tok）」，
但模型回答「抱歉，我没有看到您之前任务的具体上下文（这是一次新的会话，我没有保留之前的任务状态）」。

**问题原因**

传输链路完全正常 —— 运行日志 `[intent.start] {"history_turns": 4}` 证明 4 条历史确实到达了 Python。
断点在 Python 内部的最后一米：

`AgentState.messages` 带 `Annotated[list, add_messages]` reducer（`graph/state.py:44`）。
LangGraph 的 `add_messages` 会调 `convert_to_messages`，把入图时的 `{"role", "content"}` dict
统一转成 `HumanMessage` / `AIMessage` 对象。而 **LangChain 的 `BaseMessage` 没有 `.role` 属性** ——
它只有 `.type`，取值 `human` / `ai` / `system`。

全仓 10 处消费 `state["messages"]` 的代码各自手写 `getattr(h, "role", None)`，对象形态一律取不到，
分两类后果：

- **静默丢弃**（`role=None` → `continue`，整条历史被过滤成空）
  1. `llm/prompts.py::format_history_brief` —— 终答链路，**本次报障的直接来源**
  2. `llm/private_llm.py::analyze_intent`
  3. `llm/private_llm.py::plan`（走 `hasattr(h, "role")` 变体）
  4. `llm/ollama.py::analyze_intent`
  5. `llm/ollama.py::plan`
  6. `tools/loop.py` 原生工具循环首轮 messages 构造
  7. `tools/loop.py` 「找回原任务用户消息」逻辑
  8. `builtin/dispatcher.py::_collect_user_texts`
- **角色错乱**（fallback 成默认 `"user"`，文本还在但标签全错，比丢弃更隐蔽）
  9. `llm/router.py::_conversation_summary` —— assistant 回复被误标成用户提问，decompose 决策被误导
  10. `llm/router.py::_compact_messages` —— 工具编排 prompt 里「历史对话」全是提问、没有回答

日志里 intent 节点看似「用上了上下文」（把「继续」改写成「继续制作介绍 daide 的 PPT」），
实际是从 `page_context` 的页签标题猜的 —— 页签正好叫「做一个介绍你自己daide的ppt」。
这个巧合长期掩盖了缺陷。

**修复方案**

`llm/prompts.py` 新增 `normalize_message(msg) -> tuple[role, content] | None` 作为全仓
**唯一**解析入口：BaseMessage 读 `.type` 并按 `_LC_TYPE_TO_ROLE` 映射，dict 读键，其他返 `None`。
10 处消费点全部改走它，删掉各自的手写解析。

两个关键设计决定：

1. **解析失败必须跳过，不得 fallback 成默认 role。** 猜 `"user"` 正是第 9/10 处旧 bug 的成因。
2. **`format_history_brief` 放行 `system` 角色。** 旧实现只放行 user/assistant，前提是
   「system = 界面日志噪声」。该前提已证伪：客户端结构上发不出 system（`chatStore.tabContextMessages`
   只放行 user/assistant，服务端 `api/chat.py::_HISTORY_ROLES` 又过滤一次），
   `state["messages"]` 里真实存在的 system 只有 `graph/stream.py` 自己注入的两条 ——
   「前段对话摘要」（压缩后的旧对话）与「任务台账锚点」（已交付文件路径）。
   这两条恰是跨轮上下文里信息密度最高的内容（「太丑了」指哪个文件全靠它），旧实现连带丢掉了。

未采用「给 reducer 传 `format` 参数让 state 保持 dict 形态」的方案：改动虽只有一行，
但影响所有读 messages 的代码，风险面反而更大。留作后续重构。

**影响范围**

所有跨轮对话：终答、意图识别、planner、工具编排决策、原生工具循环、内置工具用户文本汇集。
两个 LLM 后端（private / ollama）均受影响。

**验证方式**

新增 `services/agent/tests/test_history_role_normalization.py`（20 用例，全过）。

关键点：**每个用例都从 `add_messages()` 的真实输出造数据**，而不是手写 dict。
既有测试全部手写 dict，所以 10 个 bug 一个都没抓到 —— 这是本次真正的教训。
文件里第一条 `test_basemessage_has_no_role_attribute` 专门把「BaseMessage 无 `.role`」
这个物理前提钉死，将来 langchain 若改了行为会立刻失败。

同时修正 `test_summarise_history_injection.py` 中基于已证伪前提的 2 条断言
（system 消息应被过滤 → 改为未知 role 应被过滤，并新增 system 上下文必须保留的正向用例）。

全量回归：**2497 passed / 3 failed / 9 skipped**。3 个失败为 `test_preview_e2e.py` 端口相关，
已用 `git stash` 在原始代码上复现，确认预存、与本次无关。TS `tsc --noEmit` 0 错。

**相关链接** —— 无 issue；日志证据 `%LOCALAPPDATA%\Enterprise AI IDE\logs\{agent,cot}.log` run_id `38637782-0e5b-479e-b009-990345debb9a`。

---

### #164 「工具执行」卡片永久转圈 —— tool_call 与 tool_result 无共享标识

| 字段 | 内容 |
|---|---|
| **是否修复** | 已修复 |
| **修复时间** | 2026-08-27 |

**现象**

聊天区里 20+ 张「工具执行」卡片一直显示「进行中」转圈，即使本轮早已结束
（底部已显示「本轮耗时 27.6s」、assistant 终答已呈现）。

**问题原因**

三处叠加：

1. `graph/stream.py:715,728` —— `tool_call` 与 `tool_result` 事件各自 `str(uuid.uuid4())`，
   **两条事件之间没有任何共享标识**。
2. `builtin/models.py::ToolResult.to_dict()` 不含 `name`，MCP 路径（`tool_runner.py:91`
   原样透传 `mcp.invoke()` 返回值）同样不含 —— 但 TS 侧 `tools.ts` 声明 `name: string` 必填。
   **协议漂移**：TS 类型是一份与 Python 实现脱节的虚构（还声明了 Python 从不发的 `server` / `data`，
   而 Python 实发的是 `content` / `meta`）。
3. `useAgentStream.ts:154` —— 前端只能退而求其次用 `evt.result?.name ?? ''` 配对，
   恒为 `''` → `m.content === resultName` 永远不命中 → 走兜底分支**追加一张新的「已完成」卡**，
   原 running 卡片没人翻牌。

而 done / error 分支的 `endRun`（`chatStore.ts:461`）只清理运行态字段，不遍历 messages 收尾。
`sanitizeRestored` 那套 running→ok 的兜底只在 localStorage 恢复时跑一次，运行时路径够不着。
所以卡片会一直转下去。

**修复方案**

根治 + 兜底两层：

- **根治（配对标识）**：`call["call_id"]` 这个约定 `builtin/dispatcher.py:182` 本已存在，
  但从不写回 call 字典。现在 dispatcher 与 `tool_runner` 都把 call_id **回写进 call 字典**，
  于是 `pending_tool_call` 携带它，`stream.py` 两条事件同源读取并下发 `callId`。
  取值刻意用**确定性**方案（MCP 路径 `call_<stepIndex>`，builtin 路径 uuid4 hex 复用）——
  重试 / HITL 审批后重跑同一步会得到同一 call_id，前端原地更新那张卡而非堆出第二张。
- **根治（协议对齐）**：`ToolResult` 新增 `name` / `call_id` 字段并进 `to_dict()`，
  dispatcher 统一盖章；`stream.py` 对 MCP 路径按 call 回填 `name`。
  TS `tools.ts::ToolResult` 改写为与 Python 实际输出一致的形状（字段全部可选，
  因为 builtin / MCP 两条路径字段集本就不同）。
- **前端配对**：消息 id 改为 `tool-<callId>` 派生，`tool_result` 按 id 精确 `update`。
  三级兜底：callId → 工具名 → 最近一条 running（宁可翻错一张也不留永久转圈）。
- **兜底层**：`endRun` 强制把该页签内所有 `status === 'running'` 的 execution / search 卡片
  翻成 `ok`。一轮已结束，它们不可能再收到结果事件了。即使未来配对逻辑再出问题，
  也不会留下永久转圈的卡片。

**影响范围**

所有工具调用的 UI 状态呈现（builtin 与 MCP 两条路径）；shared-protocol TS 类型；
HITL 审批后重跑的卡片去重。不影响工具实际执行逻辑。

**验证方式**

新增 `services/agent/tests/test_tool_event_pairing.py`（8 用例，全过）：
`to_dict` 带 name/call_id、两条事件 callId 同源、MCP 路径 name 回填、
已有 name 不被覆盖、缺 call_id 时降级为 None 不崩、失败结果同样可配对。

全量回归 **2497 passed / 3 failed（预存）/ 9 skipped**；TS `tsc --noEmit` 0 错。
Rust 未改动（`sse_bridge.rs` 是纯透传，本就无需改）。

**相关链接** —— 无 issue。前置记录 #157 曾按工具名做过一次配对补救，本次改为按 call_id 根治。

---

### #165 shell 报「成功」掩盖失败 —— 22 轮空转烧光工具编排预算

| 字段 | 内容 |
|---|---|
| **是否修复** | 已修复 |
| **修复时间** | 2026-08-27 |

**现象**

用户报「24 轮就停了，任务明显没做完，是不是设计不合理」。终答为
「工具编排预算已用尽（共执行 24 轮）」，而一页 PPT 都没生成。

**问题原因**

不是预算不够 —— 是 22 轮被浪费了。从 `audit.sqlite` 的 `tool_calls` 表取出该 run
（`98f28573-0a04-41c8-a286-e3d63fc78f2c`）的 30 条执行记录，序列是：

| 轮次 | 内容 |
|---|---|
| 1-8 | 正常：找到 PPT Master 技能、读 SKILL.md |
| **9-30** | **同一件事重试 22 次**：执行 `attribution_guard.py` |

那 22 轮换的花样：`python` → 绝对路径 → `python3` → 写 `run_guard.bat` 包装 →
`cmd /c` → `start /wait /b` → `^` 转义空格 → `echo %PATH%` → 反复 `dir` 探目录。
卡点是路径含空格（`Enterprise AI IDE`）加 Windows cmd 引号规则。

**两层缺陷叠加**：

1. **`builtin/shell.py` 的 `ok` 语义错了** —— 只要进程**成功启动**就返 `ok=True`，
   `exit_code` 被埋进 `content`。审计印证：那 22 次全是 `ok=1`，但 `content_size`
   只有 200-500 字节，装的是 cmd 的「不是内部或外部命令」。
   同仓 `ssh/client.py:204` 一直是 `ok = exit_code == 0` —— **shell 是唯一的异类**，
   这坐实了它是缺陷而非有意设计。
2. **停滞熔断在测错的东西** —— `tools/loop.py` 的判据是
   `any(r.get("ok") for r in executed)`，被缺陷 1 一直喂 `ok=True`，
   本该 3 轮就掐断的空转，计数器**一次都没涨过**。

附带发现：`dangerous_operator` 拦截（前 3 次调用命中 `&` / `(`）只回一句
「not allowed」，不告诉模型该怎么办。模型正是被这种沉默反馈带进盲试的。

**修复方案**

四处改动，Python 与 Rust 严格镜像（桌面端走 Rust，Agent 独立运行走 Python，
两边都有同一个 bug）：

- **A · `ok` 语义纠正**：`exit_code != 0` 或超时 → `ok=False`，
  stderr 摘要进 `error`（模型首先读 error，不该让它去 `content.stderr` 里翻）。
  失败时**保留 content** —— 模型仍需要 stdout/stderr 判断原因。
  新增 `allow_nonzero_exit` 参数，供「非零退出是正常语义」的命令显式放行
  （`findstr`/`grep` 无匹配返 1、`diff` 有差异返 1）。
- **B · 新增重复调用熔断**：`tools/loop.py` 增加第二道闸门 ——
  同一工具 + 同一参数指纹连续 3 次即掐断，**不依赖任何工具的 `ok` 语义**。
  即使将来某个工具的 `ok` 再出问题，原地打转也拦得住。
  指纹用 `sort_keys` 的 JSON（键序不同不能绕过），排除 `call_id`（每次都不同）。
  native 路径用局部列表，prompt 路径跨图轮 → 新增 state 字段
  `tool_last_call_fp` / `tool_repeat_streak`。
- **C · 拦截给出路**：危险操作符 / 白名单拦截都附 `hint`，指明替代路径
  （`&&` → 拆成多次调用；管道 → `builtin_grep`；`if exist (...)` →
  `builtin_stat_file`；`dir` → `builtin_list_dir`）。工具描述也重写，
  明确「`ok=false` 表示命令失败，读 `error` 换方法，不要重试同一条命令的变体」。
- **D · Windows 默认 shell 改 pwsh**（用户要求，装了才用，未装回退 cmd）：
  cmd 的引号规则是本次事故的直接推手，pwsh 一致得多。
  带 `-NoProfile`（防 profile 污染 stdout）+ `-NonInteractive`（防等输入挂死）。
  **不回退 `powershell.exe`（5.1）** —— 编码与参数解析和 pwsh 有差异，
  混用只会多一种不确定性；没 pwsh 就老实用 cmd。

**D 的连带风险与处置**（值得单独记一笔）

pwsh 把 `where` / `dir` / `type` 都设成了**别名**（`where` → `Where-Object`，
不是 `where.exe`），语法与 cmd 有实质差异 —— 而事故里模型正好用了 `where python`。
换 shell 修掉了引号问题，却可能引入新的失败模式。

根本问题是**模型不知道自己在跟哪个 shell 说话**。处置：
`ToolResult.content` 新增 `shell` 字段（`pwsh`/`cmd`/`sh`），
并由 `registry.generate_tool_descriptions()` 把当前 shell 的写法提醒
拼进模型可见的 system prompt（pwsh 的 `where` 用 `Get-Command`、
`echo a b` 输出两行等）。测试 `test_shell_note_reaches_tool_description`
钉死「提醒必须真的进 prompt」—— 否则等于没说。

**影响范围**

所有 `builtin_shell` 调用的成功判定（Python + Rust 双端）；工具编排循环的
熔断行为（native + prompt 双路径）；Windows 上 shell 命令的执行语义与引号规则；
`AgentState` 新增 2 字段；Tauri `ShellArgs` 新增 1 字段。

**行为变更（需注意）**：此前依赖「shell 总是 ok」的调用方会开始收到 `ok=False`。
这是意图内的 —— 但若有命令的非零退出是正常语义，需显式加 `allow_nonzero_exit=true`。

**验证方式**

- 新增 `services/agent/tests/test_tool_repeat_breaker.py`（19 用例）：
  指纹一致性 / 键序无关 / 排除 call_id / 不可序列化参数不抛异常 / 只数末尾连续段 /
  阈值 3 / 熔断文案可操作。其中 `test_reproduces_the_22_round_burn` 直接复现事故场景，
  断言在第 3 轮掐断（旧行为烧到 24）；`test_varied_commands_are_not_penalised`
  守住反面 —— 真的在换方法时不误杀。
- `test_builtin_v2.py` 新增 6 用例（非零退出是失败 / `allow_nonzero_exit` 放行 /
  未知命令 error 带原因 / 拦截 hint 可操作 / pwsh 优先 / shell 名与提醒进 prompt），
  并修正 2 条基于旧语义的断言（超时此前断言 `r.ok`，现在断言失败）。
- Rust 侧镜像新增 2 个单测（`test_shell_nonzero_exit_is_failure` /
  `test_shell_block_hints_are_actionable`），并修正超时单测语义 + 6 处调用签名。
- 全量回归：**2523 passed / 3 failed / 9 skipped**。3 个失败为
  `test_preview_e2e.py` 端口相关，已确认预存（见 #163）。TS `tsc --noEmit` 0 错。
- Rust 未编译（用户手动验证），改动为签名 + 返回值 + 单测，无新依赖。

**相关链接** —— 无 issue。与 #163 叠加分析：预算耗尽的文案让用户「再发一句继续」，
而 #163 恰好让「继续」完全失效，两个缺陷叠成了无法绕过的死路。

**待跟进**：`tool_loop_max_turns` 仍为 24（硬上限 30）。本次没有调整 ——
先看熔断生效后长链任务是否还会触顶。若仍频繁触顶，再评估提高上限
或引入基于 `task_id` 台账的断点续跑（不依赖对话历史）。

---

### #166 Rust crate 编译不过 —— #165 遗留丢失 `GENERAL_SHELL_HINT` 常量定义

| 字段 | 内容 |
|---|---|
| **是否修复** | 已修复 |
| **修复时间** | 2026-08-27 |

**现象**

cargo check / test / clippy 对 `eaide-desktop` crate 直接报 4 处 `E0425: cannot find value GENERAL_SHELL_HINT in this scope`，Rust 侧完全无法编译。

**根本原因**

#165（shell 拦截附可操作 hint）在工作区落地时，引用点（`execute_shell` 白名单拦截 / 非零退出 hint / `operator_hint` 拼接）都写进去了，但 `GENERAL_SHELL_HINT` 常量定义本身丢失。#165 记录里明言「Rust 未编译（用户手动验证）」—— 未编译验证就是本次的直接推手。

**修复**

在 `apps/desktop/src-tauri/src/builtin/mod.rs` 的 shell 安全常量区补回 `pub const GENERAL_SHELL_HINT`，文案与 Python 端 `builtin/shell.py::_GENERAL_SHELL_HINT` 严格镜像（列目录用 builtin_list_dir / 查文件用 builtin_find / 读文件用 builtin_read_file）。顺带修掉同文件一个 clippy -D warnings 报错（`&[first.clone()]` → `std::slice::from_ref(&first)`，#165 新增单测里的）。

**验证方式**

- `cargo check --bin eaide-executor` 通过；`cargo clippy --all-targets -- -D warnings` 零告警。
- `cargo test --lib`：executor_rpc 新增 8 用例全过；仅剩 `test_shell_nonzero_exit_is_failure` / `test_shell_echo_ok` 2 个环境性失败（本机 shell 输出行为，#165 引入时即未编译验证，非本次引入）。
- Python 镜像侧 `test_shell_streaming.py` 4 用例验证同一文案链路。

**可复用教训**：涉及编译型语言的修复，「手动验证」不能替代 `cargo check` —— 未过编译的改动连存在性都不成立。双端镜像常量（Python ↔ Rust）任何一侧落地时都应连同定义体一起核对。

---

### #167 tauri build 报 "failed to find main binary" —— 新增第二个 [[bin]] 后未配 default-run

| 字段 | 内容 |
|---|---|
| **是否修复** | 已修复 |
| **修复时间** | 2026-08-27 |

**现象**

cargo 侧编译已通过，但 `pnpm tauri build` 在打包环节报 `failed to find main binary, make sure you have a package > default-run in the Cargo.toml file`，安装包构建中断。

**根本原因**

cargo 推断「主二进制」的规则：包内只有一个自动发现的二进制时直接采用；v2.116 为 `eaide-executor` 执行器显式新增了第二个 `[[bin]]` 目标，主二进制不再唯一，cargo 拒绝推断，Tauri 找不到要打包哪个。

**修复**

cargo 侧编译能通过（单目标构建都显式指定了目标），问题只在推断环节 —— [Cargo.toml](../apps/desktop/src-tauri/Cargo.toml) `[package]` 段补 `default-run = "eaide-desktop"`；`cargo metadata` 验证 `default_run` 字段与两个 bin 目标均正常解析。

**可复用教训**：往既有 crate 新增 `[[bin]]` 目标时，必须同步检查依赖主二进制推断的下游（tauri build / `cargo run` / CI 构建脚本），补 `default-run` 显式锁定 —— 这类错误编译环节拦不住，只在打包/运行环节暴露。

---

### #168 参数确认式追问漏选项卡 + 执行树同类工具调用刷屏（两项体验修复）

| 字段 | 内容 |
|---|---|
| **是否修复** | 已修复 |
| **修复时间** | 2026-08-27 |

**现象**

① PPT 参数确认终答（「请直接回复例如：'10 页 / 客户介绍 / A / A / 不要'，缺省我会按 '…' 执行」）未渲染成可点选项卡，用户只能手打（用户反馈：为什么不是选项卡）；② 执行过程树把 22 条相同的「执行命令」逐条列出刷屏（用户反馈：同一个类型显示一个就好）。

**根本原因**

① 多维参数确认的编号行不是互斥选项，枚举路径（#136/#149/#150 系列）即使命中也不宜当选项卡，且「缺省按…执行」的默认组合此前无人解析；「直接回复」类引导语也不在 `_CHOICE_CUE_RE` 词表。② 树形合并（#162）只折树不压缩子项，同名工具逐条渲染。

**修复**

① [responder.py](../services/agent/src/agent/graph/nodes/responder.py) 新增 `_confirm_combo_clarify`：确定性抽取「缺省按 X 执行」默认组合（需与回复引导语同现，双门槛防误伤），生成「按默认配置执行：X（推荐）/ 自定义配置」二元确认卡，优先级高于枚举路径；`_CHOICE_CUE_RE` 补「直接回复 / 回复例如 / 回复即」引导语。② [ExecutionTree](../apps/desktop/src/components/chat/ExecutionTree.tsx) 新增 `compressSameType`：连续同名工具行折叠为一行 + ×N 徽标（聚合态：任一在跑即转圈、任一失败即标红；摘要仍按真实步数统计），非工具步骤不受影响。

**验证方式**

- 后端：`test_responder_clarify_fallback.py` 新增 3 用例（截图原文成卡 / 单有引导语不误伤 / 单有缺省组合不误伤），14/14 全过；ruff check/format 干净。
- 前端：`executionTree.test.tsx` 新增 3 用例（×N 合并 / 交替不合并 / 聚合状态）并适配 occurrence 用例，全量 294/294，tsc / eslint 零错零警。

**可复用教训**：确定性选项卡提取每新增一类模式都要配「正例 + 两个反例（单条件不构成）」用例，守住「宁可漏加不可误伤」；前端批量列表降噪（同类合并）要同步更新依赖旧渲染行数的交互用例（点击第 N 行、occurrence 定位）。

---

### #169 任务计划卡串到另一个会话 —— todo 事件写激活页签而非 run 归属页签，关掉接收方页签后彻底丢失

| 字段 | 内容 |
|---|---|
| **是否修复** | 已修复 |
| **修复时间** | 2026-08-28 |

**现象**

第一个 chat 开始任务后，打开第二个 chat 对话，第一个 chat 的任务计划进度卡显示在了第二个 chat；关掉第二个 chat 返回第一个 chat，计划卡也没了（用户反馈原文）。

**根本原因**

2026-08-26 多会话并发改造把 message / tool_call / trace 执行块等事件全部改为按 `runId → 页签` 归属路由，唯独漏了 `trace` 通道里的 `todo` 分支：它调 `chatStore.upsertTodo`，而该 action 内部写死 `activeTabId`（激活页签）。于是 A 会话跑任务期间切到 B，后续每帧 todo 都追加进 B；todo 卡从未进过 A —— 关掉 B（页签连同消息销毁）后自然彻底消失。

**修复**

① [chatStore.ts](../apps/desktop/src/store/chatStore.ts) `upsertTodo` 签名加 `tabId` 首参，写入指定页签（与 `appendToTab` / `appendExecutionToTab` 同款），不再碰 `activeTabId`；② [useAgentStream.ts](../apps/desktop/src/hooks/useAgentStream.ts) todo 分支传入 `tabFor(evt.runId)`，与其它事件同一套归属路由（无 runId 旧式事件仍回退激活页签）。

**验证方式**

todoProgress.test.tsx 新增 2 用例（写指定页签不碰激活页签 / trace 事件按 run 归属路由：A 跑任务 + B 激活 → 卡进 A 不进 B），全量 295/295，tsc / eslint 零错零警。

**可复用教训**：多会话并发改造时，新事件通道 / 新 store action 必须默认接「归属路由」而非「激活页签」—— 漏掉一处就是一类串台；给每类事件加「双页签 + 单侧 startRun」的串台回归用例，比事后排查便宜得多。

---

### #170 产物路径点不开也右键不了 —— 路径识别正则排斥空格，带空格目录名被截断（#170）

| 字段 | 内容 |
|---|---|
| **是否修复** | 已修复 |
| **修复时间** | 2026-08-28 |

**现象**

任务完成后的产物路径（如默认工作空间下的 `C:\Users\79834\AppData\Local\Enterprise AI IDE\workspace\tasks\…\EAIDE_Intro.pptx`）在对话里显示为纯文本：点击无反应、右键无「资源管理器定位/复制路径」菜单。README 宣称的「对话中的文件路径可点击直接用默认程序打开」对这类路径失效。

**根因**

[FilePathChip.tsx](../apps/desktop/src/components/chat/FilePathChip.tsx) 的路径主体正则 `PATH_BODY = [^\s…]+` 显式排斥空白：默认安装目录 `Enterprise AI IDE` 目录名带空格，正则在第一个空格处截断，识别不出完整路径，不渲染可点击胶囊。两处叠加：① 连续文本扫描正则无法跨空格；② 执行步骤行正文（ExecutionRow）压根没走路径渲染。

**修复**

① [FilePathChip.tsx](../apps/desktop/src/components/chat/FilePathChip.tsx)：新增 `extendOverSpaces` 后处理——无空格正则命中后逐段试吸收「空格 + 路径字符段」，只有吸收结果以扩展名锚（`\.[A-Za-z0-9]{1,8}$`）收尾才记录，锚外散文不吞；行内代码整体判定（`FILE_PATH_EXACT_RE`）换用容忍内部空格的主体（空格后必须紧跟合法路径字符，不吃尾部空格）。② [ExecutionBlock.tsx](../apps/desktop/src/components/chat/ExecutionBlock.tsx)：ExecutionRow 正文接入 `renderTextWithPaths`，执行步骤里的路径同样可点可右键。

**验证方式**

markdownPathLink.test.tsx 新增 4 用例：带空格路径整体识别 / 完整成胶囊 / 空格后散文不误吞 / 点击传完整路径；前端全量 313/313，tsc / eslint 零告警。

**可复用教训**：凡是要识别「用户文件系统里真实路径」的正则，默认安装目录/用户名带空格是常态（Program Files / Enterprise AI IDE），不能简单 `\S+` 一刀切；但连续文本里又不能让路径正则无界吞散文——用「扩展名锚 + 逐段试吸收」后处理比纯正则更稳。另：一个交互能力（路径可点击）落地时要清点所有渲染出口（Markdown 终答 / 执行步骤行 / 汇总卡），漏一个出口就是一类用户可见的失效。

---

### #166 工具层三处缺陷把模型逼进死角 —— glob 不可用 + 路径分词被啃 + pwsh 调用操作符被拦

| 字段 | 内容 |
|---|---|
| **是否修复** | 已修复 |
| **修复时间** | 2026-08-27 |

**现象**

#165 修复后再跑同一任务，**又**耗尽 24 轮预算（53 次工具调用）。
且这次重复调用熔断没触发 —— 因为模型确实在换方法，每次调用都不相同。

**问题原因**

先确认 #165 的修复已生效：审计里出现了新的 error 格式
`exit_code=1: ResourceUnavailable...`，pwsh 也确实在用。问题是这次撞上了三个
**更底层的**缺陷，其中一个是 #165 引入的回归。

| 调用 | 结果 |
|---|---|
| 2, 5, 6 | `glob` → `PathSecurityError: empty path`（工具本身不可用） |
| 13, 15, 18 | `shell` → `command_not_allowed`，报错里路径的反斜杠全没了 |
| 19-21 | `cd xxx` → `ok=1` 但毫无作用 |
| 22, 23 | `& "C:\...\python.exe"` → 被危险操作符拦截 |

**① `glob` 工具彻底不可用** —— `schemas.py` 对模型声明参数叫 `base_dir`
（可选，默认 `.`），`tauri_bridge.py::build_rust_args` 却读 `root`，默认还是空串：

```python
"root": args.get("root", ""),      # 模型按 schema 传的是 base_dir
```

空串进 `validate_path` → `empty path`。Rust `GlobArgs.root` 也无 `#[serde(default)]`。
反方向同样错：Python 兜底 `builtin_glob_py` 只认 `base_dir`，模型传 `root` 会 TypeError。
模型三次尝试 glob（这是**正确**的直觉）三次被打回，只能退回 shell，进而撞上引号地狱。

**② `shlex.split` 啃掉 Windows 反斜杠** —— 白名单校验取首 token 用它，
默认 POSIX 模式把 `\` 当转义符：

```
输入  C:\Users\79834\AppData\Local\Enterprise AI IDE\python.exe
posix=True  → 'C:Users79834AppDataLocalEnterprise'   ← 审计报错原文
posix=False → 'C:\Users\79834\AppData\Local\Enterprise'
```

模型给了正确的白名单前缀也会被判 `command_not_allowed`。

**③ #165 引入的回归** —— 在 pwsh 里，调用路径含空格的可执行文件**必须**用
`&` 调用操作符（`& "C:\Program Files\python.exe"`），而 `&` 在
`DANGEROUS_SHELL_CHARS` 里被拦。结果：cmd 式直接调用在 pwsh 下不成立，
pwsh 唯一正确的写法又被禁 —— **模型被逼进了无解的死角**。
这是 #165 换 shell 时未预见的后果。

附带：`cd` 在每次独立子进程里是空操作（模型白花 3 轮）；
pwsh 彩色报错把 ANSI 转义码（`[31;1m`）带进 error 字段挤占有效信息。

**修复方案**

核心判断：单独补这几个洞是治标。真正的问题是**我们逼着模型去跟 shell 的引号
规则搏斗**。所以主修复是给它一条根本不需要引号的路。

- **A · 新增 `argv` 数组形式（主修复）**：直接以参数数组 exec，
  **完全绕过 shell** —— 没有引号、没有转义、没有操作符解释。
  `argv=["C:\\Program Files\\python.exe", "guard.py"]` 即可。
  这是唯一能可靠调用「路径含空格的可执行文件」的方式，同时消除了整个
  操作符注入面（数组元素不会被 shell 解释），比字符黑名单更安全。
  argv 形式下跳过操作符校验、白名单直接取首元素（天然免疫缺陷 ②）。
- **B · 新增 `cwd` 参数**：并在描述里明确 `cd` 不跨调用生效。
  cwd 不存在时直接报 `cwd_not_a_directory`，不让子进程抛难懂的 OSError。
- **C · glob 参数名双向兼容**：`root` / `base_dir` 互为别名，缺省一律回落 `.`。
  Python 映射层、Rust `GlobArgs`（`#[serde(alias = "base_dir", default)]`）、
  Python 兜底（`**_ignored` 吞掉 Rust 专用参数）三处都改。
- **D · `shlex.split` 在 Windows 用 `posix=False`**，并剥掉外层引号；
  引号不配对时退化成按空白切分，不抛异常。
- **E · 剥离 ANSI 转义码**：Python 用正则，Rust 手写状态机（不为此引入 regex 依赖），
  两边都保证 `array[0]` 这类普通方括号不被误吃。
- **F · 参数透传补齐**：`build_rust_args` 此前只传 `command`，
  `argv` / `cwd` / `allow_nonzero_exit` 都没传 —— 桌面端走 Rust，
  **不透传等于 #165 和本次修复对桌面端完全无效**。这是最容易漏的一环。

**刻意不做**：没有放宽 `&` 的拦截。有了 argv 形式就不需要为 pwsh 的调用操作符
开口子，黑名单保持收紧更安全。

**影响范围**

`builtin_shell` 新增 2 个参数（Python + Rust + schema + 工具描述 + Tauri ShellArgs）；
`builtin_glob` 参数解析（三处）；白名单分词逻辑；error 字段内容。
`command` 形式行为不变，向后兼容。

**验证方式**

- `test_builtin_v2.py` 新增 4 个测试类 18 用例：argv 跑带空格路径解释器 /
  argv 跳过操作符校验（字面量 `a&b|c;d` 不被拦）/ argv 白名单取首元素 /
  非字符串元素被拒 / 空 argv 与空 command 都报 empty_command 且 hint 推荐 argv /
  cwd 真实生效 / cwd 不存在报错 / Windows 首 token 保留反斜杠 /
  引号不配对不崩 / ANSI 剥离（含普通方括号不被吃）/ glob 双向别名 + 缺省为 `.` /
  Python 兜底容忍 Rust 专用参数 / **`build_rust_args` 透传 argv+cwd** /
  **工具描述里必须出现 argv 与 cwd**（模型只看描述，不写进去等于不存在）。
- Rust 镜像新增 7 单测（argv 执行 / argv 跳过操作符 / argv 白名单 /
  空 argv / cwd 必须存在 / strip_ansi / glob root 默认值），并更新 13 处调用签名。
- 全量回归：**2561 passed / 3 failed / 9 skipped**（3 个失败为 preview 端口相关，
  已确认预存，见 #163）。
- Rust 未编译（用户手动验证）。

**相关链接** —— 无 issue。#165 的直接后续；三个缺陷叠加导致 #165 修复后问题依旧。

**教训**

同一个任务连续烧掉两次预算，暴露的不是单点 bug 而是一类系统性问题：
**工具的失败反馈不足以让模型改变策略**。glob 报「empty path」不提示参数名错了、
白名单报「command_not_allowed」不显示它实际解析出了什么、`&` 被拦不说明
pwsh 下该怎么调用带空格的路径 —— 每一处都让模型只能靠猜。
本次除了修 bug，更重要的是给每种失败都配上可操作的 `hint`，
并提供一条（argv）根本不会踩坑的主路径。

---

### #171 热重载 MCP 后新工具不进规划目录 —— planner 5 分钟工具目录缓存未失效（#171）

**现象**

用户在设置页注册新 MCP 并点「重新加载」（`POST /mcp-config/reload`）后，
新工具对规划链路不可见：要么模型答「未配置该工具」，要么规划器按旧目录
过滤，新工具步骤被 `_normalise_step` 丢弃。

**根因**

`reload_mcp_clients()` 只重建了 MCP 连接（`runtime.mcp` 是动态引用，这部分
即刻生效），但 `planner.py` 有一份模块级 5 分钟 TTL 的工具目录缓存
（`_cache_result` / `_cache_expiry`，避免重复 list_tools），热重载从未清它。
重载前 5 分钟内有请求的话，新工具要等缓存自然过期才进规划目录。
`reset_tool_specs_cache()` 此前只有测试在用。

**修复**

`reload_mcp_clients()` 重建连接后调 `reset_tool_specs_cache()`（函数内延迟
import 避免 api ↔ graph 循环依赖），下一轮规划即拿到新工具清单。

**另注（非本条修复范围）**：「你有没有 X 工具」这类能力自问走 chitchat /
直答链路，模型上下文里没有工具清单（MCP 摘要只在动态工具循环 SELECT_TOOLS
阶段注入），所以自省式提问本来就会答错 —— 验证新 MCP 请直接下任务。

**验证**

- `pytest -k "reload or planner"` 全过；ruff 零告警；mypy 无新增错误。

**教训**

热重载类入口必须同步失效所有引用被重载资源的缓存层 —— 连接换了、缓存没清，
等于重载只对一半链路生效。今后新增任何模块级缓存都要自问：谁负责在配置/
连接变更时清它。

---

### #172 「文件列表」页签未导入工程时整片空白，无任何导入入口（#172）

**现象**

开发模式资源管理器切到「文件列表」页签，未导入任何工程时面板纯白空白，
用户找不到导入按钮（对比「系统功能点」页签空态有「导入工程」按钮）。

**根因**

`ProjectFileTree.tsx` 在 `openedProjects` 为空时直接 `return null`，
整个页签不渲染任何内容；导入入口此前只存在于 File 菜单 / 命令面板 /
「系统功能点」页签，「文件列表」自身从未提供。

**修复**

空态改为渲染居中提示 + 「📁 导入工程」按钮，复用 `pickAndImportFolder()`
（选目录 → 加入文件树 + codenav 索引 + 触发功能点 AI 提取）。已导入工程后
渲染路径不变；`LeftAssetTree` 仅在有工程时渲染该组件，不受影响。
新增回归测试覆盖空态按钮展示与点击导入链路。

**验证**

- `pnpm vitest run tests/projectFileTree.test.tsx` 8/8 通过；eslint 零告警。

---

### #173 纯 JavaScript 前端工程导入后提取 0 功能点（#173）

**现象**

导入 `.js/.jsx` 前端工程（如 hby-js）后，业务功能点提取提示「提取完成但
未生成任何功能点（工程中可能没有受支持的源码文件）」。

**根因**

`codenav/language_registry.py` 的后缀白名单只注册了 `.java/.py/.ts/.tsx`
（当时注释称 tree-sitter-typescript 不含 JS grammar，未引入独立包），
biznav 扫描阶段拿不到任何候选文件，直接产出 0 功能点。`indexer.py` 的
`_extract_from_tree` 其实早已预留 `"javascript"` 分支，仅缺注册。

**修复**

新增依赖 `tree-sitter-javascript`，在 `_LANGUAGE_BUILDERS` 注册
`.js/.jsx → javascript`，符号抽取复用既有 `_extract_typescript` 逻辑。
codenav 索引与语法检查同步受益。

**验证**

- 新增回归 `test_codenav_indexer.py::test_extract_javascript_js_and_jsx`；
  codenav / biznav 相关测试全部通过，ruff check 零告警。
- 临时脚本端到端验证：`.js` 文件可抽到 function/class/method 符号。

---

### #174 中央分隔条不可拖 + 点预览没反应（#174）

**现象**

1. chat 与编辑器/文件区中间的分隔条无法拖动调节宽度，两侧永远五五开；
2. 编辑器工具栏点「▶️ 预览」毫无反应，无任何提示。

**根因**

1. `CenterChatFlow` 的 `Splitter` 是静态 4px 色条，没有任何拖拽事件；
   左右两栏各自 `flex: 1` 固定均分。
2. `PreviewButton.startPreview` 的 `catch {}` 静默吞掉后端错误 ——
   `/preview/start` 失败（Node.js 缺失 / node_modules 缺失 / Agent 离线）
   时返回的 400 错误详情用户完全看不到，注释里写的「状态栏承接」
   实际上从未实装。

**修复**

1. `Splitter` 改为可拖拽分隔条（同左/右侧栏 sash 方案：pointer capture +
   按容器内相对位置换算），新增 `uiStore.chatPaneRatio`（0.15–0.85，默认 0.5，
   持久化），对话区与编辑器/预览区按占比分配；双击复位均分；上下拆分同理。
2. 预览启动失败改为 `window.alert` 弹窗展示错误原因 + 项目目录 +
   排查提示（需 Node.js + Agent 在线），不再静默。
新增回归：`tests/centerSplit.test.tsx`（拖拽换算/夹取/双击复位）、
`tests/preview.test.tsx` 失败路径断言弹窗文案。

**验证**

- `pnpm vitest run tests/centerSplit.test.tsx tests/preview.test.tsx` 16/16 通过；
  eslint 零告警。

**教训**

用户可见动作的失败路径绝不允许静默吞错 —— 「没反应」是最差的错误体验，
比报错更让用户困惑。写 `catch {}` 前先问：用户能从这个分支里知道发生了什么吗？

---

### #175 常见语言支持补齐：Vue / Go / C/C++ / C# / PHP / Ruby / Rust / Kotlin / Swift / Scala（#175）

**背景**

#173 修复后仅补齐 .js/.jsx；用户要求「常见的语言都要支持」。此前后缀白名单只有 6 种，
导入 Vue / Go / C++ 等工程同样会提取 0 功能点。

**设计**

codenav 语法层（符号索引/语法检查）与 biznav 扫描层共用同一后缀白名单：
- `language_registry` 新增 10 种语言的 tree-sitter grammar 注册
  （go/c/h/cpp/cc/hpp/cs/php/rb/rs/kt/swift/scala）；
- `.vue` 为虚拟后缀（PyPI 无独立 grammar 包）：进白名单但无整文件 parser，
  indexer 抽 `<script>` 块（`lang="ts"` 走 TS、其余走 JS）后复用既有 TS/JS 抽取，
  行号按块位置偏移，language 标为 'vue'；
- 新增 `generic_extractors.py`：声明式节点类型表 + 容器递归（含 body 包装层穿透，
  否则类内方法抽不到），新增语言只需加一条 spec；节点类型全部实测自 AST；
- biznav 功能点提取只读原文喂 LLM，白名单扩容后自动受益，无需改动；
- 前端 `Language` 类型 / `LANGUAGE_COLORS` 同步扩到 15 种，筛选按钮改由颜色表派生。
- 注：编号与 #174（中央分隔条）同期，台账录入时发现撞号，改记 #175。

**验证**

- 新增 `tests/test_codenav_multilang.py` 14 个用例（每语言顶层声明 + 类内方法 +
  parent_class + Vue 双 script 块行号偏移 + 全量扫描入库）；
- codenav + biznav 全量 173 用例通过；ruff check/format 零告警；
  前端 tsc --noEmit + eslint 通过，相关 vitest 10/10。

**备注**

tree-sitter-vue 不在 PyPI，若日后需要整文件模板语法检查再评估自编译 grammar；
`uv sync` 因运行中进程锁 .pyd 失败，本次用 `uv pip install` 直装，
依赖清单已同步进 pyproject（下次干净环境 `make install` 会拉齐）。

---

### #175 日志分析三连：预览白名单死局 + DEBUG 刷屏 + asyncio 管道噪音（#175）

**现象**

用户日志（`%LOCALAPPDATA%\Enterprise AI IDE\logs`）排查：
1. `POST /preview/start` 连续 5 次 400，但 agent.log 里没有任何预览相关错误；
2. agent.log 被 aiosqlite 每条 SQL 两行 DEBUG 刷屏（152KB 日志九成是噪音）；
3. 每十秒左右一条 `asyncio: Exception in callback _ProactorBasePipeTransport
   ._call_connection_lost` + ConnectionResetError 10054。

**根因**

1. 预览路径白名单（`path_policy`）默认只放行 `~/.eaide/projects` 与用户 home 直接
   子目录；用户的工程在 `D:\work\...`，必然被拒。错误文案让用户「在配置
   `preview_allowed_paths` 中加入该目录」，但前后端根本没有这个配置的修改入口 →
   死局；且 #174 之前前端静默吞错、后端不落日志，两头都看不见原因。
2. `main.py` 的 `logging.basicConfig(level=logging.DEBUG)` 硬编码，`settings.log_level`
   （默认 info）只管了 uvicorn 的访问日志，没管应用日志。
3. Windows ProactorEventLoop 上子进程（MCP / executor）stdio 管道被对端先断开时，
   `_call_connection_lost` 抛 ConnectionResetError 10054，默认异常 handler 按 ERROR
   刷屏。这是正常生命周期收尾，不是故障。

**修复**

1. 白名单打通：`StartPreviewRequest` 新增 `allow_path`；路径被拒时前端
   `PreviewButton` 弹确认框，用户确认后带 `allow_path=true` 重试 → 后端把该目录
   写入 `preview.db::preview_allowed_roots`（持久化 + 审计）并加入运行时白名单，
   重启后自动回载。入口仅限已导入工程（前端文件树控制），写操作仍走审计。
   后端 400 同时落 `log.warning`，不再两头静默。
2. 日志级别跟随 `settings.log_level`，root handler 级别同步钉住。
3. lifespan 启动时注册自定义 asyncio 异常 handler：ConnectionResetError 管道重置
   降为 DEBUG，其余异常照常默认处理。
新增回归：`test_preview_v0.py::TestPreviewAllowedRoots`（3 条：确认放行/重启回载/
运行时合入）、`preview.test.tsx` 白名单确认重试 2 条。

**验证**

- `pytest tests/test_preview_v0.py` 72 条全过；ruff / mypy --strict（preview 包）零告警。
- `pnpm vitest run tests/preview.test.tsx` 15/15；eslint 零告警。
- `cargo check --lib` 通过（preview_start 新增 allow_path 参数）。
- `test_preview_e2e.py` 3 条失败经 git stash 对照证实为改动前既有问题（与本次无关）。

**教训**

安全白名单必须配套可达的解锁入口，否则就是功能死局；报错文案里指引用户做的操作，
先确认那条路真的存在。

---

### #176 纯 HTML 工程预览被强制要求 Vite/Node —— 改走进程内静态服务（#176）

**现象**

用户已装 Node.js v24，对老系统纯静态工程（`D:/work/.../remoteAuth/html`，
无 package.json / node_modules）点预览，报 400：「未找到 Vite：项目
node_modules 缺失或未安装 vite。请先运行依赖安装或安装 Node.js ≥ 18」。
HTML 预览本不需要 Node，报错误导。

**根因**

预览引擎所有框架一律走 Vite 子进程（`_resolve_vite_command` 找不到 vite 即抛错）；
框架检测明明已把无 package.json 的目录判为 `html`，却没有对应的零依赖服务路径。
另注：用户机器 Node 已装但仍报「未找到」是因为真正缺的是 vite 二进制，文案把两件事混在一起。
（vue/react 类工程需要 Node 时，检测走 Agent 进程 PATH 的 `shutil.which("node")`，
装完 Node 后需重启应用才能继承新 PATH。）

**修复**

1. `vite_manager`：`framework == html` 时改用进程内 `ThreadingHTTPServer`（绑定
   127.0.0.1），零 Node 依赖；`StaticServerProcess` 仿 asyncio.Process 接口，
   既有生命周期逻辑（停止/重启/内存监控分支）直接复用。
2. `session_manager`：HTML 不再生成/下发 `.eaide-vite.config.mjs`（不污染用户工程）；
   入口文件在项目内时 URL 直达该文件（否则开目录首页）；`restart_crashed` 透传框架。
3. 确定性关停：直接置 `__shutdown_request` + 哨兵连接唤醒 select（避开 `shutdown()`
   死等服务线程的坑）；退出钩子弱引用登记兑底收割孤儿服务，解释器退出不被拖累。
   （排查中发现旧实现会让 serve 线程泄漏并在解释器退出时死锁，已一并修掉。）
新增回归：真实起静态服务拉 HTTP 内容 / 停止后端口释放 / 入口路径归一 3 条。

**验证**

- `pytest tests/test_preview_v0.py` 74 条全过（含新 5 条）；此前「组合连跑退出挂死」
  的坑经独立复现脚本验证已干净退出；ruff / mypy --strict 零告警。
- 前端无需改动：会话返回的 url 直接可用（预览窗口 / 内嵌面板同源）。

**教训**

框架检测已给出分支结论时，执行层必须有对应的全部分支路径，否则检测形同虚设；
「零依赖能做的事不要引入运行时依赖」对老系统兼容尤其重要。

---

### #177 CI 全链路转绿：W605 非法转义 + 格式漂移 + local_small 5xx 穿透（#177）

**现象**

Actions run 33140683099（CI #33）lint-py 失败：`test_builtin_v2.py:874` 报
W605 invalid escape sequence `\P`；同批还有 B010（vite_manager setattr）、
2 处 I001 import 排序、21 个文件格式漂移。test-py 因依赖 lint-py 从未跑过，
存量雷未排：`local_small` 后端 5xx 时 HTTPStatusError 穿透、preview 用例与 #176
HTML 静态降级新语义不符、live 探针只测端口不测后端。

**根因**

1. 测试字面量 `"C:\Program Files"` 未加 r 前缀（`\P` 非法转义）。
2. 本地提交未跑 `ruff format`，格式债务累积。
3. `local_small._chat` 只捕获 ConnectError/Timeout，网关活着但模型服务挂（5xx）
   时异常穿透，安全兜底失效。
4. `test_vite_unavailable_clear_error` 造的是纯 HTML 工程 —— #176 后这类工程走进程内
   静态服务不再依赖 Vite，用例前提已不成立。
5. `test_llm_internal` 可达性探针只做 TCP 连接，内网网关回 502 时 live 用例照跑照红。

**修复**

1. `test_builtin_v2.py`：字面量加 `r` 前缀；`vite_manager` setattr 补 `# noqa: B010`
   （私有名改写的 setattr 是刻意为之，直接赋值会被 mypy 拦截）；`ruff check --fix`
   + `ruff format` 清掉 I001 与格式漂移。
2. `local_small._chat`：`httpx.HTTPStatusError` 且 ≥500 → 视为不可用抛
   `LocalSmallUnavailableError` 走安全兜底；4xx 仍上抛便于排障。
3. `test_vite_unavailable_clear_error`：工程补 `package.json`（react 依赖）让其被判为
   Vite 系，Vite 缺失降级路径重新成立。
4. `test_llm_internal` 探针升级为 TCP + `/v1/models` HTTP 探测，<500 才算可用；
   后端挂了自动 skip，与 CI 无内网时行为一致。
5. CI #34 暴露的跨平台雷（同批修复）：
   - `test_ppt_master_bootstrap` fixture 只造 `python.exe`，Linux 上 `resolve` 找 `python3`
     永远落空 → 改按平台造名。
   - `/data/export` 路径穿越校验在 Linux 拦不住反斜杠写法（`\\` 非分隔符）→
     归一化后补一道校验（透传保留原值，不动 Windows 分隔符语义）。
   - `test_start_and_info` 硬编码 5173，CI runner 该端口被占时避让机制选 5174 →
     改断言落在分配区间。
   - `test_restart_crashed` 空依赖被判 HTML 框架（#176 后不走 spawner，Linux 上
     同端口重绑必失败）→ 改用 vue 依赖走 Vite 子进程路径。

**验证**

- `uv run ruff check .` / `ruff format --check .` / `uv run mypy packages` 全过；
  `uv run pytest -q` 2700+ 条全绿（live 用例因内网后端 502 按预期 skip）；
  `pnpm lint` / `pnpm test`（50 文件 331 用例）全绿；
  `cargo check --lib` / `cargo clippy --all-targets -- -D warnings` 通过。
- Rust 3 条内置 shell 用例仅本机 Windows 环境失败（依赖本地 shell 行为），
  CI ubuntu 不受影响。

**教训**

test-py 被 lint-py 门控挡住时，存量测试雷不会暴露 —— lint 失败要连根拔掉而不是只修
当次报错行；「不可达降级」类客户端要把 5xx 与连接失败同等对待，否则兜底链断在中间态。

---

### #178 Phase 19 自进化两处隐患 —— 收尾后台任务可被 GC + 影子实验 db_path 只透传一半（#178）

| 字段 | 内容 |
|---|---|
| **是否修复** | 已修复 |
| **修复时间** | 2026-08-31 |
| **发现方式** | Phase 19 模块代码评审（review 报告问题 1 / 2） |
| **涉及文件** | `services/agent/src/agent/graph/stream.py`、`services/agent/src/agent/evolution/prompt_opt.py` |

**现象**

两个无用户报障的潜伏缺陷，均在代码评审中发现：
1. 任务收尾的轨迹抽取/反思后台任务偶发静默丢失 —— 进化信号、失败经验不入库，无任何日志；
2. 影子优化实验指定非默认 `db_path` 时，回放素材从生产库读、候选版本却写进指定库，
   出现「无可回放的历史请求」误报或数据撕裂。

**根因**

1. `graph/stream.py` 收尾钩子里 `asyncio.create_task(record_run_outcome(...))` 的返回值被丢弃。
   事件循环对 Task 只持弱引用（官方文档明确警告），任务随后挂起在 SQLite 写入 / LLM 反思
   等 I/O 上，可能被 GC 回收 → 整条进化链路静默丢失。同模块 `evolution/api.py` 已有正确范式
   （`_BACKGROUND_TASKS` 持引用），此处遗漏。
2. `prompt_opt.py::run_prompt_experiment(db_path=X)` 把 `db_path` 传给了 `low_score_feedback`
   与 `insert_prompt_version`，但 `_replay_requests`（内部硬编码 `storage._db_target()` 无参）
   与 `_record_run`（均未传 `db_path`）始终读写默认生产库 —— 同一实验横跨两个库。
   存储层其余函数均遵守 `db_path` 契约，此两处是唯一破口。

**修复**

1. `stream.py`：新增模块级 `_EVOLUTION_BG_TASKS: set[asyncio.Task[Any]]`，收尾任务创建后入集合，
   `add_done_callback(_EVOLUTION_BG_TASKS.discard)` 完成即移除，与 `evolution/api.py` 同范式。
2. `prompt_opt.py`：`_replay_requests` / `_record_run` 增加 `db_path` 关键字参数并透传至
   `storage._db_target(db_path)` / `storage.record_experiment_run`；`run_prompt_experiment`
   内全部 4 处 `_record_run` + 1 处 `_replay_requests` 调用统一传入，保证整个实验在同一库内完成。

**验证**

- `ruff check` / `ruff format --check` 两个改动文件均通过。
- `uv run pytest tests/test_evolution.py tests/test_evolution_v1.py tests/test_evolution_v15.py -q`
  59 条全绿。
- `mypy --strict` prompt_opt.py 零错误；stream.py 的 36 条 `dict` 类型参数告警经逐行核对均为存量，
  改动行区间无任何新增错误。

**教训**

coroutine 转后台任务必须持强引用 —— 「fire-and-forget」在 asyncio 里会被 GC 静默吃掉，
项目内已有正确范式时就该全仓对齐而不是各自发挥；参数契约（如 `db_path`）在函数内部透传时，
要逐一核对每一处下游调用，漏传一半比完全不传更隐蔽（部分路径写对了，剩下的路径撕裂数据）。

---

### #179 Phase 19 评审建议六项清零 —— 脱敏/单 active/反思去重/蒸馏去重/失败误判/检索 DDL（#179）

| 字段 | 内容 |
|---|---|
| **是否修复** | 已修复 |
| **修复时间** | 2026-08-31 |
| **发现方式** | Phase 19 模块代码评审（续 #178，建议级问题 3–8） |
| **涉及文件** | `skills/schema.py`、`evolution/trajectory.py`、`evolution/storage.py`、`evolution/reflection.py`、`evolution/prompt_opt.py`、`evolution/api.py` |

**现象**

六处健壮性/红线对齐隐患（均为低概率边界，无用户报障）：轨迹落库残留用户原文（可能含连接串）、
自动采纳可能双 active、同轨迹双重反思、approve 后窗口期重复蒸馏、失败判定裸短词误判、
经验检索热路径每次执行建表 DDL。

**根因与修复**

1. 轨迹的 `reason`（用户提示词）/ `answer_digest` / `rewritten_query` 直接存原文，用户可粘贴连接串。
   `skills/schema.py` 新增 `scrub_dsn()`（与 `_DSN_PATTERNS` 同源，整段替换含 user:pass@host 的
   DSN 与 `name:pass@host` 凭证形态），`trajectory.py` 三处落库前统一过脱敏。
2. `prompt_opt.run_prompt_experiment` 自动采纳直接插 `active` 不降级旧版。抽公共函数
   `_demote_other_actives`，自动采纳与 `api.apply_prompt_version` 共用，保证同 skill 单 active。
3. env 失败反思与用户 👎 反思可对同轨迹各跑一次。`run_reflection` 入口按 `source_session`
   查重（新增 `storage.has_experience_for_session`），已有产出即跳过，不白跑本地 LLM。
4. `has_draft_for_signature` 只拦 `draft`。改 `status IN ('draft','approved')`，关闭新技能被采纳后、
   带 `active_skill_id` 轨迹产生前的重复蒸馏窗口期。
5. `_FAIL_MARKERS` 裸短词（「重试」等）会把「重试成功」类成功回答误判失败。改与 `tools/loop.py` /
   `responder.py` 硬失败文案同源的整句短语（如「次后仍然失败」「任务已被用户停止」）。
6. `retrieve_experiences_sync` 每次执行建表 DDL（隐式写事务，加剧 database is locked）。按库的**绝对路径**
   缓存「已建表」（相对路径随 cwd 漂移，测试隔离靠 chdir，绝不能拿原始字符串当键），异常时剔除缓存项。

**验证**

- 新增回归 7 条：scrub_dsn 脱敏/不误伤、落库文本脱敏断言、失败误判正反例、反思会话去重、
  approved 拦重复蒸馏、自动采纳降级旧 active、建表缓存检索照常；连同存量共 66 条全绿。
- `ruff check` / `ruff format` 全过；`mypy --strict` evolution 包六个文件零错误，
  `schema.py` 仅 3 条存量 `dict` 类型参数告警（非本次改动行）。

**教训**

text 落库前脱敏与校验层用同一套形态定义，避免两处各自维护漂移；状态机两条写入路径必须共享同一
降级/去重函数；启发式文案匹配用整句而非裸短词；进程级缓存键必须绝对化（相对路径 + chdir 隔离必串）。

---

### #180 财税规则混合检索：进程内向量模型启用后无关文档误召回（#180）

| 字段 | 内容 |
|---|---|
| **是否修复** | 已修复 |
| **修复时间** | 2026-08-31 |
| **发现方式** | 意图识别四层增强全量回归（`test_doc_review_fiscal_rules` 失败） |
| **涉及文件** | `config.py`、`doc_review/fiscal_rules.py` |

**现象**

进程内 ONNX 向量模型（bge-small-zh-v1.5）随意图增强默认启用后，财税规则混合检索的语义通道
第一次在开发机真正生效：闲聊文本（「今天天气真好，我们一起去公园散步吧」）也能召回 2 条财税规则，
`test_irrelevant_document_gets_no_rules` 失败。此前 `local_embedding_base_url` 未配置时语义通道
缺席、全程纯关键词，掩盖了阈值缺陷。

**根因与修复**

入选条件只有融合分阈值 `doc_review_fiscal_min_score`（0.10，按 (1-w)·BM25 + w·余弦 量纲设计）；
无关中文对的 BGE 基线余弦 ~0.3，乘 0.5 权重后仍过 0.10。新增语义通道独立下限
`doc_review_fiscal_sem_min`（默认 0.40，实测无关 ≤0.321 / 真相关 ≥0.595，取中隔开），
无关键词命中时需同时过独立语义下限与融合分阈值；关键词通道行为不变。

**验证**

- 闲聊文本召回为空、真实财税文本召回不受影响；`test_doc_review_fiscal_rules` 全绿，
  后端全量退出码 0。
- 相关用例：新增 `tests/test_onnx_embedding.py`（路径回退 + 降级）6 条。

**教训**

检索阈值与模型绑定：语义通道从「配置才生效」变成「默认启用」时，必须重测阈值在真实模型上的
分数分布，而不能沿用按降级形态（纯关键词）调出的参数；基线余弦高的中文模型尤其需要独立语义下限。

---

### #181 提交前漏跑 `ruff format --check`，CI lint-py 红灯（#181）

| 字段 | 内容 |
|---|---|
| **是否修复** | 已修复 |
| **修复时间** | 2026-08-31 |
| **发现方式** | 推送后 CI（run 33371091666，lint-py / ruff format --check） |
| **涉及文件** | 意图增强 7 个新增/改动文件（intent_memory / intent / semantic_route / types 等） |

**现象**

v2.121 提交后 CI lint-py 失败：7 个文件不符合 `ruff format` 排版（行折叠/括号换行）。
本地验证只跑了 `ruff check`，漏了 Makefile lint-py 的第二步 `ruff format --check`。

**修复**

`ruff format` 统一格式化 7 文件，`format --check .` 全仓绿 + 受影响 61 用例复跑全过后补提交推送。

**教训**

text 提交前验证清单必须与 Makefile `lint-py` 完全对齐（check + format --check 两步），
只跑其一等于把另一半留给 CI 踩雷。

---

### #182 停滞熔断只甩模板句直接停，不反向追问用户缺什么（#182）

| 字段 | 内容 |
|---|---|
| **是否修复** | 已修复 |
| **修复时间** | 2026-08-31 |
| **发现方式** | 用户实测截图：「做一个介绍联想公司的 ppt」任务连续 3 轮零成功后被熔断 |
| **涉及文件** | `tools/loop.py`、`graph/state.py` |

**现象**

PPT 生成任务连续 3 轮工具执行无有效结果，停滞熔断触发后直接甩一句模板文案
「请补充关键信息或换一种表述」终止循环：用户看不出到底缺什么（素材？依赖？要求？），
任务卡死在「等待人工干预」但无任何可操作的提问。熔断的本意是防空转，
但把「停止」当成了终点，丢掉了「把缺什么问清楚」这一步。

**根因与修复**

`_stagnant_msg` 是无上下文的固定文案，达阈即 `_done` 终止，模型没有机会基于
已有失败做针对性追问。修复：熔断从「立即停」改为「先追问、再停」——
1. 达阈时不终止，置位 `tool_stagnant_asked`（新增 state 字段）：提示词协议路径经
   EXTRA_RULES、native 路径经追加 messages，注入强制指令，逼模型输出 ASK_USER /
   调 ask_user，反向列出需要用户补充的东西（素材路径、目标结构、环境依赖等）；
2. 追问后仍无进展才终止，终答 `_stagnant_msg` 附最近失败原因清单（去重、
   最新优先，最多 3 条），并告知可回复「继续」换路子接续；
3. 任一轮成功执行即复位 streak 与追问旗标，不背历史欠账。
   空转防护不放松：追问只给一次，重复调用熔断（#165）不受影响。

**验证**

- 新增/更新回归 9 条：提示词协议 3 条（达阈不终止且下轮注入指令、模型追问后
  正常退出、成功后复位）+ 单元 3 条（指令注入条件、终答附失败原因、空结果不报错）
  + native 3 条（追问后 ask_user、追问后仍败附原因终止、成功复位）；
  存量熔断/重复调用/瞬时重试测试全绿，后端全量退出码 0。
- `ruff check` / `ruff format` 全过；`mypy` 无新增错误（存量基线不变）。

**教训**

熔断/限流类防护的终止文案不能是孤立模板句：终止前要么把失败原因带出来，
要么给模型一次「反向追问」的机会把缺什么问清楚——防护机制的目标是把任务
导回可推进状态（用户补信息），而不只是停止烧资源。

---

### #183 首轮问候耗时 34.6s：隐形探测/路由开销 + 无后端空探测（#183）

| 字段 | 内容 |
|---|---|
| **是否修复** | 已修复 |
| **修复时间** | 2026-09-01 |
| **发现方式** | 用户实测截图：一句「你好」整轮耗时 34.6s，而控制台仅显示意图识别 4983ms + 回答生成 0ms |
| **涉及文件** | `llm/router.py`、`dual/router.py`、`graph/nodes/intent.py`、`graph/state.py`、`trace/collector.py`、前端 `ThinkingStepCard.tsx` |

**现象**

首轮闲聊总耗时与可见步骤耗时严重不符（~30s 无归属）。排查发现三块隐形开销：
1. `mode_router` 首节点每 run 调 `resolve_native_backend()` 原生工具调用探测，
   外层超时 15s，配了慢/不通的内网端点时干等；且未配置任何后端时仍逐个走客户端构建链路；
2. `intent` 节点的 `duration_ms` 在 Skill 路由之前截表，SkillRouter 探活 + 最多两轮
   Ollama 分类（最坏 ~11s）完全不在 trace 里；
3. 语义路由直出/闲聊这类零 LLM 场景仍会跑完整 Skill 路由。

**根因与修复**

1. `resolve_native_backend()` 增加无后端快路径：先查注册表，无已启用内网/云端后端时
   直接判无并缓存，不构造客户端、不发任何探测请求（本地 Ollama 本就不参与）；
   探测超时自 15s 降至 4s（失败本就回退提示词协议，长等无收益）。
2. `intent_node` 对语义路由直出（`_route` 命中）与 chitchat 直接短路 SkillRouter；
   强钉是用户显式动作，保持最高优先级不受影响。
3. 隐形耗时显形：`mode_router` trace 补 `duration_ms` + `native_probe_ms`（并落 cot.log）；
   intent 节点内 Skill 路由阶段单独计时落 `skill_route` trace 条目（`NodeName` Literal、
   后端 `NODE_LABELS`、前端 `NODE_META` 同步补中文名）。

**验证**

新增回归：`test_intent_skill_shortcut.py`（闲聊/_route 短路不得构造 SkillRouter +
对照组正常路由）、`test_router_native_probe.py`（空/仅本地注册表不发探测 +
已启用内网后端照常命中）。既有 141 条相关用例（强钉/快路径/语义路由/原生工具环）全绿，
前端 tsc/eslint/vitest 通过；全量后端仅 1 条与本次无关的 `test_builtin_office`
真实二进制用例偶发失败（单独重跑通过）。

**教训**

节点计时要在「所有副作用结束后」截表，且每个会发网络请求的阶段都要有独立 trace 条目；
能力探测类逻辑必须先问「有没有必要探」——未配置目标时零开销直判，而不是靠超时兜底。

---

### #184 build-all 调用的 .ps1 存为 UTF-8 无 BOM，被 Windows PowerShell 5.1 按 GBK 误读致整脚本解析失败（#184）

| 字段 | 内容 |
|---|---|
| **是否修复** | 已修复 |
| **修复时间** | 2026-09-03 |
| **发现方式** | 打包前置核查：直接跑 fetch-ppt-master.ps1 下 cp314 wheel 时，powershell 报「数组索引表达式丢失/字符串缺少终止符」等 7 处解析错误，中文全成乱码（`鎶€鑳藉寘涓嬭浇澶辫触`） |
| **涉及文件** | `infra/scripts/fetch-ppt-master.ps1`、`infra/scripts/fetch-officecli.ps1`、`model/download_bge_reranker_onnx.ps1`、`model/download_bge_onnx.ps1` |

**现象**

build-all.bat 用 `powershell`（Windows PowerShell 5.1）调用这些含中文注释/字符串的 .ps1。5.1 对无 BOM 文件按系统 ANSI（GBK）解码，UTF-8 中文字节被误读，个别多字节序列吞掉相邻的 `"` 字符串终止符 → 级联解析错误，整个脚本无法执行。用 5.1 解析器实测：fetch-ppt-master.ps1 FAIL 7、download_bge_reranker_onnx.ps1 FAIL 3（fetch-officecli/download_bge_onnx 仅字节碰巧没触发，属同类定时炸弹）。而 pwsh 7 默认 UTF-8，`[Parser]::ParseFile` 校验反而报 OK，掩盖了问题——CI（release.yml 用 pwsh）不受影响，仅本地 build-all.bat 用 powershell 触发。

**根因与修复**

这些脚本此前均为 UTF-8 无 BOM。给 4 个 .ps1 前置 UTF-8 BOM（`EF BB BF`）：5.1 见到 BOM 即按 UTF-8 正确解码，pwsh 7 亦兼容；采用字节级 prepend，正文与 CRLF/LF 换行一字不动。修复后 5.1 解析全部 OK。

**验证**

经 `powershell`(5.1) 的 `[System.Management.Automation.Language.Parser]::ParseFile` 复测 4 脚本 → 全 OK；fetch-ppt-master.ps1 实跑成功下载 65 个 wheel。

**教训**

Windows 上会被 `powershell`(5.1) 调用、且含非 ASCII 的 .ps1 必须存为 **UTF-8 with BOM**；跨 5.1/pwsh7 的脚本要用 5.1 解析器实测，别只信 pwsh7 的 ParseFile（两者对无 BOM 文件的默认编码不同，会漏判）。用 Write/SearchReplace 写 .ps1 后应复检 BOM 是否仍在。

---

### #185 fetch-ppt-master.ps1 的 Resolve-Python 命中无 pip 的 uv venv，静默下载 0 个 wheel（且已先清空旧 wheel）（#185）

| 字段 | 内容 |
|---|---|
| **是否修复** | 已修复 |
| **修复时间** | 2026-09-03 |
| **发现方式** | 修完 #184 后重跑 fetch，日志 `via C:\Windows\py.exe` 后紧跟 `.venv\Scripts\python.exe: No module named pip`，最终 `done: skill + 0 wheels` |
| **涉及文件** | `infra/scripts/fetch-ppt-master.ps1` |

**现象**

脚本先 `Remove-Item deps\*.whl` 清旧 wheel，再用 `Resolve-Python` 取解释器跑 `pip download`。Resolve-Python 仅按 `py/python/python3` 顺序返回第一个存在的命令、**不校验 pip 可用性**；在激活了 uv 建的 `.venv`（不含 pip）的 shell 里，裸 `py` 命中 venv 的 python → `No module named pip` → 下载 0 wheel。旧 wheel 已被清空，deps 直接变空，PPT 离线依赖全丢；而脚本对该失败仅 `Write-Warning` 不中断，`done: 0 wheels` 表面像成功。

**根因与修复**

跨版本 wheel 下载（`--python-version/--abi` 决定目标）只要求解释器**带 pip**，与被调用解释器自身版本无关。将 `Resolve-Python` 改为 `Resolve-PipPython`：逐个候选探测 `-m pip --version` 退出码，返回首个带可用 pip 的调用（优先 `py -3` 取注册的真实解释器，绕过 venv），并以 `@{Exe;Args}` + 数组 splatting 传参；全不可用则 `throw` 明确错误，不再静默下 0 wheel。

**验证**

修后重跑 `via C:\Windows\py.exe -3` → 真实 3.14（pip 25.3），成功下载 65 个 wheel（12 cp314 + 4 abi3 + 余 none-any，52MB）+ 生成 requirements-offline.txt，deps 无 cp312 残留。

**教训**

调外部解释器跑 `-m pip` 前必须探测 pip 实际可用（退出码），别假设「命令存在=能用」；激活 venv 的开发机与干净构建机上 `py`/`python` 解析目标不同，脚本要能绕过无 pip 的 venv。「先删旧产物再下载」的步骤一旦下载静默失败即净损失——失败必须硬中断，或改为先下后删。

---

### 待跟进：工具编排预算 24 轮上限

> **2026-08-27 更新**：本条最初写于 #163 修复时，当时推测「24 轮不够用」。
> 后经审计日志核实，真相是 22 轮被空转烧掉 —— 详见 **#165**。预算本身够用，
> 结论已被推翻，保留此条仅为记录判断过程。

`config.py::tool_loop_max_turns` 默认 24、硬上限 `le=30`（`tools/loop.py` 注释说明
旧默认 8 轮会误杀 PPT 这类 10+ 步长链任务，故提至 24）。预算耗尽时的文案是
「直接再发一句『继续』即可从断点接续」—— 但这条恢复路径依赖历史上下文，
而 #163 恰好让「继续」完全失效。两个缺陷叠加，用户就撞上了无法绕过的死路。

现状：#163 修复后「继续」应当可用，#165 修复后空转会在第 3 轮被掐断。
预算上限暂不调整 —— 先看熔断生效后长链任务是否还会触顶。若仍频繁触顶，
再评估提高 `le` 上限或引入基于 `task_id` 台账的断点续跑（不依赖对话历史）。

---

### #186 打包后向量检索全仓静默失效 —— sqlite-vec 原生扩展 vec0.dll 未随 exe 分发

| 字段 | 内容 |
|---|---|
| **是否修复** | 已修复 |
| **修复时间** | 2026-09-03 |
| **发现方式** | 用户问「上传后向量模型是否真运行」；查生产日志见 `hybrid_search ... bm25=20 vec=0`（向量通道恒 0），直查生产 kb.db 发现 `kb_chunks_vec` 表不存在，且 data_root 下所有 .db（router.db / doc_review_vec.db / data_expert.db）均无任何 vec0 虚表 |
| **涉及文件** | `eaide-agent.spec` |

**现象**

本地知识库混合检索上传入库看似正常（chunks/FTS5 都在，`ingest done children=103`），embedding 模型也确实加载运行（日志 `onnx_embedding: loaded`、`kb_meta.dim=512`），但文档审核时 `hybrid_search ... vec=0` —— 向量通道恒返 0，检索实际只靠 BM25。进一步查生产 data_root 下所有 SQLite 库，**没有任何一个 vec0 虚拟表**（kb_chunks_vec / l2_cache_vec / fiscal_vec / route_vec 全无），说明 sqlite-vec 在打包后的 exe 里从未成功建表。

**问题原因**

`agent/vector_store.py::load_extension` 走 `sqlite_vec.load(conn)` → `conn.load_extension(<sqlite_vec 包目录>/vec0)`，依赖包内原生扩展 `vec0.dll`（282KB）。该 .dll 是**包内数据文件、运行时按路径动态加载**，不是 Python 导入的二进制依赖，PyInstaller 静态分析发现不了，也无内置 hook 收集。`eaide-agent.spec` 既没把它列进 `binaries`/`datas`，`hiddenimports` 也没有 `sqlite_vec` → 打包后 `_MEIPASS/sqlite_vec/vec0.dll` 缺失 → `load_extension` 抛异常被 best-effort 吞掉返 False → `ensure_vec_table` 的 `CREATE VIRTUAL TABLE ... USING vec0` 失败 → 全仓所有向量表都建不起来。因为 vector_store 处处 best-effort 静默降级，开发/测试（venv 里 .dll 在）全绿，只有打包 exe 才暴露，且表现为「功能能用但向量检索悄悄退化成纯关键词」，极隐蔽。自 v2.123 sqlite-vec 全仓迁移起即存在。

**根因与修复**

`eaide-agent.spec` 新增 `_sqlite_vec_binaries()`（`collect_dynamic_libs('sqlite_vec')` → `[('.../vec0.dll', 'sqlite_vec')]`，落到 `_MEIPASS/sqlite_vec/vec0.dll`，与 `loadable_path()` 对齐），接进 `Analysis(binaries=...)`；`hiddenimports` 补 `sqlite_vec`（保证 Python 模块本体也被收集）。

**验证**

`collect_dynamic_libs('sqlite_vec')` 实测返回正确 (src, 'sqlite_vec') 目标；spec `py_compile` 通过。重新打包后 vec0.dll 随 exe 分发，向量建表恢复；存量库经设置页/知识库页「重建索引」基于库内原文重嵌入即可补齐向量（无需重新上传）。

**教训**

「靠 load_extension 按路径动态加载的原生扩展」（sqlite-vec 这类）PyInstaller 不会自动收集，必须显式 `collect_dynamic_libs`/`collect_data_files` 进 binaries/datas；best-effort 静默降级会掩盖打包缺失——凡是「开发/测试正常、只在打包 exe 里悄悄退化」的能力，验收必须真的跑一次打包产物并查持久层（本例：查 .db 里 vec0 表是否真建出来），不能只看单测绿。

---

### #187 agent.log 中文全成乱码 —— logging.basicConfig 未指定 encoding，Windows 默认 GBK 落盘

| 字段 | 内容 |
|---|---|
| **是否修复** | 已修复 |
| **修复时间** | 2026-09-04 |
| **发现方式** | 用户核对 RAG 生产日志时发现 `agent.log` 里中文（上传文件名 `ingest done file=...`、部分 INFO 日志）全是乱码，而同进程写的 `cot.log`、Rust 侧 `eaide.log` 中文正常 |
| **涉及文件** | `services/agent/src/agent/main.py` |

**现象**

打包 exe 运行时，`logs/agent.log` 里所有中文都是乱码（如 `ingest done ... file=Ʊ▒▒▒.docx`），排查知识库入库/审核链路时读不出文件名与中文日志；但同一进程写的 `cot.log` 和 Rust 侧 `eaide.log` 中文完全正常。

**问题原因**

`main.py` 的 `logging.basicConfig(filename=..., ...)` 没传 `encoding`，`FileHandler` 默认用 `locale.getpreferredencoding(False)`——中文 Windows 上是 cp936/GBK，于是中文被按 GBK 落盘成乱码字节。`cot.log` 的 `FileHandler` 显式写了 `encoding="utf-8"` 所以正常，两处不一致。

**根因与修复**

`basicConfig` 补 `encoding="utf-8"`，与 `cot_log.py` 的 FileHandler 对齐，钉死 UTF-8 落盘。

**验证**

`encoding` 参数 Python 3.9+ 的 `basicConfig` 即支持（本项目 3.12/3.14）；重启后新写入的中文日志按 UTF-8 落盘、可正常读出。存量旧 agent.log 已是 GBK 字节、不追溯转换。

**教训**

Windows 上任何 `FileHandler`/`basicConfig(filename=...)` 都必须显式 `encoding="utf-8"`，否则默认跟随系统 ANSI 代码页（中文机 cp936），中文必乱码；同一项目多个日志入口要统一编码，避免「这个日志能读、那个不能读」的割裂。

---

### #188 文档审核 LLM 链后端选择与「已启用」要求不对称 + 默认回退本地 ollama

| 字段 | 内容 |
|---|---|
| **是否修复** | 已修复 |
| **修复时间** | 2026-09-04 |
| **发现方式** | 用户核对生产日志后要求：文档审核分析应「云端优先→内网 private」，且两者相同要求都必须是「模型管理」里已启用的后端 |
| **涉及文件** | `services/agent/src/agent/config.py`、`services/agent/src/agent/llm/router.py`、`services/agent/tests/test_doc_review_llm.py` |

**现象**

`generate_review` 默认链是 `["cloud", "private", "ollama"]`：① 末尾会回退本地 ollama（小模型产出的审核 JSON 质量差）；② private 分支写的是 `_build_private_client() or self.private`——`_build_private_client()` 只取注册表已启用后端，但 `or self.private` 又回退到 settings/env 配置的 private，与 cloud 分支「只认已启用注册表后端」不对称，违背「相同要求：已启用」。

**问题原因**

cloud 分支 `_build_cloud_client()` 仅遍历 `list_backends(enabled_only=True)`，未启用即 None；而 private 分支多了一层 `or self.private` 兜底，导致未在注册表启用的 private（仅 env 配置）也能被审核链使用，两个后端「已启用」判定标准不一致。

**根因与修复**

① `config.py`：`doc_review_llm_chain` 默认改为 `["cloud", "private"]`（去掉本地 ollama 兜底）；② `router.py::generate_review` private 分支去掉 `or self.private`，与 cloud 对称——只认注册表已启用的 private，查询异常按未启用处理（backend=None 跳过）；ollama 代码分支保留供显式配置，但默认链不含。两者都未启用则抛 `LLMBackendError`。同步校正 `generate_review`、`retriever.py`、`doc_review/llm.py` 里过时的链描述。

**验证**

重写 `test_doc_review_llm.py`：默认链断言改 `["cloud","private"]`；新增「仅启用 private→命中 private」「cloud+private 都启用→云端优先」「都未启用→抛错不回退 ollama」三例，连同原有 cloud 用例全绿。

**教训**

同一降级链里各层后端的「可用性判定」标准必须一致（本例统一为「注册表已启用」），否则某一层偷偷放宽（settings 兜底）会让「已启用」这个开关对用户失去意义；策略性改动要连带把散落在多处的过时文档串一起校正。

---

### #189 文档审核「分析失败: doc_review.http: error sending request」—— 读取详情 GET 串行做 N 次 RAG 超过前端 15s 超时

| 字段 | 内容 |
|---|---|
| **是否修复** | 已修复 |
| **修复时间** | 2026-09-04 |
| **发现方式** | 用户报告文档审核界面红色横幅「分析失败: config: doc_review.http: error sending request for url (http://127.0.0.1:8765/doc-review/documents/{id})」，附 `AppData\Local\Enterprise AI IDE\logs` 日志 |
| **涉及文件** | `services/agent/src/agent/doc_review/api.py`、`services/agent/src/agent/config.py`、`apps/desktop/src-tauri/src/commands/doc_review.rs`、`services/agent/tests/test_doc_review_api.py` |

**现象**

文档审核分析其实**成功**了（agent.log：`doc_review run done ... findings=16 total_elapsed=237.0s`，16 条风险点已入库），但前端在分析完成后拉取详情时弹红色横幅「分析失败: config: doc_review.http: error sending request for url (.../doc-review/documents/{id})」。点「重新分析」会重跑数分钟后再次同样报错，findings 越多越必现。

**问题原因**

① 前端 `pollStatus` 见状态 `done` 后调用 `docReviewGet` → GET `/doc-review/documents/{id}`（`docReviewStore.ts:223`）。② 该端点 `get_document` 返回前同步调用 `_attach_kb_refs`（`api.py:138`），对**每条 finding 顺序**做一次混合检索（原 for 循环）：N 条 finding = N 次串行 RAG，每次 ≈2s。③ Rust 侧 `doc_review` command 的 reqwest client 写死 15s 超时（`doc_review.rs:56`）。16 条 finding 的 kb_refs 实测耗时 32.3s（agent.log：`kb_refs (rag) attached: findings=16 refs=48 elapsed=32271.6ms`），远超 15s → reqwest 抛 `error sending request` → `AppError::Config("doc_review.http: ...")` → 前端 `analyze` catch 成「分析失败」。佐证：上一份 6 条 finding 的文档 kb_refs 耗时 13.08s 刚好卡在 15s 内所以成功（阈值约 7~8 条）；`/status` 轮询全程 200 OK（不触发 kb_refs），证明服务是活的、纯粹是这一个重读接口太慢。

**根因与修复**

① `api.py::_attach_kb_refs`：把每条 finding 的检索从串行 for 改为 `asyncio.gather` + `asyncio.Semaphore` 受限并发（reranker 走 `asyncio.to_thread`、检索不阻塞事件循环，可安全并发）；gather 保留提交顺序、各 item 就地写入自己的 kb_refs，finding↔依据映射不错位；单条检索失败仍 best-effort 跳过。总耗时从 N×单次 压到 ≈(N/并发度)×单次。② `config.py`：新增 `doc_review_kb_refs_concurrency`（默认 4，ge=1 le=16）控制并发度。③ `doc_review.rs`：超时改为按 op 区分——`get`/`findings`（触发 kb_refs 的重读接口）用 90s 兜底，其余轻量 op（status/list/analyze/register/delete）仍 15s，以便 Agent 掉线时快速失败。

**验证**

新增 `test_kb_refs_parallel_preserves_mapping_and_bounds_concurrency`（12 条 finding + 假检索器记录在飞并发峰值）：断言每条 finding 拿到自己 query 对应的依据（不错位）、并发峰值 >1（确实并发）且 ≤4（受信号量限流）；连同原有 kb_refs 用例、doc_review config/analysis 套件全绿，ruff 通过。修复后 16 条 finding 的详情 GET 从 ~32s 降到 ~4s，不再超时；存量已入库的该文档无需重跑分析，重新打开即可正常显示。

**教训**

「读取型 GET」里绝不做随数据量线性放大的重活（本例 N 条 finding × 单次 RAG）——它把「读」变成了隐性批处理，一旦超过客户端超时就被误报成上游「失败」，而数据其实已成功落库，误导用户去「重试」反而重复触发同一超时。前后端超时预算必须对齐后端最坏耗时；能并发的 best-effort 富化（RAG/enrich）用 gather+信号量压缩墙钟时间，并把并发度做成可配置。

---

### #190 【文档审核】切换到已分析文档时误显示「正在分析」 —— open() 把「加载持久化结果」错当成「重新分析」

| 字段 | 内容 |
|---|---|
| **是否修复** | 已修复 |
| **修复时间** | 2026-09-04 |
| **发现方式** | 用户反馈：审核模式切换文档时，已经分析过的文档又触发了分析；结果本应持久化直接展示 |
| **涉及文件** | `apps/desktop/src/store/docReviewStore.ts`、`apps/desktop/src/components/doc-review/DocTextViewer.tsx`、`apps/desktop/src/components/doc-review/DocReviewList.tsx`、`apps/desktop/tests/docReviewStore.test.ts` |

**现象**

在文档审核列表点击一个已 `done` 的文档切换过去时，正文区把已持久化的内容模糊化并弹出「正在分析，请稍后」遮罩 + 计时器，列表里该文档也从「↻ 重新分析」变成「分析中…」，看起来像把已分析过的文档又重跑了一遍分析。实际上后端并未重新分析，结果早已落库。

**根本原因**

`docReviewStore.open(docId)` 一进来就无条件 `set({ analyzing: true })`，随后 `await ipc.docReviewGet(docId)`。而该 GET 会触发后端 `_attach_kb_refs`（逐条 finding 附加 RAG 知识库依据），即便并发化后仍需数秒（见 #189）。这几秒里 `analyzing` 为真：`DocTextViewer` 依据 `analyzing` 做 blur + 遮罩、`DocReviewList` 依据 `analyzing && selected` 显示「分析中…」，于是「读取已持久化结果」被 UI 渲染成了「正在分析」。根因是 `analyzing` 这一个标志被同时复用为「加载详情中」和「真正在跑分析」两种语义。后端 `GET /documents/{id}`、`analyze` 与存储层均无重复触发问题——findings/runs 正确持久化，切换文档只读不写。

**具体修复**

拆分两种状态：① store 新增 `loading` 标志（读取已持久化详情的加载态），与 `analyzing`（后端确有进行中 run）严格区分。② `open()` 不再无条件置 `analyzing: true`，改为先清空 detail/findings 并置 `loading: true`；GET 返回后依据 `detail.status` 判定 `inProgress`（仅 `queued/classifying/analyzing`），据此设 `analyzing`，只有 inProgress 才续接 `pollStatus`；`done/failed/none` 直接展示持久化结果。③ `analyzeDoc()` 的详情预读也用 `loading` 而非 `analyzing`。④ `DocTextViewer` 在 `!detail && loading` 时显示轻量「正在加载审核结果…」转圈，而非分析遮罩。⑤ `DocReviewList` 在 `loading && selected` 时显示「加载中…」，与「分析中…」区分。

**验证**

新增回归用例 `open on a done doc loads persisted result without re-analyzing`：mock `docReviewGet` 返回 `status: "done"` + 1 条 finding，断言 `open()` 后 `docReviewAnalyze` 未被调用、`analyzing===false`、`loading===false`、findings 读回 1 条。`docReviewStore.test.ts` + `docReviewDashboard.test.tsx` 共 7 用例全绿，`tsc --noEmit` 无类型错误。

**教训**

一个布尔状态不要承载两种语义。「加载中」与「处理中（有副作用/长任务）」在 UI 上必须分开：前者只是读回已有数据、可随时切换、不该展示阻塞式进度遮罩；后者才需要遮罩 + 进度 + 禁止打断。当某个前端标志同时驱动「遮罩/模糊化/禁用按钮」时，务必确认它的置位时机严格对应真实的后端进行中状态，而不是对应一次可能很慢的只读请求——否则只读的慢请求会被用户误读成重复触发了写操作。

---

### #191 启动空窗期界面可点但全部请求失败 —— 缺一道「Agent 未就绪」全局闸门，用户乱点放大故障

| 字段 | 内容 |
|---|---|
| **是否修复** | 已修复 |
| **修复时间** | 2026-09-04 |
| **发现方式** | 用户反馈：启动后 Agent 还未就绪，这段间隔界面看着正常、用户容易乱点，要求改为启动后整屏模糊、等 Agent 就绪后才能使用 |
| **涉及文件** | `apps/desktop/src/store/uiStore.ts`、`apps/desktop/src/lib/agentBoot.ts`（新增）、`apps/desktop/src/components/chrome/AgentReadyGate.tsx`（新增）、`apps/desktop/src/styles/globals.css`、`apps/desktop/src/App.tsx`、`apps/desktop/src-tauri/src/commands/router.rs`、`apps/desktop/tests/agentReadyGate.test.tsx`（新增） |

**现象**

Tauri 开窗到 Python Agent(:8765) `/health` 通之间有几秒空窗（打包态要解压依赖 + 加载 onnx 模型，实测可达 30s+）。这段时间界面完全可点，但点哪儿都失败：模型管理/知识库/生成参数面板各自弹「⚠ Agent 未就绪」，运营工作台报 `error sending request`，聊天发消息没反应。用户以为软件坏了就到处点，反而制造更多失败请求与错误横幅。各处只能自己写重试兜底（`envStore` 4 次递增 backoff、`opsCaseStore` 15s 退避、`SubAgentPanel` 10 次×3s、四个设置面板各自 `agentWaitReady(15)`），但界面本身没有任何「现在还不能操作」的信号。

**问题原因**

启动期缺一道**全局**闸门。已有的连通性设施只解决局部问题：① `useAgentHealth` 是状态栏用的 5s 轮询（首探延迟 1.5s、单次探测窗 3s），只把结果写进 `uiStore.agentStatus` 供顶栏/底栏显示文字，不拦任何交互，且最坏要 6.5s 才知道 Agent 起来了；② Rust `agent_wait_ready` 是个阻塞命令，但只有若干设置面板在自己挂载时调它，主界面从不调；③ Rust `agent_manager` spawn 后的 30 轮健康检查只写日志，不通知前端。三者都没有把「未就绪」这个状态提升成全局 UI 门禁。

**根因与修复**

把「Agent 是否就绪」提升为全局闸门状态，未就绪期间整屏模糊 + 禁交互：

① `uiStore` 新增 `agentBootState: 'booting' | 'ready' | 'failed'`（+ `agentBootError` / `agentBootElapsedMs`）与 `setAgentBoot()`。**刻意不列入 `partialize` 白名单** —— 每次启动都必须重新探测，否则上次会话的 `'ready'` 会被 rehydrate 回来，闸门形同虚设。② 新增 `lib/agentBoot.ts`：挂载即 `agentWaitReady(30)` 阻塞等待（30s 与 Rust `agent_manager` spawn 后的健康检查预算对齐，超时正好能在日志里读到「健康检查超时 (30s)」；在飞 Promise 去重，重试连点不并发）；就绪 → `ready` + 顺手把 `agentStatus` 置 `'ready'`（不等 5s 轮询）；超时/异常 → `failed` 并开启 3s 后台复探（Agent 起来后**自动放行**，无需用户点击）。另提供 `restartAgentForGate()`（杀 :8765 占用者再等一轮）、`readAgentLogTail()`、`skipAgentBootGate()`、`stopAgentBootReprobe()`。③ 新增 `components/chrome/AgentReadyGate.tsx`：`body` 挂 `.agent-booting`（用 `useLayoutEffect`，保证首帧绘制前就生效，不闪一帧可点的清晰界面），遮罩卡片走 `createPortal` 挂到 `body` —— 不在 `#root` 里，所以自身不受 `filter` 影响、保持清晰可交互；`booting` 态转圈 + 已用时计时，`failed` 态给出「重试 / 重启 Agent / 查看 eaide.log / 跳过闸门」四个出口。④ `globals.css`：`body.agent-booting #root { filter: blur(6px) saturate(.85); pointer-events: none; user-select: none }` + `body { overflow: hidden }` + 加载环 keyframes。⑤ 键盘也堵：window **capture** 阶段拦下 keydown/keyup（`preventDefault` + `stopPropagation`，先于 WorkspaceLayout 等处的 bubble 监听，命令面板/快捷键都开不了），遮罩内按键放行；并把跑出遮罩的焦点弹回（挂载时 blur 原焦点 + `focusin` 兜底）。⑥ Rust `agent_wait_ready` 改为「先探测再 sleep」（原 `while elapsed < deadline` 是 sleep-first）：Agent 已在跑时立刻返回，否则每次开窗都要白等 500ms 的模糊遮罩。⑦ `App.tsx` 在 ErrorBoundary 内挂 `<AgentReadyGate />`。

**验证**

新增 `tests/agentReadyGate.test.tsx` 11 例全绿：未就绪渲染遮罩 + body 挂类 + 以 30s 阻塞等待；就绪后遮罩消失/类名摘掉/`agentStatus` 转 `'ready'`；首轮超时切错误态并展示四个出口与耗时；点重试重走一轮；点重启先 `agentRestartNow` 再等；点查看日志展示 tail；`invoke` 抛错（非 Tauri 环境）时可用「跳过闸门」逃生；3s 后台复探自动放行；卸载后不再打 IPC（定时器已清）；键盘闸门遮罩外拦下、遮罩内放行、就绪后彻底退出。全量 `pnpm test` 60 文件 / 383 用例全绿，`tsc --noEmit` 与 `eslint src tests --max-warnings=0` 零告警；Rust `cargo clippy --all-targets -- -D warnings` 通过（首轮曾因 `loop` 改写触发 `unused_assignments`，改为不给 `last_err` 初值后消除）；`cargo test` 282 通过 / 3 失败，失败项为 `builtin::tests::test_shell_echo_ok` 等三个本机拉起真实 shell 的用例（本台账早前条目已记录为沙箱环境下既有失败，与 `agent_wait_ready` 无代码路径交集，CI 跑 ubuntu 不受影响）。

**教训**

「服务比界面慢」的启动竞态不能靠每个消费方各自重试来兜 —— 那样界面在空窗期表现为「看着能用、点什么都坏」，用户乱点反而放大故障面。正确做法是把依赖服务的就绪状态提升为**全局门禁**（单一 store 字段 + 单一遮罩组件），一次性阻断交互，就绪瞬间统一放行；门禁态**绝不能持久化**，否则上次会话的成功值会让闸门形同虚设。遮罩自身要 portal 到被模糊子树之外，并连键盘快捷键与焦点逃逸一起堵，只挡鼠标等于没挡。最后务必留逃生口（跳过闸门）+ 自愈路径（后台复探、重启、看日志），任何门禁都不允许把界面永久锁死。


### #192 知识库提问「查到了却不用」：RAG 召回从未接入作答 + 意图误判 + 工具循环跨盘发散扫描卡死

| 字段 | 内容 |
|---|---|
| **是否修复** | 已修复 |
| **修复时间** | 2026-09-04 |
| **发现方式** | 用户反馈：最近一次对话卡住无反应，且「找一下对公转账汇兑的规章制度 / 知识库里没有吗」意图识别不对；用户指出「就算向量分低，BM25 也该查得到，甚至 grep 都能查到」 |
| **涉及文件** | `services/agent/src/agent/llm/prompts.py`、`llm/normalize.py`、`llm/router.py`、`llm/private_llm.py`、`llm/ollama.py`、`llm/local_small.py`、`llm/mock.py`、`graph/nodes/responder.py`、`graph/nodes/decompose.py`、`graph/nodes/rag_retrieve.py`、`graph/semantic_route.py`、`tools/loop.py`、`config.py`；测试 `tests/test_rag_context_injection.py`、`test_decompose_rag_first.py`、`test_rag_retrieve_query_rewrite.py`、`test_semantic_route_kb_lookup.py`、`test_tool_loop_fs_divergence.py`（均新增）、`test_router_backcompat.py` |

**现象**

日志（session `cb19b403` / `bd74d96f`）显示：用户问「找一下对公转账汇兑的规章制度」，`hybrid_search bm25=80 vec=80 fused=118 hits=20` —— 知识库**检索完全成功**，却先弹 A/B/C 澄清不作答；用户追问「知识库里没有吗」后，agent 进入 `TOOL_ONLY` 动态工具循环，用 `shell` 跨盘符 `dir C:\ / D:\ / E:\ / F:\`、`glob **/*.md`、`find *汇兑*`、`list_dir`、`biznav_features` 到处翻找，每轮命令都不同，直到日志末尾（16:37:35）仍在发云端请求、始终不出终答 —— 用户感知为「卡死」。期间用户在 HITL 弹窗点了 `approve_always`，`shell` 会话豁免登记后所有命令自动放行，失去人工刹车。

**问题原因**

三处叠加，根因是第一处：

① **RAG 召回从未接入作答链路（真正根因）**：`rag_retrieve` 节点把召回写进 `state.system_prompt_addon`，但全仓**没有任何读取方** —— `summarise` / `summarise_stream` / `build_summarise_messages` 及各后端（private/ollama/local_small/mock）签名只有 `intent/user_prompt/plan/results/history`，没有 rag 参数；唯一消费 `rag_context` 的是 doc_review 审核，与聊天无关。所以模型作答时**根本看不到知识库内容**，才会「命中 80 条却弹澄清」「跑去 shell 自己翻全盘」。日志里那句 trace「已检索本地知识库，将相关上下文注入提示词」是假的。

② **意图改写把确认追问误判成工具任务**：「知识库里没有吗」语义路由判成 chitchat（0.4854 < 阈值 0.78，未命中）→ 回退云端 LLM，被改写成 `data_query + need_tool=true` → `decompose` 判 `TOOL_ONLY`。旧 `DEFAULT_ROUTES` 没有覆盖这类「知识库确认追问」场景（用户指出的向量识别缺口）。且 `rag_retrieve` 用**原始短句**检索（日志 `query_chars=7 bm25=15`），未用意图改写句，追问轮检索词退化。

③ **工具循环缺发散刹车**：现有重复熔断只拦「连续完全相同」指纹、停滞熔断只拦「零成功」轮；而发散扫描每轮命令都不同、都 `ok=True`，两道熔断都不触发，24 轮预算 + 云端每轮最长 ~100s 往返累计可达十几分钟，且无墙钟超时。

**根因与修复**

① **把 RAG 接进作答**：`prompts.format_rag_block()` 统一包装召回段（附「优先据库作答 + 按编号溯源 + 禁止编造 + 资料不足如实说明、绝不用 shell/dir/glob/find 去别处翻找」纪律）；`summarise`/`summarise_stream`/`build_summarise_messages` 及 private/ollama/local_small/mock 全链新增可选 `rag_context` 参数并注入 prompt；`responder._summarise_maybe_stream` 单点把 `state.system_prompt_addon` 透传进去（覆盖 MAIN_AGENT / 工具结果汇总 / 子智能体 / 闲聊全部路径）；L1 缓存 key 纳入 `rag_context`（同问不同召回不同 key）；native 工具循环系统提示也注入召回（双保险）。`router` 转发时经 `_rag_kwarg()` 做**后端能力探测**（`inspect.signature`），旧后端/测试替身不接受该参数时优雅跳过、不崩（与 `intent_node` 对可选 kwarg 的既有兼容策略一致）。

② **RAG 优先作答**：`decompose._intent_analysis_fast_path` 在 `need_tool→TOOL_ONLY` 前加门控 —— 文档/制度类查询（`intent_category=data_query` 且带 `doc_type` 实体或命中知识库/文档关键词、且未被工具型语义路由 `_route` 命中）且 RAG 已 **BM25 命中**（`chunk.metadata.matched` 非空 ≥ `rag_first_min_matched_hits`）→ 直接走 `MAIN_AGENT` 据召回作答，不进工具循环。判据用 BM25 词元命中而非 reranker 绝对分（cross-encoder logit 量纲不稳），正是回应用户「这种词该查得到」。带开关 `rag_first_answer_enabled`。

③ **检索词优选**：`rag_retrieve` 改用 `rewritten_query` 检索（缺失回退原句）；改写句仍短（<12 字）时 `_augment_followup_query` 拼接上一轮用户主题（会话式查询改写，仅影响检索、无副作用）。

④ **向量意图识别补场景**：`semantic_route.DEFAULT_ROUTES` 新增 `kb_lookup` 路由（「知识库里没有吗 / 库里有没有 / 文档里查不到吗 / 内部资料里有没有…」），零 LLM 直出 `need_tool=False` → 走 RAG 作答不动文件工具；带 hard_negatives（「写个查知识库的脚本 / 清空知识库」）拦截。

⑤ **发散刹车 + 墙钟**：`loop.py` 新增只读文件探测统计（`_is_fs_probe`：`dir/ls/glob/find/list_dir/grep/tree` 及 shell 内同类命令，不含 `read_file` 以免误伤 coding 任务）；累计达软阈 `tool_loop_fs_probe_soft_limit`(6) 注入强制收敛指令，达硬阈 `tool_loop_fs_probe_hard_limit`(12) 直接停并给出「去知识库/告知文档位置」的可操作终答；另加单 run 墙钟超时 `tool_loop_wall_clock_sec`(240s)。

**验证**

新增 5 个测试文件共 30+ 例全绿：`format_rag_block` 空/非空与纪律、ollama/private/`build_summarise_messages` 注入与空则不注入、responder 透传 `system_prompt_addon`、缓存 key 对 `rag_context` 敏感；`decompose` RAG 优先命中走 MAIN_AGENT + 关开关/无 matched/无 rag_context/DB 查询/`_route` 命中/阈值提高六种不误伤；`rag_retrieve` 用改写句 + 短追问拼上轮主题；`kb_lookup` 路由定义/命中/负样本拦截/`intent_node` 零 LLM；`_is_fs_probe` 单元 + 发散硬停/软阈收敛指令集成。`test_router_backcompat` 签名冻结表按「新增可选参数合法」既有约定登记 `rag_context`。全量 `uv run pytest` 通过（仅 1 既有 skip，无 failed）；`ruff check`/`ruff format --check` 全绿。

**教训**

「检索命中」不等于「模型看到了检索结果」—— RAG 的价值全在召回**是否真的进了作答 prompt**，只写 state 不接线等于没做，且 trace 里「已注入提示词」这类自我描述一旦与真实数据流脱节就是排障黑洞，必须以「谁读取了这个字段」为准。判「知识库能否作答」要用 BM25 词面命中这种量纲稳定的强信号，别依赖 cross-encoder logit 的绝对值。工具循环的死循环不止「原地打转」一种形态，「每轮都换目标的发散扫描」同样烧光预算，熔断要覆盖「有效动作的无界发散」，并配墙钟超时兜住云端长往返。用户点了 `approve_always` 后人工闸门失效，代码层刹车是唯一防线。


### #193 macOS 上「找不到日志文件」+ 文档审核报 LLM 未配置 —— .app 包只读致 Rust 侧全量静默写失败；新增设置页「一键导出全部日志」

| 字段 | 内容 |
|---|---|
| **是否修复** | 已修复 |
| **修复时间** | 2026-09-04 |
| **发现方式** | 用户反馈：macOS 版文档审核报「分析失败：分类输出解析失败: doc_review generate_review failed: cloud: not configured; private: not configured」，且按 Windows 路径找不到任何日志文件 |
| **涉及文件** | `apps/desktop/src-tauri/src/agent_manager.rs`、`apps/desktop/src-tauri/src/commands/logs.rs`（新增）、`commands/mod.rs`、`src/lib.rs`、`Cargo.toml`；前端 `src/ipc/invoke.ts`、`src/views/settings/AboutSettingPanel.tsx`；测试 `apps/desktop/tests/aboutLogExport.test.tsx`（新增）、`src-tauri` 内 `logs.rs` 单测 |

**现象**

macOS 上两个症状同源：① 文档审核/聊天等一切走 LLM 的功能报 `cloud: not configured; private: not configured`（`router.py generate_review` 降级链里 cloud/private 两个 builder 都返 None）；② 用户按 About 页写的 `%LOCALAPPDATA%\Enterprise AI IDE\logs\` 去找日志，macOS 上根本不存在该路径，且真实日志也没落盘——「没找到日志文件」。

**问题原因**

① **Rust 数据根落在只读 .app 包内（根因）**：`agent_manager::get_app_data_dir()` 优先返回 `current_exe().parent()`，macOS 上即 `/Applications/EAIDE.app/Contents/MacOS/`。已签名 .app 包在 Gatekeeper 校验后**只读**，`create_dir_all` / `File::create` 全部失败，而代码里所有 IO 错误都被 `let _ =` 静默吞掉 → Rust 侧 `logs/eaide.log`、`logs/crash.log`、`audit.sqlite`、`systems.yaml`、`compile.json`、`config/llm-config.json` **一个都写不进去**，用户自然「找不到日志」。Python 侧因 `agent_runtime_dir()` 早已为 macOS 重定向到 `~/Library/Application Support/eaide/`（子进程 cwd），故 `agent.log`/`cot.log`/`router.db` 能写——两路数据根**不一致**，Rust 与 Python 各写各的。

② **LLM 未配置是独立的用户侧配置问题（非代码 bug）**：`generate_review` 只认 `router.db.llm_backends` 里 `enabled=1` 的 cloud/private 后端，不回退环境变量。macOS 全新安装时该表为空（Windows 上配好的 router.db 不跨平台迁移），故报 not configured。此点通过「设置→模型管理添加并启用后端」解决，代码侧无需改；但「找不到日志」让用户无法自助排查，故一并补导出能力。

**根因与修复**

① **macOS 数据根对齐**：`get_app_data_dir()` 增加 `#[cfg(target_os="macos")]` 分支，强制返回 `~/Library/Application Support/eaide/`（与 `agent_runtime_dir()` 完全一致），不再走 exe 父目录。修复后 Rust 与 Python 共用同一数据根，日志/数据库/配置集中可查；Windows/Linux 行为不变。

② **新增「一键导出全部日志」**：新 Tauri command `export_all_logs(dest_path)`（`commands/logs.rs`），收集 `<data>/logs/` 下 `eaide.log`/`crash.log`（rust/）、`agent.log`/`cot.log`/`orchestrator-*.jsonl`（python/）、数据根散落 `*.log`（other/），用 `zip` crate（新增依赖，deflate）打包到用户 `save()` 选定路径；zip 内附 `MANIFEST.txt` 记录每个来源的 OK/MISSING/错误原因 + 平台 + 数据根，便于支持人员定位缺失环节；单文件读失败不阻断整体（best-effort）。**不**导出 `environments.json`/`llm-config.json` 等可能含密钥的配置。zip 创建放 `spawn_blocking` 避免卡 async runtime。

③ **About 页平台感知 + 导出入口**：`AboutSettingPanel` 用 `@tauri-apps/plugin-os` 的 `platform()` 动态展示 macOS/Linux/Windows 三套真实日志路径（替换原硬编码 Windows 路径），并在「日志位置」标题旁加「📦 一键导出全部日志」按钮：`save()` 拿路径 → `ipc.exportAllLogs()` → 成功/失败内联提示（含文件数与大小）。

**验证**

Rust：`cargo check --lib` 零错误零警告；`cargo test --lib logs::` 1 例通过（真实建 zip + 回读校验含 MANIFEST.txt）。前端：新增 `tests/aboutLogExport.test.tsx` 6 例全绿（渲染按钮/三平台路径/点击导出参数/成功提示/失败提示/取消不触发）；全量 `pnpm test`（vitest）61 文件 / 389 用例全绿；`tsc --noEmit` 无错。macOS 实机需回归：启动后 `~/Library/Application Support/eaide/logs/` 应出现 eaide.log；设置→About→一键导出应产出含 rust/+python/ 的 zip。

**教训**

「静默吞 IO 错误（`let _ =`）+ 只读安装目录」是 macOS 打包应用的经典组合坑：功能看似正常（进程起来了、UI 能渲染），但所有持久化副作用全部丢失，且无任何报错，用户与开发者都无从排查。凡「写安装目录」的逻辑必须按平台区分可写性（macOS .app 包 / Windows Program Files 均可能只读），统一重定向到用户数据目录；且**日志路径必须与子进程 cwd 对齐**，否则两路日志散落、排障成本翻倍。给用户一个「一键导出全部日志 + MANIFEST 清单」的自助入口，比让支持人员远程猜路径高效得多。



---

### #194 安装包整屏「Unexpected Application Error! 404 Not Found」—— 窗口 url 带 index.html 后缀，react-router 无匹配路由

| 字段 | 内容 |
|---|---|
| **是否修复** | 已修复 |
| **修复时间** | 2026-11-04 |
| **发现方式** | 用户反馈：v2.141（前端 dist 热替换接线）全量重打包安装后，主窗口整屏显示「Unexpected Application Error! / 404 Not Found」；覆盖重装、重新打包均复现 |
| **涉及文件** | `apps/desktop/src/router.tsx`、`apps/desktop/src-tauri/tauri.conf.json`、`apps/desktop/src-tauri/src/lib.rs`（注释）、`apps/desktop/src-tauri/src/web_loader.rs`（注释）；测试 `apps/desktop/tests/routerEntryFallback.test.ts`（新增）、`apps/desktop/tests/frontendDiskLoader.test.ts`（护栏期望更新） |

**现象**

后端完全正常（eaide.log 里 Agent spawn、/health 200、executor ready 一条龙），但窗口整屏只有两行裸文本「Unexpected Application Error!」+ 斜体「404 Not Found」。重装安装包、清理手工覆盖的 web/ 后依旧复现——说明不是资产损坏，而是必现逻辑 bug。

**问题原因**

「Unexpected Application Error!」是 react-router `createBrowserRouter` 未配置 errorElement 时的**默认错误页**，404 即内部 `ErrorResponse(404, "Not Found")`——唯一来源是「当前 URL 没有任何路由匹配」。

v2.141 把主窗口 url 配成 `eaide://localhost/index.html`（CustomProtocol 的 path 原样透传，不会被改写成 `/`），于是 WebView 里 `location.pathname === '/index.html'`。而 router.tsx 的首页是 `{ index: true }` 路由，**只匹配精确 `/`**，路由表没有 `index.html` 条目也没有通配兜底 → 入口即 404。dev 模式（`pnpm tauri dev`）url 走 devUrl 根路径 `/`，所以开发时从未暴露。

**根因与修复**

① `tauri.conf.json` 窗口 url 改为 `eaide://localhost/`：path=/，web_loader `normalize_rel` 空路径归一化为 index.html（该行为已有 Rust 单测 factory_web_used_when_no_active 覆盖），磁盘热替换链路不变。

② `router.tsx` 根路由 children 末尾新增 `{ path: '*', element: <HomeView /> }` 通配兜底：即使入口 URL 带 `/index.html` 后缀（老安装包、外部唤起等场景）也渲染首页，未知 SPA 深链不再整屏 404。同步导出 `routes` 供契约测试做 `matchRoutes`。

③ 护栏与契约测试：`frontendDiskLoader.test.ts` 的 url 断言更新为 `eaide://localhost/`（它本次确实拦下了改动，防止两处失配）；新增 `routerEntryFallback.test.ts` 3 例——先临时摘掉通配路由复现失败（2 failed），恢复后全绿。

**验证**

`pnpm vitest run` 63 文件 / 403 例全绿；`tsc --noEmit`、改动文件 eslint 零告警；`cargo clippy --all-targets` 通过；`cargo test --lib web_loader` 7 例全绿。**真机验收需重新 `pnpm tauri build` 并安装**（用户自行执行）。

**教训**

自定义协议接管窗口加载时，URL 的 **path 部分会原样成为 WebView 的 pathname**——入口必须给「路由根」（`/`），不要把具体文件名写进窗口 url；同时 SPA 路由表要有 `path: '*'` 兜底，否则任何入口路径偏差都是一整屏默认 404 错误页。dev 模式与打包态的 URL 形态不同（devUrl vs CustomProtocol），此类问题只有安装包能暴露，护栏测试应同时锁「协议 url」与「路由可匹配性」两端。



---

### #195 交付草稿切页签就弹全屏 —— 全屏状态存在草稿卡内 + `lastDirectDraftId` 全程不清

| 字段 | 内容 |
|---|---|
| **是否修复** | 已修复 |
| **修复时间** | 2026-11-05 |
| **发现方式** | 用户反馈：交付草稿区有两份草稿（资料预审意见书 / 资料预审信息及补件申请表），在页签间来回切换时，切到「资料预审意见书」就整屏弹全屏表单 |
| **涉及文件** | `apps/desktop/src/components/ops/ExpertWorkflowPanel.tsx`；测试 `apps/desktop/tests/expertWorkflow.test.tsx`（新增回归用例） |

**现象**

草稿区里只是点页签切换「看另一份草稿」，但切到曾「点交付物直开」过的那份草稿时，草稿卡自动变成 `fixed inset-0 z-40` 的全屏填写页，遮住整个工作区；退出全屏后再切走、切回，依旧再次全屏。

**问题原因**

两个缺陷叠加：

① `DraftFormCard` 内部 `useState(initialExpanded)` 只认首次挂载的 prop；而页签切换时父级用 `key={activeDraft.id}` 强制重新挂载，于是每次切回都重新按 prop 初始化 → 又开全屏。

② 「直开草稿」标记 `opsCaseStore.lastDirectDraftId` 只在 `loadCase`/`reset` 时清空，同一业务会话内**一直保留**；父级把它写成 `initialExpanded={activeDraft.id === lastDirectDraftId}`，于是这份草稿在整个会话里都被判定为「该弹全屏」。

**根因与修复**

把「是否全屏」从草稿卡内部状态提升到面板层，按草稿 id 记账（`expandedDraftId`）：

① 面板新增 `expandedDraftId` + `autoExpandedDraftRef`：监听 `lastDirectDraftId`，**首次**见到某 id 才 `setExpandedDraftId(id)` 一次（消费即标记），之后页签来回切换只改 `activeDraftId`，不再改全屏状态 → 直开草稿的「弹出表单」感知保留，切页签回来保持紧凑卡。

② `DraftFormCard` 的 `expanded` 改为受控 prop，`initialExpanded` 删除：`expanded={activeDraft.id === expandedDraftId}` + `onExpandedChange={(v) => setExpandedDraftId(v ? activeDraft.id : "")}`，卡片内「⛶ 全屏」「✕ 退出全屏」两个按钮改为回调。

**验证**

新增回归用例「直开草稿只自动全屏一次」：两份草稿 + `lastDirectDraftId=DR-A` → 首屏仍自动全屏；退出后切到 DR-B 再切回 DR-A，断言无「✕ 退出全屏」、只有「⛶ 全屏」。`pnpm vitest run tests/expertWorkflow.test.tsx` 21 例全绿；`tsc --noEmit`、改动文件 `eslint` 零告警。真机验证需用户自行 `pnpm tauri build`。

**教训**

弹层/全屏这类「一次性提示型」UI 状态，不能靠 `useState(prop)` + `key` 重挂载来表达——重挂载会把「已经关过了」这件事忘掉；一次性意图（直开草稿、新消息滚底等）必须显式消费（ref/标记位清标），且状态要存在不会被重挂载重置的父层或 store 里。同时「最近一次直开草稿」这类标记位要回答「它何时失效」，否则会长期误命中。

---

### #196 「重启 Agent」只杀不启 —— 点完按钮后 Agent 永远不再起来，界面一直未连接

| 字段 | 内容 |
|---|---|
| **是否修复** | 已修复 |
| **修复时间** | 2026-11-06 |
| **发现方式** | 用户反馈：在「知识库 / RAG 参数」页点「重启 Agent 使新参数生效」后一直未连接，日志目录 `C:\Users\79834\AppData\Local\Enterprise AI IDE\logs` |
| **涉及文件** | `apps/desktop/src-tauri/src/commands/router.rs`；`apps/desktop/src-tauri/src/commands/agent.rs`；`apps/desktop/src-tauri/src/commands/llm.rs`；`apps/desktop/src-tauri/src/agent_manager.rs`；`apps/desktop/src/ipc/invoke.ts`；`apps/desktop/src/views/settings/KnowledgeRagPanel.tsx`；`apps/desktop/src/views/settings/ModelManagementPanel.tsx`；`apps/desktop/src/lib/agentBoot.ts` |

**现象**

点「重启 Agent」后 Agent 进程消失，状态栏始终「未连接」，再点多少次都没用，只能退出重开 EAIDE。桌面端进程仍存活（`eaide-desktop`），8765 端口无人监听（只剩桌面端 SYN_SENT）。

日志特征极其明确：`eaide.log` 停在

    [agent_manager] 端口 127.0.0.1:8765 仍被 PID=28148 占用，强杀
    [agent_manager] kill_agent_process_tree: 兜底 taskkill /IM eaide-agent.exe

之后**再无任何 `准备启动新 Agent` / `新 Agent 已 spawn` 记录**；`agent.log` 也停在被杀前的最后一条。

**问题原因**

`agent_restart_now` 命令（`commands/router.rs`）只「杀」不「启」：

    crate::agent_manager::kill_agent_process_tree();
    // 等待端口释放 …… 然后就没有然后了
    Ok(json!({"ok": true, "port_freed": ...}))

其原注释写的是「杀掉 :8765 占用者后等下一次 spawn 自动起」，但**请求侧根本没有懒启动逻辑**：`AgentManager::start()` 只在桌面端 setup 时调用一次，HTTP 请求失败就是失败，不会触发 spawn。于是「下一次 spawn」永远不来，Agent 被杀后无人拉起。

附带发现两个同源缺陷：

① `restart_agent_process()`（真正会 spawn 的那个）只认打包版 exe（`resolve_agent_exe`），解析不到直接 `return` —— 开发模式（uv run）下点重启同样会变成「只杀不启」。

② `restart_agent_process()` 注入的 env 缺 `EAIDE_HOST` / `EAIDE_PORT` / `EAIDE_LOG_LEVEL`，与首次启动 `AgentManager::start` 的注入清单不一致 —— 改过端口的环境下，重启后 Agent 可能监听在另一个端口。

③ `agent_restart`（`commands/agent.rs`）与 `agent_restart_now` 是两个同义但实现不同的命令：前者真重启、被 `llm_set_config` 内部调用但不等就绪；后者只杀、被前端三个入口调用。前端按钮实际点到的恰恰是那个残缺实现。

**根因与修复**

① `commands/router.rs` 抽出公共函数 `restart_agent_and_wait(state)`，闭环四步：kill 端口占用者 → `spawn_blocking(restart_agent_process)` 真 spawn → 等端口重新监听（最多 30s）→ 等 `/health` 2xx（最多 30s），返回 `{ok, restarted, port_freed, port_up, ready, health}`。

② `agent_restart_now` 与 `agent_restart` 全部改为委托 `restart_agent_and_wait`，消除两处实现漂移；`agent_restart` 由同步 fn 改 async，`llm_set_config` 相应 `await` —— 保存 LLM 配置后等 Agent 就绪再返回，避免下一个请求撞上未就绪进程。

③ `agent_manager.rs::restart_agent_process()` 返回 `bool`（是否成功 spawn），并补齐解析链：打包版 exe 解析不到时回退开发模式（`uv run uvicorn agent.main:app`，与 `AgentManager::start` 同策略）；env 集合补齐 `EAIDE_HOST` / `EAIDE_PORT` / `EAIDE_LOG_LEVEL` 与首次启动对齐；同步等待由 `std::thread::sleep` 改为 async `tokio::time::sleep`（原实现在 async command 里阻塞 runtime worker）。

④ 前端三处调用点文案与判定改为「真重启 + 就绪结果」：成功显示「Agent 已重启并就绪」，30s 未就绪显示「未就绪（详见 eaide.log）」，不再谎报「下次请求会触发新 Agent 启动」。

**验证**

`cargo check` 通过；`cargo clippy --all-targets -- -D warnings` 零告警通过；前端 `tsc --noEmit` 通过；改动文件 `eslint` 零告警。真机验证需用户自行 `pnpm tauri build`：点「重启 Agent」后 `eaide.log` 应出现 `[agent_manager] restart: 新 Agent 已 spawn (pid=…)` 与 `Agent 就绪 (x.xs) — http://127.0.0.1:8765/health 返回 2xx`，状态栏恢复「已连接」。

**临时恢复手段（未打包前）**

退出并重新打开 EAIDE —— 桌面端启动时 `AgentManager::start()` 会 kill + spawn Agent，连接即恢复。

**教训**

「重启」类命令必须闭环：kill 之后要有明确的 spawn 路径，不能依赖「下次请求会懒启动」这种并不存在的隐式约定 —— 命令注释里的假设若没有对应代码，就是定时炸弹。同义命令长期分叉（一个真重启、一个只杀）且恰好被前端调到残缺的那个，最终表现为「按钮点了没用」；对外暴露的重启入口应统一到一个实现，并且返回值必须携带「是否真的就绪」，让前端能如实报错而不是自我安慰。诊断这类「进程消失」问题先看两侧日志的**缺口**：杀进程的记录在、spawn 的记录不在，比任何断言都有力。

### #197 知识库面板偶发「读取知识库失败 · knowledge.read_body: error decoding response body」

**现象**

批量导入法规文档期间，知识库面板顶部偶发红字「读取知识库失败 · config: knowledge.read_body: error decoding response body」。日志里对应 `/knowledge/v1/docs`、`/knowledge/v1/status` 都拿到了 `200 OK`，但前端仍报错——说明状态码到了、body 没读全。

**根因**

三层叠加把 Agent 单 worker 打满，导致个别 GET 的连接在响应体传完前被中断，reqwest `resp.text()` 抛 `is_decode`/`is_body` 类错误：

① 导入高峰期 MiniMax 云端模型成片 429（Token Plan 速率限制）+ 退避重试，后台入库任务无并发上限地全部并发；
② 前端 `KbPanel` 多个 `useEffect` 各挂 1.5s `setInterval` 轮询，且无在飞闸门——慢响应下定时器叠加未完成请求，实测放大到 26 req/s，反过来加剧 worker 拥塞；
③ Rust 转发命令 `knowledge.rs` 对 `read_body` 无重试，一次截断即冒泡成用户可见错误（`doc_review.rs` / `audit_expert.rs` 同模式）。

（reranker / ONNX embedding 本身已 `asyncio.to_thread` 卸载，不阻塞事件循环，非本次元凶。）

**修复**

① 新增 `commands/agent_http.rs::send_json(err_prefix, max_attempts, make)`：封装「发送 → 读 body → 校验状态 → 解析 JSON」，对**快速失败的瞬时错误**（`is_connect`/`is_request`/`is_body`/`is_decode`）做有界线性退避重试；刻意排除 `is_timeout`，避免长超时 op（doc_review get/findings 90s）因真超时重试放大到分钟级。`make` 每次返回全新 RequestBuilder（builder 被 `send` 消费，重试须重建）。
② `knowledge.rs` / `doc_review.rs` / `audit_expert.rs` 三处改用 `send_json`：**幂等 GET 重试 3 次，POST/DELETE 单次**——审批类 HITL 写操作绝不重试，杜绝重复副作用。
③ 前端 `KbPanel.refresh` 加 `inflightRef` 在飞闸门（重入直接丢弃，掐掉请求风暴放大），并区分 `silent`：后台轮询遇瞬时错误静默跳过、保留上次数据、下一轮自动重试，仅用户主动/初始化刷新才提示红字；成功后 `setError(null)` 让偶发抖动横幅自愈。
④ 后端入库并发闸门 `rag_ingest_concurrency`（默认 2）+ 分类预判的并发/分批/429 退避由本次功能分支一并落地，从源头削减 worker 过载。

**验证**

`cargo check` 通过；`cargo test --lib` 中 `commands::agent_http` 及 knowledge/doc_review/audit_expert 相关用例全绿（仅 `builtin::tests::test_shell_*` 因本机 shell 环境失败，与本改动无关）；前端 `pnpm vitest run tests/kbPanel.test.tsx` 21 用例全过。真机验证由用户自行 `pnpm tauri build`：批量导入期间知识库面板不应再出现 read_body 红字，`eaide.log` 里偶发截断会被重试吞掉。

**教训**

「转发型」命令把后端 HTTP 细节一比一映射成用户可见错误时，任何瞬时连接抖动都会穿透成红字。对幂等 GET 加有界重试是廉价且安全的兜底，但边界必须划清：写操作/审批永不自动重试。前端轮询一定要带在飞闸门，否则 `setInterval` 在慢响应下不是「保持节奏」而是「无上限叠加」，自己就成了压垮后端的那根稻草——排查这类问题要同时看后端**过载源**（429/并发）与前端**请求放大**（轮询叠加），二者常互为因果。

### #198 知识库上传后一直在「索引中」——限流加错层级，批量导入退化成排队长龙

**现象**

批量导入 45 个法规文档后，43 个长期停在「索引中」，只有 2 个 ready；单文档耗时 30~140 秒。界面看不到任何进度感，怀疑是卡死。

**根因（两层叠加，都不是「精度必需的代价」）**

① **限流加错层级**：为修 #197 的 429，新增的 `rag_ingest_concurrency`（默认 2）把**文档级**并发压到 2 路。而 429 的真实来源是「入库期每父块一次云端 LLM 调用」，不是「文档数量」。单文档要串行等 父块数×2 次云端往返（每父块：上下文前缀 + 实体抽取），45 个文档排 23 批 → 墙钟 20~40 分钟。对照组：13:41 那批 20 个文档 2.5 分钟跑完（那时 6 个 RAG 开关同样全开，唯一变量就是并发闸门）。

② **界面语义误导**：上传瞬间就写 `status="indexing"`，只有 ingest 完成才改 ready/failed → **排队等闸门的文档也显示「索引中」**，用户无法区分「在排队」与「真在索引」。

③ **无自愈路径**：入库是后台任务，进程重启/崩溃后任务不恢复，但状态永远停在 indexing → 永久僵尸记录，界面一直显示「索引中」且无任何自愈。

**修复（把闸门从文档级下移到 LLM 调用级）**

① 新增**全局 LLM 调用闸门** `rag_llm_concurrency`（默认 8，可热调 1~32）：`ingestion._call_llm()` 统一包裹「自动分类 / 上下文前缀 / 实体抽取」三类调用（含导入前预判路径），限制同时在飞的云端请求数。取值 8 的依据：实测 9 路并发仍返回 200，30+ 路才成片 429。
② **文档级闸门放开到 8**（`rag_ingest_concurrency`）：文档级只管解析/ONNX 向量化的 CPU/内存并行度，不再承担云端限流职责。
③ **父块级并行**：上下文前缀与实体抽取的父块循环由串行改 `asyncio.gather`（共享全局闸门 → 云端并发仍受控），单文档不再串行等 N 次往返。
④ **状态语义**：新增 `queued` 态——上传建档为「排队中」，拿到并发槽位后由 `ingest_file` 翻为「索引中」，完成后 ready；前端 STATUS_META 增「排队中」标签，两处轮询判定纳入 queued。
⑤ **启动自愈**：`storage.reset_orphaned_indexing_docs()` 把残留的 indexing/queued 置 failed（附「上次索引被中断，请重新导入」）；Agent 启动时调用，不再产生永久僵尸。

**验证**

`ruff check` 全过；`mypy --strict` 在改动文件零新增错误；`pytest services/agent/tests` 全量 EXITCODE=0；新增 `test_knowledge_llm_concurrency.py` 6 个用例（闸门上限 / 分类与前缀走闸门 / 父块并行 / 自愈幂等）；前端 `vitest tests/kbPanel.test.tsx` 21 通过、`tsc --noEmit` 与 `eslint` 零告警。真机验证由用户自行打包：批量导入应在数分钟内完成，列表可见「排队中 → 索引中 → 就绪」推进。

**教训**

限流要先问「被限的资源到底是谁」：429 是**云端 API 的速率约束**，对应的是「同时在飞的请求数」，而不是「同时在跑的任务数」。把调用级约束错加到任务级，会用吞吐换限流效果——批量场景下就是排队长龙。更根本的是：**限流不能消除等待，等待就得能被看见**——状态语义（排队 vs 执行中）和中断自愈（僵尸态回收）是限流机制的必要配套，否则用户只能对着「索引中」干等。另有一条通用教训：父子/分块级循环里的远程调用默认是串行的，块数一多就线性放大墙钟时间；只要这些调用受一个全局信号量约束，就可以放心并行化。

---

### #199 点「原文链接」后审核页顶部页签（审批工作台 / 文档审核 / 知识库）整行消失

| 字段 | 内容 |
|---|---|
| **是否修复** | 已修复 |
| **修复时间** | 2026-11-06 |

**现象**

审核模式 → 文档审核页，点风险卡里的原文定位（「第 X 页 第 Y 行」/「定位原文高亮」）或点正文里的高亮块后，
顶部那行页签（审批工作台 / 文档审核 / 知识库）整行不见了，三栏内容顶到 TopBar 下沿；刷新后恢复。

**根因（两层叠加）**

① **布局溢出**：`AuditDashboard` 根是 `flex h-full flex-col`，页签行是第一个子项，三个视图（`DocReviewDashboard` /
`KbPanel` / `AuditApprovalDashboard`）根部都是 `h-full` 且**直接平铺**成第二个子项 → 内容总高 = 容器高 + 页签行高（约 33px），
多出的那截被外层 `section.overflow-hidden` 裁掉，平时看不见。

② **scrollIntoView 会滚裁剪容器**：`overflow: hidden` 只挡鼠标滚轮，挡不住编程滚动 —— 浏览器仍把它当 scroll container。
定位跳转原实现是 `el.scrollIntoView({ block: "center" })`，它顺带滚动**所有**可滚动祖先：外层那个裁剪容器被滚到 `scrollTop = 33`，
正好把顶部页签行卷出可视区。所以现象只在「点了定位」之后出现，且内容同时上移 33px。

**修复**

① `AuditDashboard`：根加 `overflow-hidden`，页签行下方的分支包进 `<div className="min-h-0 flex-1 overflow-hidden">`，
高度归位后不再有 33px 溢出（页签行不再可能被滚走）。
② 新增 `apps/desktop/src/lib/scroll.ts`（`scrollIntoContainer` / `scrollWithin`）：定位只滚目标面板**自己的**滚动容器。
原文视图内容区标 `data-doc-scroll`，风险卡列表标 `data-findings-scroll`；`jumpToBlock`、`scrollToFindingCard`、
`docReviewStore.selectFinding` 三处 `scrollIntoView` 全部改走它（找不到容器才回退原行为；jsdom 无 `scrollBy` 时退化为改 `scrollTop`）。

**验证**

`vitest` 跑 `docReviewDashboard / docReviewStore / findingsPanel / kbPanel` 共 4 文件 32 用例全绿（新增回归用例：
`jumpToBlock` 在存在 `data-doc-scroll` 容器时不得调用 `scrollIntoView`）；`tsc --noEmit` 零错误。
真机验证由用户自行打包：文档审核页点任意「原文位置 / 定位原文高亮 / 正文高亮块」后页签应保持可见。

**教训**

「overflow: hidden = 不可滚动」是错觉：它只是对用户隐藏滚动条与滚轮，编程滚动照样生效。所以面板内做定位，
一律滚该面板自己的滚动容器，不要用 `scrollIntoView` 这种“顺带滚所有祖先”的 API —— 否则相邻固定区域（页签、工具栏）
会被静默卷走，且只在特定交互后复现。另一条：flex 容器里给子视图写 `h-full` 时必须同时给它 `flex-1 min-h-0`，
否则它按“容器 100%”算高度，和兄弟行一起就必然溢出。

---

### #200 知识库页点「设置」再关闭，回来被踢回「审批工作台」

| 字段 | 内容 |
|---|---|
| **是否修复** | 已修复 |
| **修复时间** | 2026-11-06 |

**现象**

审核模式 → 知识库页签 → 点右上「设置」（RAG 参数）→ 关闭设置回到主界面，页签变成「审批工作台」，知识库列表/筛选全没了，用户需重新点回去。

**根因**

`WorkspaceLayout` 用 `settingsOpen = location.pathname.startsWith('/settings')` 分支渲染设置页（`<Outlet />`），
所以进设置会把 `AuditDashboard` **整体卸载**；而页签状态原先是 `useState("approval")`，组件重建即回默认值。

**修复**

页签状态提升到 `uiStore`：新增 `auditTab`（`'approval' | 'doc' | 'kb'`）+ `setAuditTab`，
并纳入 `eaide.ui` 持久化（`partialize` + `onRehydrateStorage` 合法性校验，旧 localStorage 无此字段时回默认 `approval`）；
`AuditDashboard` 改从 store 读写页签，不再持有本地 state。

**验证**

新增 `tests/auditDashboard.test.tsx` 2 用例：① 卸载（模拟进设置页）后重新挂载仍在「知识库」；② 点页签写入 uiStore。
连同 `docReviewDashboard / docReviewStore / findingsPanel / kbPanel / settingsRoutes / routerEntryFallback / knowledgeRagPanel`
共 8 文件 41 用例全绿；`tsc --noEmit` 与 `eslint` 零告警。

**教训**

只要存在「跨路由分支卸载重挂载」的布局（设置页/Diff 页/全屏页走 Outlet 或条件分支），页面级视图状态就不能放组件 useState——
组件一卸载就回到默认值，用户看到的是「我明明选过」。判断标准很简单：这个状态在“离开再回来”之后是否应该保持？
应该保持的一律提升到 store。

---

### #201 文件预览里 Markdown 的 YAML 头被当正文渲染，URL 还被路径胶囊截断

| 字段 | 内容 |
|---|---|
| **是否修复** | 已修复 |
| **修复时间** | 2026-11-09 |

**现象**

审核模式 → 知识库 → 点法规文档「预览」，文档顶部出现一大段挤在一起的原始 YAML：

```
title: 防范和处置非法集资条例 LinkTitle: 防范和处置非法集资条例（2021）  author: 国务院 …
categories:
• 行政法规
tags:
• 国务院
• 有效
urls:
• htt  [detail?id=ff808081777d07c50177b8d8670038d4]
```

最后一行尤其离谱：URL 被截成「htt」+ 一个蓝色「文件路径」胶囊，胶囊里是 `detail?id=…` —— 点开只会让系统去找一个叫 `detail?id=…` 的文件。

**根因**

两处叠加：

1. 预览面板的 md 分支直接用通用 Markdown 渲染器（`Markdown`）渲染全文，而渲染器不认识 frontmatter ——
   `---` 被当成水平线，YAML 键值被当成段落，`- xxx` 被当成裸列表。
2. `FilePathChip` 的全局路径正则 `FILE_PATH_RE` 里，盘符分支是 `[A-Za-z]:[\\/]`。
   URL `https://…` 里的 `s:/` 正好命中这个分支，于是从 `s:` 开始的一段被当成 Windows 路径，
   交给 FilePathChip 渲染（胶囊只显示末段文件名）→ 只剩「htt」留在正文里。

**修复**

- 新增 `apps/desktop/src/lib/frontmatter.ts`：零依赖 YAML-lite 解析器（标量 / 行内数组 / 块数组 /
  引号 / 注释 / CRLF 归一 / 未闭合围栏兜底）+ `fmText` / `fmItems` / `fmValue`（键名大小写不敏感，兼容 `LinkTitle`）。
- 新增 `apps/desktop/src/components/office/DocMetaCard.tsx`：frontmatter → 元数据卡
  （标题 + 效力状态徽标 + 制定机关/法规层级 + 发布/施行日期 + 分类/标签胶囊 + 未识别字段兜底网格），右上角可收起/展开；
  `urls` 外链字段直接不展示（文档都在本地，外链对用户无意义）。
- 新增 `apps/desktop/src/components/office/MarkdownDoc.tsx`：解析 → 渲染元数据卡 + 正文
  （正文首行与 frontmatter 标题重复时自动去重）；`OfficePreviewPanel` 的 md 分支改用它（无 frontmatter 的文档渲染不变）。
- `apps/desktop/src/components/chat/FilePathChip.tsx`：`FILE_PATH_RE` 加前置否定回顾 `(?<![A-Za-z0-9_])` ——
  路径前不能紧跟字母/数字/下划线，`C:\…` 仍能命中，`https://` 不再误判。

**验证**

新增 `tests/frontmatter.test.ts` 12 例、`tests/markdownDoc.test.tsx` 8 例，
`tests/markdownPathLink.test.tsx` 补 URL 回归 1 例；全量 `pnpm vitest run --no-file-parallelism`
68 文件 / 463 例全绿；`npx tsc --noEmit` 与 `npx eslint --max-warnings=0` 零告警。

**教训**

两条。其一：Markdown 渲染器遇到「文档级结构」（frontmatter）要显式识别，而不是让它掉进正文兜底分支 ——
掉进兜底不会报错，只会难看。其二：正则里的「单字母 + 冒号 + 斜杠」这种宽松写法，要先想清楚会不会命中 `http://`；
凡是「前面紧邻一个字母」的匹配，都该加否定回顾把 URL 排除掉。


### #202 交付草稿审核一直转圈 —— 审核状态永久停在 reviewing 且无自愈入口

| 字段 | 内容 |
|---|---|
| **是否修复** | 已修复 |
| **修复时间** | 2026-11-09 |

**现象**

运营模式 → 交付草稿「业务办理路径单」提交后，右侧抽屉一直显示「业务办理引导专家正在审核草稿」转圈，
专家卡上同一份材料也一直挂「⏳ 审核中」；关闭应用重开仍然转圈，既不报错也不出结果。

**根因**

查 `ops.db`（安装目录）确认材料行 `CF-20261106180551-bd92` 的 `status` 停在 `reviewing`，
`updated_at` 停留在 2026-11-06 18:05:51 —— 即提交那一刻，此后**再没有任何更新**。

对照 `logs/eaide.log`：18:05:51 有 `PUT /ops/case/drafts/DR-...` 200，但**没有**对应的
`POST /ops/case/drafts/DR-.../submit` 记录；紧接着 18:08:35 `stop_agent_process: 终止 Agent`。
即：**提交请求发出后，Agent 被重启/关闭，请求被硬断在审核中途**。

三处设计缺口叠加，导致状态永远卡在 `reviewing`：

1. `ops/api.py` 的 `_do_review_file` 形态是「先写 `status=reviewing` → 再调长耗时外部 LLM」。
   LLM 不可用时有 `RuntimeError` 分支回落到 `pending`，但**超时 / CancelledError / 其它未预期异常
   没有兜底** —— 请求一断，材料永远停在 `reviewing`。
2. **没有僵尸状态自愈**。同类问题在知识库侧已修过（`reset_orphaned_indexing_docs`，
   BUGFIX #198「知识库上传后一直在索引中」），但运营模式的 `case_files` 从未做过。
3. **前端没有自救出口**。材料行的「↻ 重审」按钮只在 `pending`/`rejected` 时出现，
   `reviewing` 状态下既不能重审也不能取消，用户只能对着转圈干等。

**修复**

- `services/agent/src/agent/config.py`：新增 `ops_review_timeout_sec`（默认 240s）—— 审核全流程墙钟上限。
- `services/agent/src/agent/ops/api.py`：`_do_review_file` 拆成「状态守护壳 + `_run_review_pipeline` 主体」。
  壳层用 `asyncio.wait_for` 加墙钟上限；`TimeoutError` → 落 `pending` 并写明「超时…可点重审」；
  `CancelledError` → 落 `pending`（Agent 重启/连接断开）；其余异常 → 落 `pending`。
  **任何路径都不再把材料留在 `reviewing`。**
- `services/agent/src/agent/ops/cases.py`：新增 `CaseStorage.reap_orphaned_reviews(inflight=..., reason=...)`
  —— 把「状态是 reviewing 但本进程没在跑」的材料落回 `pending`，关联草稿从 `submitted` 退回 `draft`（可重新提交）。
- `services/agent/src/agent/ops/api.py`：新增进程级 `_INFLIGHT_REVIEWS` 登记表 + `_reap_orphaned_reviews()`；
  在 `GET /ops/case`（用户打开面板/刷新）时触发自愈。
- `services/agent/src/agent/main.py`：Agent 启动时自愈一次（对齐知识库 orphan heal 的既有模式）。
- `apps/desktop/src/components/ops/ExpertWorkflowPanel.tsx`：材料行「↻ 重审」在 `reviewing` 状态同样可见；
  交付草稿卡（全屏 + 紧凑两种形态）的「正在审核草稿」旁新增「↻ 重新审核」按钮。
- `apps/desktop/src-tauri/src/commands/ops.rs`：`ops_case_file_review` / `ops_case_draft_submit`
  补 300s HTTP 超时 —— 否则 Agent 挂死时桌面端命令永远不返回。

**验证**

`services/agent/tests/test_ops_case.py` 新增 4 例（审核超时回落 pending、未预期异常回落 pending、
僵尸 reviewing 读取时自愈、正在审核中的材料不被误自愈）；`apps/desktop/tests/expertWorkflow.test.tsx`
新增 1 例（审核中材料可见「↻ 重审」并能触发重新审核）。
`services/agent` 全量 pytest 0 失败 / 0 错误（30 例因内网模型不可达跳过）；
`apps/desktop` vitest 22 例全绿；`npx tsc --noEmit` 与 `npx eslint --max-warnings=0` 零告警；
`ruff check` 全通过；`mypy --strict` 无新增错误（改动前后基线 1192 → 1189）；
Rust `cargo check` 通过。

**教训**

「先写中间态 → 再调外部资源」这个形态，**必须配三件套**：墙钟上限、异常兜底落终态、僵尸状态自愈。
少任何一件，外部资源一挂（LLM 不可达 / 进程重启 / 连接断开），状态就永远停在中间态，
而界面只会诚实地显示「审核中」——用户既看不到错误，也找不到出路。
知识库侧 #198 已经踩过「索引中」这个坑，运营模式这次是同一个坑的第二次出现：
**同类状态机必须成体系地治理，不能修一处漏一处。**

---

### #176 合规审核报告「点依据打不开原文」+ 专家团选择器多余「AI 推荐」标签（两项体验修复）

| 字段 | 内容 |
|---|---|
| **是否修复** | 已修复 |
| **修复时间** | 2026-11-09 |
| **影响范围** | 运营模式「系统合规审核报告」弹窗（专家验收前置）、运营工作台专家团选择器 |
| **相关链接** | 本条（前端体验，无后端协议变更） |

**现象**

1. 运营模式 → 上传材料 → 弹出「系统合规审核发现 N 项风险」→ 展开「依据（知识库）」
   区（《私募投资基金监督管理条例》等条目）→ **点文件名没有任何反应**，看不到原文。
2. 专家团选择器下拉框下方挂着一行绿色的「**AI 推荐**」标签，纯噪音（用户要求去掉这四个字）。

**问题原因**

**第 1 项（真缺陷）**：`RiskAuditReportModal.tsx` 把 `kb_refs` 渲染成**纯文本** `<p>{ref.source}</p>`，
而同一份知识库依据在「审核专家 · 文档审核」模块（`FindingsPanel.tsx`）里是**可点击按钮**，
点击走 `previewLocalFile` 打开原文件。同一个数据、同一个诉求，两处 UI 行为不一致 ——
运营模式这侧漏了可点性，所以「点了没反应」。

后端链路是通的：`ops/doc_audit.py::_run_doc_audit` 调 `doc_review/api.py::_attach_kb_refs`，
`kb_refs` 里已带 `file_path`（`knowledge/storage.py::resolve_file_path` 解析出的绝对路径），
字段与文档审核模块完全一致 —— **数据齐了，只是前端没接**。

**第 2 项（体验冗余）**：`ExpertTeamSelector.tsx` 把选择来源映射成角标展示
（`SOURCE_LABEL.llm = 'AI 推荐'`）。AI 推荐选出的团已经显示在下拉框里，
再挂一个「AI 推荐」角标不提供任何新信息（业务预设/手动才有区分价值）。

**修复**

- 新增 `apps/desktop/src/components/doc-review/KbRefLink.tsx`：抽出统一的「知识库依据条目」组件
  —— 带 `file_path` 渲染为可点击按钮（`previewLocalFile`：md/txt/html 内置预览、pdf 内嵌、
  doc 等交系统默认程序），无 `file_path`（知识文件已清理）降级为纯文本。
- `apps/desktop/src/components/doc-review/FindingsPanel.tsx`：删除本地重复的 `openRefFile` 与内联按钮，
  改用 `KbRefLink`（消除两份同逻辑实现）。
- `apps/desktop/src/components/ops/RiskAuditReportModal.tsx`：
  - 「依据（知识库）」区每条依据改用 `KbRefLink` → 点文件名直接打开命中的知识库原文件；
  - 「原文证据」标题旁新增「· 点此看材料原文」入口 → 打开被审核材料原文（`file.file_path`），
    补齐「报告里的原文摘录 ↔ 材料原文」的验真闭环；
  - 依据区标题旁加「· 点文件名看原文」提示，避免用户不知道可点。
- `apps/desktop/src/components/chat/ExpertTeamSelector.tsx`：`llm` / `keyword` 两种来源不再展示角标；
  `业务预设` / `手动` / `未选择` 保留（它们有区分价值）。

**验证**

- 新增 `apps/desktop/tests/riskAuditReport.test.tsx`（3 例）：依据可点 → 打开知识库原文件；
  原文证据入口可点 → 打开材料原文；无 `file_path` → 纯文本且不触发预览。
- 新增 `apps/desktop/tests/expertTeamSelector.test.tsx`（4 例）：AI 推荐/关键词来源不展示角标；
  业务预设/手动来源角标保留。
- 既有 `tests/findingsPanel.test.tsx`（2 例）保持全绿 —— 抽取共享组件无行为回退。
- `apps/desktop` 相关 vitest 36 例全绿；`pnpm tsc --noEmit` 零错误；
  `pnpm eslint --max-warnings=0`（改动文件）零告警。

**教训**

同一份数据在两个模块里各自渲染时，**可点性这类交互语义必须收敛到共享组件**，
否则「一边能点、一边不能点」几乎必然发生，且用户只会认为另一边坏了。
同理，**角标只在能提供额外信息时才显示**：来源已是「AI 推荐」时，
再标一个「AI 推荐」是纯噪音；「业务预设」才值得标。

---

### #203 专家验收材料行臃肿 + 「⚠ 合规风险 N」不像按钮（正常人会点吗？）

| 字段 | 内容 |
|---|---|
| **是否修复** | 已修复 |
| **修复时间** | 2026-11-09 |
| **影响范围** | 运营模式「专家验收工作流」材料行（ExpertWorkflowPanel / CaseFileRow） |
| **相关链接** | 本条（前端交互，无后端协议变更） |

**现象**

1. 用户反馈：“**合规风险按钮不太明确啊，正常人会点这里吗？**” —— 材料行上的「⚠ 合规风险 N」
   只是一段红色小字，与状态徽标、低置信计数排在一行，没有任何可点提示，普通人不会去点。
2. 用户反馈：“**这一块太臃肿了，要简洁明确，直接显示结果和点击查看审核明细就可以**” ——
   截图里一张被打回的材料默认铺开了「上传材料大按钮 + 状态行 + 四步步骤链 + 意见气泡 +
   关键要素卡（多行胶囊）+ 操作按钮行」，把整张专家卡撑满，真正要看的结论反而被埋在中间。

**问题原因**

- **入口不明确**：`CaseFileRow` 把「⚠ 合规风险 N」渲染成 `<button>` 但只给了 `color` 内联，
  没有边框、没有底色、没有尺寸类 —— 视觉上与旁边两个静态文本徽标几乎同构，
  “可点”完全靠用户猜（且未走 `.btn` 按钮体系，与全局按钮规范脱节）。
- **默认展开造成臃肿**：`detailOpen` 初值为 `file.status === "rejected"` —— 被打回的材料
  默认展开全部明细；而审核意见（结果）本来就在明细区里，于是“先看结论”这个动线被拆成两段。
- **结论与结果各说各话**：状态徽标已经写了「打回」，审核意见又被塞进气泡里（3 行钳制），
  两处信息重叠，且意见与徽标同文（如都是「通过」）时白白占一个框。

**修复**

- `apps/desktop/src/components/ops/ExpertWorkflowPanel.tsx` → `CaseFileRow` 重排为三层：
  - **摘要行**：文件名 + 结论徽标 + `⚠N 待核`（低置信计数补上「待核」二字，不再是光秃秃的 `⚠N`）；
  - **结果行（默认可见）**：专家审核意见直接显示（2 行钳制、点开看全文），左侧色条随结论状态
    （打回红 / 通过绿 / 待审橙）；意见与徽标同文时不再重复铺框；草稿材料只给「审核意见见草稿卡」指引；
  - **入口行（默认可见）**：`⚠ 合规风险 N` 改为独立按钮（`.btn btn-xs btn-danger`，
    灰底红字红边 + hover 变红底），另配「查看审核明细 / 收起审核明细」文字入口；
  - **审核明细（默认收起）**：步骤链 / 关键要素 / 证据链 / 人工干预（重审 · 打回定位 · 通过 · 打回 · 删除）
    全部收进此处，打回材料也不再默认展开。
- 步骤链、关键要素、证据链的渲染逻辑与数据接口均未变，只换了收纳位置。

**验证**

- `apps/desktop/tests/expertWorkflow.test.tsx` 新增 1 例：结论直显可见、明细默认收起（关键要素/证据链不出现）、
  「合规风险」为 `.btn` 独立按钮且点击能开报告、点「查看审核明细」才见关键要素。
- 同步更新 2 例受影响用例：明细入口改点「查看审核明细」（打回定位用例）、
  「通过」材料不再重复铺结果框（渲染用例）。
- `apps/desktop` 全量 vitest 70 文件 / 472 例全绿；`npx tsc --noEmit` 零错误；
  `npx eslint --max-warnings=0`（改动文件）零告警。

**教训**

- **可点的东西必须长得像按钮**：用了 `<button>` 标签不等于用户看得见 —— 没有边框/底色/尺寸类时，
  它和旁边的静态文本没区别（与 #176「点依据打不开原文」同源：交互语义必须被看见）。
- **默认展开要克制，被打回也不例外**：结果（审核意见）应当在第一屏直显，明细（步骤链/要素/证据链）
  应当按需展开；展开明细不应随状态自动打开。
- **同一句话说两遍就是臃肿**：结论徽标与审核意见同文时，只留一处。

---

### #204 运营模式点「数据字典」无反应（CSS 特异性竞争 + center 分支缺失）+ 侧栏占位项隐藏 + 数据字典全屏树形改造

| 字段 | 内容 |
|---|---|
| **是否修复** | 已修复 |
| **修复时间** | 2026-11-09 |
| **影响范围** | 左侧 ActivityBar（侧边栏）、数据字典入口、WorkspaceLayout 布局分支优先级 |
| **涉及文件** | `apps/desktop/src/components/chrome/ActivityBar.tsx`、`apps/desktop/src/layouts/WorkspaceLayout.tsx`、`apps/desktop/src/views/DataDictView.tsx`（新增）；删除 `apps/desktop/src/components/ops/DataDictionaryPanel.tsx` |

**现象**

1. 运营模式（operator）下点击 ActivityBar「📖 数据字典」**没有任何反应**：主区仍是运营工作台、左侧栏也不展开；审核/数据专家模式同样点不动。
2. 侧边栏里「审计/版本」「运行/调试」「扩展」「代码符号」四个入口全是占位/装饰项，用户要求隐藏。
3. 数据字典原 SideBar 260px 窄条卡片列表太挤，用户要求：隐藏会话与控制台、全屏铺平、按树维护。

**根因**

「点不动」是两处叠加：

① **center 分支缺 data-dict**：`WorkspaceLayout` 中央区条件链里没有 `active === 'data-dict'` 分支，全接管模式下 mode 分支（`mode === 'operator' → OperationsWorkbench`）兑底 —— 主区永远渲染运营工作台，点数据字典主区纹丝不动。

② **SideBar CSS 特异性竞争**：operator/auditor/analyst 折叠规则 `[data-mode="operator"]:not(...):not(...) .workspace-sidebar`（4 个选择器）与 data-dict 恢复规则 `.workspace-root:has(.data-dict-active) .workspace-sidebar`（3 个选择器）都带 `!important`，前者特异性更高胜出 → 左侧栏保持 `width: 0`，永远打不开。这正是「三处协同模板」里 CSS `:has` 覆盖方案的已知短板（SideBar early-return 方案注释里早写着「V1 应统一为一种模式」）。

**修复**

1. **数据字典升级为横切全屏视图**（与「需求工作台」同款模式）：
   - center 条件链在 mode 分支之前新增 `active === 'data-dict'` → `<DataDictView />`（横切优先于专家模式分支，任何模式点击都接管主区）；
   - SideBar 对 data-dict early-return 折叠（JS 方案替代失效的 CSS `:has` 覆盖），删除 CSS 恢复规则与 `data-dict-active` class；
   - RightPanel（控制台）在 data-dict activity 下不挂载 —— 「会话与控制台都隐藏，铺平维护」。
2. **新建 `DataDictView` 全屏树形维护视图**：左 280px 分类树（分组可折叠、key+名称叶子、内置 seed 徽章、搜索实时过滤）｜右详情/编辑区（详情直显 key/分类/名称/值/说明/更新时间，编辑表单右置替代原顶部挤压条）。复用 dictStore 与 `/dict/*` IPC，无后端改动。
3. **侧边栏优先级第一（用户要求：除设置外，任何模式点侧栏都能出来）**：
   - explorer/sessions/search 的折叠例外补全（`:not([data-activity="explorer"])`），专家模式下点 explorer 也展开 SideBar；
   - SideBar 内容分支 explorer 提前到 mode 分支之前（专家模式下显示资源管理器内容而非模式占位）；
   - LeftPanelDevSwitcher（系统资产/文件列表/系统功能点切换）与 SideBarHeader 标题跟随从仅 `full` 模式扩展到全模式。
4. **隐藏 4 个占位项**：ActivityBar `ITEMS` 增加 `hidden` 标志过滤渲染（source-control / run-debug / extensions / code-nav），ActivityId 类型与 TITLES 保留，日后想恢复改一个布尔即可。

**验证**

- `npx tsc --noEmit` 零错误；全量 `vitest run` 70 文件 / 472 例全绿。
- 行为核对：运营/审核/数据专家模式下点数据字典 → 全屏树形视图；点 explorer → SideBar 展开资源管理器；点搜索/会话/协作/需求 → 各自视图照常；设置页穿透优先级不变。

**教训**

全接管模式与横切入口共存时，「谁覆盖谁」必须单点裁决：要么统一 JS early-return（SideBar 折叠 + center 分支前置），要么统一 CSS `:has` 高特异性 —— 两种方案并存时，CSS 方案在特异性竞赛中必输（4 vs 3），且输得毫无报错、表现为「点了没反应」。`!important` 对 `!important` 比的是特异性不是书写顺序，布局冲突排查第一步是数选择器。

占位入口不上线就先藏起来：`hidden` 标志比删代码更可逆，且 ActivityId 类型保留使 Record 完备性检查继续生效。

---

### #205 专家卡提问框收起⇄展开瞬间跳变 + 智能体运行/思考指示去绿色改简洁黑（全局 + chat）

| 字段 | 内容 |
|---|---|
| **是否修复** | 已修复 |
| **修复时间** | 2026-11-09 |
| **影响范围** | 运营模式专家卡（ExpertCaseCard 提问框）、全局 Agent 状态徽标（TopBar/StatusBar）、chat 思考中动效（AiThinkingIndicator） |
| **涉及文件** | `apps/desktop/src/components/ops/ExpertWorkflowPanel.tsx`、`apps/desktop/src/styles/globals.css`、`apps/desktop/src/components/chrome/AgentStatusBadge.tsx`；测试 `apps/desktop/tests/agentStatusBadge.test.tsx` |

**现象**

1. 专家卡「💬 向XX追问」收起时是一条窄按钮（卡片底部），点开后输入框行瞬间出现 —— 无任何过渡，突兀。
2. 智能体运行的绿色图标（状态栏/顶栏「运行中」、chat「思考中」的绿色 orb/能量柱）用户认为土，要求全局改简洁黑色。

**根因**

① 提问区是 `{askOpen ? <输入行> : <按钮>}` 的条件渲染，两态互斥卸载/挂载，无过渡。
② 运行/思考指示绿色硬编码：`AgentStatusBadge` busy 色 `#10a37f`；`globals.css` 里 `ai-thinking-glow`、`ai-orb-ring`（锥形渐变）、`ai-orb-core`（径向渐变球）、`ai-orb-satellite`（粒子）、`ai-eq-bar`（能量柱）、`thinking-shimmer`（流光高光）全部用科技绿 `#10a37f/#34d399/#6ee7b7`。

**修复**

1. **提问框过渡动画（ask-dock）**：两态 pane 常驻叠放在同一 grid 格，外层 `grid-template-rows 0fr⇄1fr`（240ms）裁切高度实现展开/收起动画，pane 层 `opacity`（200ms）淡入淡出 + `pointer-events` 穿透防透明层挡点击；`autoFocus` 因常驻渲染失效，改 `askInputRef` + `useEffect` 展开时聚焦；发送按钮同步黑化 `#1f1f1f`。
2. **运行/思考指示黑色化**：`AgentStatusBadge` busy `#10a37f → #1f1f1f`（弧自转+呼吸动画保留，只换色）；`ai-thinking-glow/orb-ring/orb-core/orb-satellite/eq-bar/shimmer` 全部改黑灰系（`#1f1f1f/#52525b/#3f3f46`）。语义性绿色（验收通过/成功/交付标准勾选）保留不动。

**验证**

`npx tsc --noEmit` 零错误；全量 vitest 71 文件 / 478 例全绿；改动文件 eslint 零告警。
真机验证：专家卡点「追问」应平滑展开输入框并自动聚焦，点 ✕ 平滑收起；Agent 运行时顶栏/状态栏/chat 思考胶囊均为黑色动效。

**教训**

条件渲染 `A ? B : C` 切换布局位置/形态的交互，用户感知的是「瞬间跳变」；要平滑就把两态都常驻、用高度裁切（grid 0fr⇄1fr）+ 透明度过渡来切换，注意用 `pointer-events` 防止透明层挡点击、用 ref+effect 补回被常驻渲染废掉的 `autoFocus`。语义色要分层管理：状态指示色（运行/思考）与业务语义色（成功/通过）分开，改主题时只动前者，别把「验收通过」的绿也误伤。

---

### #206 业务办理无台账：侧边栏缺「业务汇总」入口，业务发起即登记 + 四态流转 + 多维查询

| 字段 | 内容 |
|---|---|
| **是否修复** | 已修复 |
| **修复时间** | 2026-11-09 |
| **影响范围** | 运营模式（专家验收工作流）、左侧 ActivityBar 新增顶级入口 |
| **相关链接** | 本条（新增功能，无既有行为回退） |

**需求（用户原话）**

「侧边栏多加一个业务汇总，作为对业务的查询，**只要业务发起了就要记录**，状态为正在办理，挂起，人工确认，结束。支持按多个维度查询（业务大类，小类，日期，状态等）」。

澄清后的口径（逐项与用户确认）：

1. **发起时点**：客户经理**上传第一份材料**才算发起（不是点业务、不是提交草稿）；
2. **记录粒度**：一条业务一条记录（同一业务反复办理不新增行），状态随办理变化；
3. **状态流转**：
   - 正在办理 —— 系统自动（发起建档）；
   - 挂起 —— **人工**点「挂起」→ 当前交易界面**只读冻结**，客户经理可去办另一个交易，回来**接着原记录继续办**；
   - 人工确认 —— **系统自动**：业务审核出问题需要人工接入（材料被打回 / 审核异常）时转入；
   - 结束 —— **系统自动**：业务没问题、办理结束（导出交付物）后转入；
4. **界面形态**：侧边栏点「业务汇总」→ **全屏查询页**（不是窄栏面板）。

**问题原因**

- 业务**只在办完之后**才留痕：现有 `business_records` 表是「导出交付物时 AI 生成的小结卡片」，**业务发起到办完之间没有任何台账** —— 客户经理中途挂起、被打回待处理时，管理层看不到任何「有哪些业务在办」。
- 侧边栏没有业务查询入口：ActivityBar 只有资产/搜索/协作/会话/需求/数据字典，运营侧只能靠人记。

**修复**

- 后端（新增 `services/agent/src/agent/ops/tasks.py` + `ops/schema.sql` 新表 `business_tasks`）：
  - 台账与「业务记录卡片」**分表**（前者发起即建、状态流转；后者完成后的小结），用 `case_id` 关联；
  - 状态四态：`in_progress` 正在办理 / `suspended` 挂起 / `manual_review` 人工确认 / `closed` 结束；`case_id` 唯一索引（一条业务一行）；
  - 状态**派生式**同步：`_sync_task_status()` 按 Case 现有材料状态重算（被打回 / 审核异常 → 人工确认；处理完 → 回到正在办理），挂起与已结束不自动改写（人工优先）；
  - 触发点四处：上传材料（建档）、审核出结果（打回/异常 → 人工确认）、人工改判（清待处理项 → 回到办理中）、导出交付物（→ 结束）；
  - 新增 4 个端点：`POST /ops/tasks/start`（幂等建档）、`GET /ops/tasks`（状态/大类/小类/日期/关键词多维筛选 + 各状态计数）、`GET /ops/tasks/{id}`（详情：待处理项 + 材料进度）、`POST /ops/tasks/{id}/status`（挂起 / 恢复）；
  - `GET /ops/case` 顺带带回当前业务的台账状态（前端据此冻结界面）。
- 前端：
  - [ActivityBar.tsx](../apps/desktop/src/components/chrome/ActivityBar.tsx) 新增「业务汇总」图标（`TableIcon`）；[uiStore.ts](../apps/desktop/src/store/uiStore.ts) 增 `biz-summary`；[WorkspaceLayout.tsx](../apps/desktop/src/layouts/WorkspaceLayout.tsx) 增 center 分支 + SideBar early-return 折叠（横切所有模式，含运营模式）；
  - 新增 [BusinessSummaryView.tsx](../apps/desktop/src/views/BusinessSummaryView.tsx)：状态计数条（点一下即筛选，可多选）+ 筛选行（业务大类 / 小类 / 发起日期区间 / 关键词）+ 结果表（业务名称｜大类｜小类｜状态｜发起时间｜最后更新｜经办人）+ 点行展开明细（待处理项 / 材料进度 / 关联小结）；
  - [ExpertWorkflowPanel.tsx](../apps/desktop/src/components/ops/ExpertWorkflowPanel.tsx)：页头新增「⏸ 挂起 / ▶ 恢复办理」按钮 + 挂起横幅（黄色，提示可先去办别的业务）；挂起期间上传 / 提问 / 重审 / 改判 / 导出全部只读（按钮禁用 + store 侧双重拦截）；
  - [opsCaseStore.ts](../apps/desktop/src/store/opsCaseStore.ts)：新增 `task` 状态与 `startTask / suspendTask / resumeTask / isSuspended`。
- Rust（[ops.rs](../apps/desktop/src-tauri/src/commands/ops.rs)）：新增 `ops_task_list / ops_task_get / ops_task_status / ops_task_start` 四个 Tauri 命令并注册。

**验证**

- 新增 [test_ops_tasks.py](../services/agent/tests/test_ops_tasks.py) 12 例：上传即建档（含标签）/ 无 start 兵底建档 / 幂等 / 打回转人工确认 / 审核异常转人工确认 / 改判回到办理中 / 挂起与恢复 / 挂起期间不改状态 / 非法动作 400 / 导出结束 / 多维查询（大小类、状态多值、关键词、日期区间、计数）/ 详情；
- 新增 [businessSummary.test.tsx](../apps/desktop/tests/businessSummary.test.tsx) 6 例：查询页渲染 + 计数条、点状态即筛选、筛选参数下发、点行展开明细、挂起冻结（上传禁用 + 横幅）、恢复解冻；
- `services/agent` 全量 pytest 0 失败 / 0 错误（9 例因内网模型不可达跳过）；`apps/desktop` vitest 71 文件 / 478 例全绿；`npx tsc --noEmit` 零错误；改动文件 eslint 零告警；`ruff check` 全通过；`mypy --strict` 无新增错误；Rust `cargo check` 通过。

**教训**

- **「办完之后留痕」不等于「有台账」**：小结卡片只能回答「做过什么」，回答不了「现在有多少业务在办、卡在谁那里」—— 两者要分表、各司其职。
- **状态要有唯一权威来源**：台账状态不是另存一份，而是**从材料现状派生**（被打回 → 人工确认），否则两处状态各说各话；同时人工状态（挂起）必须**压过**自动派生，否则用户刚点挂起就被系统改回去。
- **发起即建档要有双保险**：前端建档 + 后端上传接口兵底建档，前端调用失败也不会漏登记。

### #207 运营模式首开红字「ops command failed: error decoding response body」+ 点业务无反应（切模式才恢复）

**现象**

启动 EAIDE 进入运营模式，中间区顶部红字 `ops command failed: error decoding response body`；此时点左侧业务列表毫无反应（界面不变、也不发请求），要切到其他模式再切回才恢复正常。

**排查（安装目录 logs/eaide.log + logs/agent.log，2026-11-09 14:26 启动窗口）**

- Agent access log 里首笔 `GET /ops/case` 是 `200 OK`，但 Rust 侧 `resp.json()` 失败 → 响应体在传完前被中断（与 #197 同源：Agent 单 worker 忙时个别连接瞬时截断）；
- #197 新增的有界重试 `agent_http::send_json` 当时只改造了 knowledge / doc_review / audit_expert 三处，**ops.rs 的全部 GET 仍是裸 `reqwest::get` + `resp.json()`、无重试**，一次截断直接冒泡成前端红字；
- 失败后 `opsCaseStore.error` 置位，但 `selectedItemId` 未变 → `ExpertWorkflowPanel` 的 loadCase effect（deps 仅 projectName / selectedItemId）不会重跑 → 再点同一业务彻底无操作，只有切模式强制重挂载才重新加载，即「切模式才恢复」的由来。

**修复**

- Rust（[ops.rs](../apps/desktop/src-tauri/src/commands/ops.rs)）：8 处幂等 GET 全部改走 `agent_http::send_json`（3 次有界重试 + 线性退避）；POST/PUT/DELETE 保持单次不重试（HITL 写操作绝不重复副作用）；错误文案经 `to_cmd_err` 原样透传，前端瞬态判定仍命中；
- 前端（[opsCaseStore.ts](../apps/desktop/src/store/opsCaseStore.ts)）：`isTransient` 增加 `decoding response body` / `read_body` 两个响应体截断瞬态模式 —— Rust 重试耗尽仍冒泡到前端时再静默退避重试一轮，不把红字甩给用户；
- 前端（[OperationsWorkbench.tsx](../apps/desktop/src/views/OperationsWorkbench.tsx)）：`handleSelect` 再点同一业务时检测失败态（error 非空或 case_id 与选中不匹配，含在途挂起）手动补发一次 loadCase —— 再点同一业务 = 重试，不再依赖切模式；
- Rust：`ops_task_list` 加 `#[allow(clippy::too_many_arguments)]`（Tauri 命令参数必须平铺，台账多维查询维度多）。

**验证**

`cargo check --all-targets` 与 `cargo clippy --all-targets -- -D warnings` 零告警；`pnpm vitest run tests/opsCaseStore.test.ts tests/operationsWorkbench.test.tsx` 19 例全过（含新增 2 例：decoding 错误静默退避重试、加载失败后再点同一业务手动重试）；改动文件 eslint 零告警。真机验证需用户自行打包：首开再遇瞬时截断应重试自愈、不出红字；加载失败后再点同一业务应立即重新加载。

**教训**

- **公共自愈机制补齐要一次扫全同类命令模块**：#197 只修了报障的三处，同一根因换个模块（ops）又冒头；「access log 200 OK 但客户端报 decode error」是响应体截断的指纹，排查此类红字先看该命令有没有有界重试。
- **「effect deps 未变 = 不重新加载」必须给失败态留手动重试入口**：否则用户会自己发明「切模式再切回」的绕法并固化为使用习惯，故障就被掩盖成操作仪式。

---

### #208 办理业务点交付物「填写」直接全屏铺满，改为右侧抽屉侧边出现

**现象**

办理业务时点专家交付物（如「对公开户资料清单」）→ 草稿表单整屏铺开（`fixed inset-0`），
遮住专家卡与材料行；想对照审核意见填表只能反复退出全屏再进入。

**问题原因**

[ExpertWorkflowPanel.tsx](../apps/desktop/src/components/ops/ExpertWorkflowPanel.tsx) 里 `lastDirectDraftId`
的 effect 把直开草稿 id 直接写进 `expandedDraftId` —— 等于替用户点了一次「⛶ 全屏」。

**修复**

- [ExpertWorkflowPanel.tsx](../apps/desktop/src/components/ops/ExpertWorkflowPanel.tsx)：`lastDirectDraftId`
  effect 改为只做三件事 —— 清掉可能残留的全屏状态（`setExpandedDraftId("")`）、展开右侧交付草稿抽屉
  （`setDraftsOpen(true)` + 收起交付物柜）、切到该草稿页签；不再自动全屏；
- 全屏能力保留在草稿卡的「⛶ 全屏」按钮上（需要更大空间时用户手动进入），删除的只是「点击即铺屏」的默认行为。

**验证**

`pnpm vitest run tests/expertWorkflow.test.tsx` 23 例全过（原「直开草稿只自动全屏一次」用例改写为
「直开草稿侧边展开」：断言抽屉头部出现、无「✕ 退出全屏」、页签来回切换不弹全屏）；
`npx tsc --noEmit` 零错误；改动文件 eslint 零告警。

**教训**

- **默认视图要选「不打断业务上下文」的那一种**：办理区（专家卡 + 材料行）是填表时的参照物，
  铺屏等于把参照物关掉，用户只能靠记忆填表。

---

### #209 资源管理器（系统资产）在专家模式误展示，收敛为仅开发模式可见

**现象**

切到运营 / 审核 / 数据 3 个专家模式时，最左侧 ActivityBar 仍挂着「Explorer 系统资产」入口，
左侧栏还会渲染资源管理器内容（系统资产树 + 「系统资产 / 文件列表 / 系统功能点」子切换），
与专家工作台自带的左栏（业务列表 / 审批工作台 / 数据源树）重复占位。

**问题原因**

- 2026-11-09 为修「专家模式下点侧栏图标无反应」，把 explorer 也加进了专家模式的侧栏可见白名单：
  `[data-mode="..."]:not([data-activity="explorer"])` 的例外被取消，SideBar 内容分支也从
  `mode` 判断改成 `activity === 'explorer'` 优先 —— 资源管理器（开发模式专属资产）被当成了横切能力；
- ActivityBar 的 ITEMS 没有「仅开发模式」的概念，explorer 条目对所有模式一视同仁。

**修复**

- [ActivityBar.tsx](../apps/desktop/src/components/chrome/ActivityBar.tsx)：新增 `devOnly` 标记，
  explorer 条目标 `devOnly: true`，渲染时按 `mode === 'full'` 过滤 → 专家模式不再出现该入口；
- [WorkspaceLayout.tsx](../apps/desktop/src/layouts/WorkspaceLayout.tsx)：
  - 专家模式侧栏折叠 CSS 去掉 `:not([data-activity="explorer"])` 例外 → 专家模式外层 SideBar 恒折叠
    （sessions / search 仍保留例外，横切能力不受影响）；
  - SideBar 内容分支由 `activity === 'explorer'` 改为 `mode === 'full'` → 资源管理器主体
    （系统资产 / 文件列表 / 系统功能点）只在开发模式挂载，专家模式走各自工作台；
  - explorer 子切换（LeftPanelDevSwitcher）与标题跟随逻辑加 `mode === 'full'` 条件。

**验证**

新增 `tests/activityBar.test.tsx`（3 例：入口的模式可见性 + 横切能力不受影响）与
`tests/explorerDevModeOnly.test.tsx`（3 例：真实 App 渲染，断言专家模式既无入口也无子切换）；
`pnpm test` 75 个测试文件 / 517 例全过；`npx tsc --noEmit` 零错误；改动文件 eslint 零告警。

**教训**

- **「跨模式可见」应是白名单而不是默认**：`sessions` / `search` 是横切能力（任何模式都要点得动），
  资源管理器是开发模式专属资产 —— 两者不能共用同一套例外规则。把专属能力塞进白名单，
  就会在专家模式里长出与工作台重复的左栏。

---

### #210 从其他界面返回业务办理又弹出草稿表单 —— 直开/新草稿标记从未真正清标

**现象**

点交付物填完表后，切到业务汇总 / 其他 Activity 再回到业务办理，表单又自动弹出来（#208 之前是全屏，
之后是自动拉开右侧抽屉）；同一份草稿反复弹，用户只能每次手动收起。

**根因**

[ExpertWorkflowPanel.tsx](../apps/desktop/src/components/ops/ExpertWorkflowPanel.tsx) 的注释一直写着「消费即清标」，
但实际只用组件内的 `autoExpandedDraftRef` 去重 —— 而 `WorkflowBody` 随界面切换会卸载，ref / `draftsOpen` /
`expandedDraftId` 全部重置，store 里的 `lastDirectDraftId` / `lastNewDraftId` 却一直保留。
重新挂载时 effect 读到旧标记 → 又当作一次新的直开。这与 #208 是同一处逻辑的两层问题：
前者是「默认展开成全屏」，后者是「标记没有生命周期，跨挂载重复消费」。

**修复**

- [opsCaseStore.ts](../apps/desktop/src/store/opsCaseStore.ts)：新增 `consumeDraftSignals()` —— 同时清空
  `lastDirectDraftId` / `lastNewDraftId`，与 `markTeamRead` 同属「一次信号一次性消费」的写法；
- [ExpertWorkflowPanel.tsx](../apps/desktop/src/components/ops/ExpertWorkflowPanel.tsx)：两个消费 effect
  （新草稿 / 直开草稿）处理完后立即 `consumeDraftSignals()`；离开再回来只剩「📝 交付草稿（N）· 继续填写」
  提醒栏（对齐 BUGFIX #134「历史草稿不自动铺屏」的意图）。

**验证**

`apps/desktop/tests/expertWorkflow.test.tsx` 24 例全绿，新增 1 例「离开再回到业务办理（组件重挂）不再自动弹抽屉」
（首次挂载抽屉自动展开 → unmount 后重挂 → 只剩提醒栏、无全屏页头）；「有模板的交付物」用例断言改为
「直开标记消费后归空 + 侧边抽屉展开 + 无全屏」；`npx tsc --noEmit` 与改动文件 eslint 零告警。

**教训**

- **一次性信号必须有生命周期**：用「标记 + 组件本地去重」代替「消费即清标」，只要组件会重挂
  （切页签 / 切界面 / 路由返回）就一定重复触发；本地 ref 只能防同一挂载内的重复，防不了重新挂载。

---

### #211 业务办理找不到「挂起」按钮 —— 台账未建档时按钮根本不渲染

**现象**

新建一个业务（还没上传材料）进入办理界面，页头只有「凭证预览 / 交付物柜 / 专家团预设 / ⋯」，
没有「⏸ 挂起」，用户以为核功能没做。上传过材料的业务就能看到。

**根因**

- [ExpertWorkflowPanel.tsx](../apps/desktop/src/components/ops/ExpertWorkflowPanel.tsx) 里按钮包在 `{task && (...)}` 中；
  而台账是「**业务发起即建档**」—— 首次上传材料才调 `startTask`（后端 `/ops/tasks/start`）；
  新业务的 `task` 恒为 `null` → 按钮不渲染，功能“不存在”。
- `startTask` 失败只在控制台 `console.warn`，用户完全无感，也分不清是「没建档」还是「没做功能」。

**修复**

- [ExpertWorkflowPanel.tsx](../apps/desktop/src/components/ops/ExpertWorkflowPanel.tsx)：挂起按钮**常显**；
  未建档时 `disabled` + 置灰，悬浮提示「上传第一份材料后可用：业务发起即建档……」；
  已建档后行为不变（挂起↔恢复，到顶后横幅 + 上传/提问双冻结）。
- [opsCaseStore.ts](../apps/desktop/src/store/opsCaseStore.ts)：`startTask` 失败写入可见错误条
  （「业务建档失败：…（挂起与业务汇总暂不可用）」），不再静默——否则挂起不可用时用户无从得知原因。

**验证**

`apps/desktop/tests/businessSummary.test.tsx` 新增 1 例「未建档也看得到挂起，但置灰并提示何时可用」；
挂起冻结/恢复 2 例维持通过（`businessSummary.test.tsx` 7 例 + `expertWorkflow.test.tsx` 24 例 = 31 例全绿）；
`npx tsc --noEmit` 与改动文件 eslint 零告警。后端台账实现已存在：`ops/tasks.py` +
`/ops/tasks/start`、`/ops/tasks`、`/ops/tasks/{id}/status` 三端点 + `tests/test_ops_tasks.py` 12 例。

**教训**

- **主操作按钮不要被「数据还没建出来」这种前置条件藏掉**：应当可见但置灰，并告知触发条件；
  藏起来的按钮不会让用户觉得「还没开始」，只会让用户觉得「没这个功能」。

---

### #212 靠「交付草稿 → 上传文件 OCR 自动填入」办的业务永远没有台账（挂起/汇总不可用）

**现象**

用户反馈「新开业务找不到挂起按钮」（附图：页头只有 凭证预览 / 交付物柜 / 专家团预设 / ⋯）。
排查发现：该业务是通过交付草稿卡里的「⬆ 上传文件 OCR 自动填入」入件的，专家卡上一个材料也没传。

**根因**

建档（`startTask`）只在 `attachFiles` 里触发 —— 即专家卡的上传/拖拽/粘贴三条路径；
但「草稿 OCR 自动填入」走的是 `ocrFillDraft` → 直接调后端 `opsCaseDraftOcrFill`，
草稿里 `file` 字段随提交上传的那次 `attachFiles` 也没传 `taskMeta`（不会建档）。
结果是：**用 OCR 填单/草稿文件办完整笔业务，`business_tasks` 里始终没有这一行**，
挂起不可用、业务汇总查不到（叠加 #211 的按钮隐藏条件 → 看起来就是「功能不存在」）。

**修复**

- [opsCaseStore.ts](../apps/desktop/src/store/opsCaseStore.ts)：`ocrFillDraft(draftId, file, taskMeta?)`
  新增可选业务上下文；首次 OCR 上传且当前无台账时先 `startTask`（后端按 `case_id` 幂等）；
- [ExpertWorkflowPanel.tsx](../apps/desktop/src/components/ops/ExpertWorkflowPanel.tsx)：`DraftFormCard`
  接收 `taskMeta` 并下传：草稿 OCR 上传与草稿内文件随提交上传（`attachFiles(..., taskMeta)`）都会补建档。

**验证**

`apps/desktop/tests/expertWorkflow.test.tsx` 新增 1 例「草稿 OCR 上传也算业务发起：自动补建档」
（断言 `opsTaskStart` 被调且 store.task 就位）；`expertWorkflow` 25 例 + `businessSummary` 7 例 = 32 例全绿；
`npx tsc --noEmit` 与改动文件 eslint 零告警。

**教训**

- **「发起即建档」必须把入口穷举**：同一件事（上传材料发起业务）有多条技术路径
  （专家卡上传/拖拽/粘贴、草稿 OCR、草稿文件字段），只在一块里埋 hook 就一定会漏；
  建档这类幂等副作用的正确位置是 store 层的“材料/凭证新增”入口，而不是各个 UI 入口。

---

### #213 办理区提问要找卡展开输入框、专家卡下沿参差 —— 输入框常驻到专家卡下面、卡片底部拉平

**现象**

运营模式办理界面：专家卡底部参差不齐（空卡短一截、有料卡长一截），卡片下方留大片空白；
想向某位专家提问，要先找到那张卡里收起的「💬 向XX追问」展开才能打字，路径绕。
用户直接点名方案：「直接把输入框显示在专家下面不就好了吗？然后把专家的下面拉平」。

**问题原因**

- 紧凑卡（无材料且无在途动作）在卡片行里 `alignSelf: "flex-start"`，只占内容高度 → 卡底参差；
- 提问入口按 2026-11 交互优化收进每张卡（默认收起），但在「无需对话」的产品语境里提问本就是
  次要动作，逐卡藏输入框既费找又费展开。

**修复**

- [ExpertWorkflowPanel.tsx](../apps/desktop/src/components/ops/ExpertWorkflowPanel.tsx)：
  - 新增 `TeamAskBar` 共享提问行，常驻在专家卡正下方（主体区底部）：下拉选提问对象 +
    输入框（回车 / 发送按钮）+ busy 态思考动效；切团 / 团成员变化时目标自动回落到第一位
    （键用 `team.id::member.name`，跨团重名不串）；
  - 删除每张卡内嵌的收起/展开提问框（ask-dock 两态动画整块下线，`globals.css` 同步清理），
    问答记录仍落在对应专家卡的滚动区；提问中的卡片在常驻区显示「思考中」动效；
  - 卡片行去掉 `alignSelf` 短一截的写法，全部专家卡等高铺满（底部拉平）。
- 测试：`expertWorkflow.test.tsx` 提问用例改走共享提问行；两处「卡头渲染专家名」断言
  改为限定在专家卡节点内（专家名现在同时出现在卡头与共享下拉里）。

**验证**

`pnpm vitest run tests/expertWorkflow.test.tsx tests/operationsWorkbench.test.tsx
 tests/opsCaseStore.test.ts` 44 例全过；`pnpm test` 76 个测试文件 / 526 例全过；
`npx tsc --noEmit` 零错误；改动文件 eslint 零告警。

**教训**

- **次要动作要降到「顺手就能做」的位置，而不是藏进各自的作用域**：6 张卡各藏一个收起输入框
  看似降噪，实际把「提问」从一步变成「找卡 → 展开 → 打字」三步；一条共享输入行 + 目标下拉，
  同时解决入口绕和卡底参差两个观感问题。
- **加了新入口要排查同名文本的全局断言**：`getByText("专家名")` 这类断言在共享下拉出现后会
  命中多元素，渲染断言应限定在目标节点内（卡片 id）而不是全局 first-match。

---

### #214 凭证预览没有层次感 —— 缺什么/必传/已齐同权重，一眼看不到重点

**现象**

用户反馈「凭证预览没有层次性，看不到重点」（附图）：顶部必传齐备度、缺口、未归属三个小徽章平铺在同一行；
左栏所有凭证项都是同一个白底小卡，已齐/缺图/选传只看得到左边一个符号；右侧选中空态只居中两行小字，
大片留白。用户要自己逐项扫列表才能知道「到底缺哪张」。

**根因**

信息本身都在，但**用同一个字号 / 同一个颜色 / 同一个容器**平铺 —— 没有视觉优先级：

1. 头部把「进度」与「缺口」混成同级徽章，缺什么没有位置优势；
2. 左栏分组（团/专家/凭证）只靠缩进区分，缺图项与已齐项无差别；
3. 右栏抬头名称/必传/类型/要求挤在一行，空态无引导；要素区未采集与已采集同权重。

**修复**（[CredentialPreviewModal.tsx](../apps/desktop/src/components/ops/CredentialPreviewModal.tsx)）

- **头部两层**：第一行标题 + 操作；第二行「必传进度条 + 数字」+ 红色缺口 chip
  （「缺 N 张：…」可点击 → 直接定位第一张缺失凭证，新增 `focusFirstMissing`）+ 未归属提示；
- **左栏状态化**：团名加分隔线与「本团必传 n/m」徽章；专家行加「缺 N」徽章；
  凭证卡改为「圆形状态点 + 名称 + 必传/选传」两行结构，缺图卡置白底 + 左侧红竖条 + 深色文字，
  已齐卡降饱和灰化，选中卡蓝竖条 —— 一眼能分出「先看哪几张」；
- **右栏聚焦**：抬头两行（名称 + 状态徽章 + 份数；介质 + 要求）；空态改为卡片式引导
  （图标 + 主文案 + 去哪位专家上传 + 拖动提示）；要素区加「已采 x/y」计数，未采集改灰虚线降噪。

**验证**

`apps/desktop/tests/credentialPreview.test.tsx` 新增 1 例「点顶部缺 N 张直接定位到第一张缺失凭证」，
12 例全绿；全量 vitest 76 文件 / 527 例全绿；`npx tsc --noEmit` 与改动文件 eslint 零告警。

**教训**

- **「看不到重点」通常不是信息不够，而是信息同级**：先把「最该做什么」（缺哪张）放到位置/色彩/尺寸三者中
  至少两个维度上占优，再考虑加内容。
- **状态不能只靠符号**：一个小 ✓/✗ 在 300px 宽的列表里几乎不可扫；底板色 + 左竖条 + 文字权重的组合
  才能支持「扫视」而不是「逐行读」。

---

### #215 #213 理解偏差返工 —— 专家卡下方要的是「交付物页签区」而不是提问输入框

**现象**

#213 把用户说的「输入框」理解成了提问输入框，在专家卡下方加了共享提问行并拆掉了每张卡
内嵌的追问入口。用户澄清：「我意思是交付物的页签直接放在专家下面，不是输入框」——
即 2026-11 抽屉化后搬去右侧的交付草稿区（一份草稿一个页签）应回到专家卡正下方铺平。

**返工内容**

- [ExpertWorkflowPanel.tsx](../apps/desktop/src/components/ops/ExpertWorkflowPanel.tsx)：
  - 撤销共享提问行 TeamAskBar，恢复每张卡内嵌的「💬 向XX追问」收起/展开提问框
    （ask-dock 两态动画与 globals.css 规则同步恢复），提问交互回到原状；
  - 交付草稿区从右侧抽屉（absolute 右侧 54% 宽）改为主体区内专家卡正下方的
    「交付物页签区」：占 45% 高度铺平，页签 / 收起 / ⛶ 全屏行为不变，
    收起时仍走「📝 交付草稿（N）· 继续填写」提醒栏；
  - 专家卡等高铺平（alignSelf 移除）保留 —— 交付物区上沿与卡底平齐。
- 测试：提问用例恢复「点追问 → 展开 → 输入」链路；直开草稿用例的断言不变
  （页签区头部提示 / 页签切换 / 不自动全屏），仅用例名与注释改为「专家卡下方展开」。

**验证**

`expertWorkflow / operationsWorkbench / opsCaseStore` 44 例全过；`pnpm test`
76 个测试文件 / 527 例全过；`npx tsc --noEmit` 与改动文件 eslint 零告警。

**教训**

- **用户的「输入框 / 页签」要对着界面实体核对，不能按字面词猜**：同一段话里的
  「输入框」指的其实是草稿表单（一堆输入框），「页签」是草稿页签；返工一轮的代价
  远高于动手前用一句话确认「你指的是右侧抽屉里那个草稿表单区吗」。
- **界面区域搬家时保留原有可见文本（页签提示、收起按钮）**：断言锚点不变，测试只需改
  用例名与注释，不用动逻辑。

---

### #216 上传文件 OCR 报「error decoding response body」—— 超时被 reqwest 伪装成解码错误，ops 的 POST 从未拿到 #207 的重试

**现象**

交付草稿里点「⬆ 上传文件 OCR 自动填入」，卡片内红字：
`识别失败：ops command failed: error decoding response body`，表单一个字段也没填上。

**排查**

1. **后端其实是成功的**。安装目录 `logs/eaide.log:552` →
   `17:16:20 POST /ops/case/drafts/DR-20261105104557-8183/ocr-fill 200 OK`；`audit.sqlite` 同刻有
   `ops_case_draft_ocr_fill` 记录。直接重放该端点（合成含中文凭证图）：`200` /
   `application/json` / `Content-Length 1041` / UTF-8 与 JSON 均合法 —— 响应体本身没问题。
2. **错误串来自 reqwest，而不是 JSON 解析**。`reqwest-0.12.28/src/async_impl/response.rs`：
   `bytes()` 末尾 `.map_err(crate::error::decode)` 把 **任何** body 流错误统一归到 `Kind::Decode`，
   而 `TotalTimeoutBody` 在截止时间到期时抛的 `error::body(TimedOut)` 也要经过这一行
   （`async_impl/body.rs`）。于是 `is_decode()` 为 true、`is_timeout()` 为 false ——
   **「服务端卡到超时」与「连接被瞬断」在错误形态上完全一样**，光看文案分不出来。
3. **自愈机制没覆盖 POST**。#207 当时只把 ops.rs 的 8 处幂等 GET 改走 `agent_http::send_json`，
   POST 仍走 `json_or_err` 的裸 `resp.json()`：既不重试，又把已取到的 `status` 丢掉，
   连 HTTP 码都无法透到前端。
4. **附带查出第二个独立问题**（本次未修）：audit 该次记录 `engine=rapidocr, source=regex, filled=[]` ——
   端侧 OCR 识别成功，失败的是第二步「把文字对应到表单字段」的模型抽取，异常被
   `_ocr_fill_values` 的 `logger.warning` 吞掉。根上是环境：Ollama 未运行（11434 连接被积极拒绝）、
   无 `llm-config.json`（`eaide.log:13` 已告警）、`router.db.llm_kv` 为空，唯一启用后端是公网 minimax。

**修复**

- [agent_http.rs](../apps/desktop/src-tauri/src/commands/agent_http.rs)：新增 `send_json_bounded(err_prefix,
  max_attempts, total_budget, make)` —— 重试次数与**总预算**双约束，每次尝试的 reqwest 超时
  设为「剩余预算」；错误文案带上 `(attempt n/m)`，至少留下快/慢失败的区分线索；
- [ops.rs](../apps/desktop/src-tauri/src/commands/ops.rs)：`ops_case_draft_ocr_fill` 改走它，
  `max_attempts = 5` / `total_budget = 300s`。**预算取 300s 与改动前的单次超时等值** —— 慢失败
  不会比原来更久，只有毫秒级的连接瞬断能多拿 4 次机会；
- [api.py](../services/agent/src/agent/ops/api.py) `_register_ocr_source_image` +
  [cases.py](../services/agent/src/agent/ops/cases.py) `find_image_by_draft_file`：底图登记改为按
  「同表单 + 同文件名」幂等（命中则就地覆盖文件 + update 行，不再 INSERT）。POST 一旦重试就
  会重跑该端点，不幂等会把凭证预览面板刷成一堆重复影像。

**验证**

`cargo check --all-targets` 与 `cargo clippy --all-targets -- -D warnings` 零告警；
`cargo test --lib commands::agent_http` 2 例全过（新增 1 例锁住「总预算封住累计等待时长」这一
性质，防止被改回只限次数）；`pytest tests/test_ops_credentials.py tests/test_ops_case.py` 59 例全过
（新增 1 例断言连发 3 次 ocr-fill 只留 1 行底图、无 `_1/_2` 文件副本）；`ruff check` 与
`ruff format --check` 对改动代码零问题。

**遗留（本次未改，已告知用户）**

- 重试只消除红字，**不会让字段填上**：模型链路不可用时 `filled` 仍为空，而前端文案会说成
  「没找到能对应到表单的字段」，误导排查方向。需要后端回传 `llm_error`、前端在
  `source=regex && filled=[]` 时明说「识别模型不可用」；
- `extract_chat` 在 ollama / private_llm 两侧均硬编码 300s，而 `_ocr_fill_values` 是三级**串行**
  → 服务端最坏 900s，Rust 侧预算只有 300s，两边错配；且 `llm_backends.timeout_seconds=30`
  在这条路上根本没被使用；
- `_make_summarize_llm` 手搓 ollama→private→cloud 链，绕过 `router.route(task=...)`，不受
  `_LOCAL_ONLY_TASKS` 任务分类治理 —— 证件号/余额这类敏感要素在本地不可用时会直接发到公网云端。

**教训**

- **reqwest 的「error decoding response body」不等于 JSON 格式错**：`bytes()` 把超时与截断全归到
  `Kind::Decode`。看到这句先去对后端 access log：如果那边是 `200 OK`，那本端就是没读完 body，
  该查重试/超时而不是该查序列化。
- **重试上限要用「总预算」而不是「次数」**：两者对快失败等价，对慢失败差出 N 倍。一个模块内
  自制的降级链（如 `_make_summarize_llm`）会把超时叠成串行乘法，外层预算必须覆盖最坏情况。
- **给一类故障上自愈，要按「同类命令」穷举而不是按「报障的那几行」**：#207 修了 GET 就收工，
  同一根因在 POST 上隔了一天又冒出来。




