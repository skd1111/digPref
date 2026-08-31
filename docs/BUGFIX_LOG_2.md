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

