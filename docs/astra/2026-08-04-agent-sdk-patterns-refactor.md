# Agent SDK 设计模式对标与重构设计卡

> 日期：2026-08-04  
> 档位：**设计卡 + large 子模式**（跨 ToolDefinition / ToolLoop / Compactor / Session 持久化 / Multi-Agent，分阶段交付）  
> 对标来源：MicrolabV《从源码拆出来的 8 条 SDK 设计模式》（Claude Code / Hermes）  
> 关联：[`docs/专业度评估与优化路线.md`](../专业度评估与优化路线.md)、[`docs/astra/2026-06-17-prompt-injection-tool-contract-context-compaction.md`](./2026-06-17-prompt-injection-tool-contract-context-compaction.md)  
> 开发指引：仓库内 Cursor skill [`.cursor/skills/developing-astracore/`](../../.cursor/skills/developing-astracore/SKILL.md)（自包含，不依赖本文）

---

## 落地状态（2026-08-04 收口）

| 切片 / 项 | 状态 | 关键落点 |
|---|---|---|
| S0 工具安全字段 + 声明式分区（含 path-scoped） | ✅ | `ToolDefinition`、`partition.py`、`ToolScheduler` |
| S1 Compact 回注 | ✅ | `USER` + `metadata.compacted=True`；`prepare_for_save` 放行 |
| S2 Schema + HITL fail-closed | ✅ | `validate.py`、`ToolExecutor` |
| S3 Toolset | ✅ | `toolset.py`、`ChatOptions.toolset` |
| S4 Transcript 事件化 + replay | ✅ | `transcript.py`、`SQLTranscriptStore`、`load_history` |
| 统一流式/非流式 Agent 循环 | ✅ | `_run_loop` + `BlockingLLMRound` / `StreamingLLMRound` |
| Token 预算硬上限 | ✅ | `TurnBudget`、`max_*_tokens` on Options/Context |
| soft_exec（破坏性预览） | ✅ | `ToolExecutor._soft_exec_result`；**完整 undo 仍未做** |
| `_ACTIVE_RUNS` 多 worker | ✅ | `RunRegistry`（本机所有权 + Redis 扇出；无 Redis 退化） |
| Eval 轨迹评估 | ✅ 已有 | `src/astracore/eval/`（tool_match + LLM-as-judge） |
| pipeline 继续拆分 | 🟡 | 已抽 history / attachment_loader / llm_factory；`pipeline.py` 仍负责 prepare/stream 编排 |

验证：全量 pytest 曾收口为 **344 passed / 25 skipped**（以当前分支再跑为准）。日常开发加载 `.cursor/skills/developing-astracore`。

---

## 意图与边界

### Job-to-be-Done

```
当 AstraCore 已经有可用的 AsyncGenerator 核心循环、HITL、记忆分层时，
我们想要 对照 Claude Code / Hermes 的 8 条 SDK 模式，只补「会造成数据损坏、上下文丢失、权限越界」的结构性缺口，
以便 不重写框架、不引入第二套 Agent 循环，把生产可靠性从「能跑」拉到「敢并行写文件 / 敢长对话」。
```

### Goals（可观察）

| ID | Goal | 成功标准 |
|---|---|---|
| G1 | 工具并发声明式分区 | 同轮 `[Read(a), Read(b), Write(c), Read(d)]` → 批1 并行 Read → 批2 串行 Write → 批3 串行 Read；有单测 |
| G2 | Tool 协议对象补齐安全元数据 | `ToolDefinition` 显式含 `is_concurrency_safe` / `is_readonly` / `is_destructive`；默认 fail-closed（不可并发、非只读） |
| G3 | Compact 摘要确定性回注 | 压缩后摘要进入下一轮 LLM 上下文（不依赖 Tier-2 机会召回）；`_prepare_for_save` 不再把 compact 摘要丢掉 |
| G4 | 工具执行前 Schema 校验回流 | 缺必填参数 → `ToolResult(is_error=True)` 回模型，loop 不崩溃；模型可自纠 |
| G5 | Toolset 角色裁剪 | 主 Agent / spawn worker / 内容类场景可绑定不同工具子集；不再默认「全工具超市」 |

### Non-Goals（本轮不做 / 仍不做）

- ❌ 把核心循环从 AsyncGenerator 再改成别的形态（已对齐，见模式 1）
- ❌ 全量迁移会话存储到本地 JSONL 文件（多用户 SaaS 架构不匹配，见模式 4 决策）
- ❌ 复刻 Claude Code 的 renderToolUseMessage UI 钩子（前端已有通用 `tool_activity`）
- ❌ Swarm / Coordinator 完整团队协作层（先稳住 Subagent 隔离）
- ❌ 重写 MCP server 协议或把全部 builtin 包装成 MCP
- ❌ 完整 undo / 事务回滚栈（当前仅 `soft_exec` 预览跳过破坏性工具）
- ~~❌ Eval / 多 worker Redis pubsub~~ → **已落地最小版**（见「落地状态」）

---

## 决策驱动变量

| 变量 | 类别 | 取值 | 来源 |
|---|---|---|---|
| **D1** 对标姿态 | driver | **选择性吸收**，不克隆 Claude Code | 项目已有 Ports/Adapters + AsyncGenerator；全量对齐会毁掉已有记忆/HITL/SSE 投资 |
| **D2** Transcript 形态 | driver | **保留 SQL/Redis**，演进为 append-only 事件语义；**不做本地 JSONL** | 多租户、JWT、`ChatRunRow` 已存在；JSONL 适合单机 CLI，不适合本项目 |
| **D3** Tool 元数据扩展方式 | driver | **一等字段**加到 `ToolDefinition`（非塞进 `metadata` dict） | 并发调度是框架硬逻辑，dict 键会被偷懒忽略；与 2026-06-17 卡「timeout 进 metadata」不同——那是可选预算，这是安全默认 |
| **D4** Fail-closed 默认 | driver | `is_concurrency_safe=False`，`is_readonly=False`；`is_destructive` 默认 False 但写工具显式 True | 对齐博客「默认不安全强迫声明」；比「默认安全」更防偷懒 |
| **D5** 切片顺序 | driver | **S0 并发分区 → S1 Compact 回注 → S2 Schema 校验 → S3 Toolset → S4 Transcript 事件化** | 并发写是数据损坏（最高）；compact 是静默丢上下文；其余增强 |
| **D6** HITL 配置接线 | driver | S2 附带：`hitl.require_tool_approval` 真正门控 `requires_confirmation` | 配置与 `tool_loop` 脱节已是已知债（见项目事实） |
| **D7** 破坏性 API | driver | `ToolDefinition` 加字段带默认值 → **向后兼容**；调度行为变更属行为改，需 RED 测试 | 公开接口，不能硬切 |

> 任一 driver 被否决 → 回对应切片重展开，不影响其余切片前提。

---

## 项目事实（8 模式逐条对标）

> 下列「现状 / 评级」为设计时快照；**以文首「落地状态」与附录对齐条为准**。

### 总览矩阵

| # | 模式 | 现状（设计时） | 评级 | 本轮动作 |
|---|---|---|---|---|
| 1 | Agent 核心循环 = AsyncGenerator | `ChatPipeline.stream` / `ToolLoopUseCase.execute_stream_with_tools` 已是 `AsyncIterator[StreamEvent]`；HTTP/SDK 共消费同一生成器 | ✅ 已对齐 | **保持，不改** |
| 2 | Tool = 协议对象（安全元数据） | 仅有 `name/description/parameters/requires_confirmation/metadata` | ❌ 缺口 | **S0 补字段 + 标注 builtin/MCP** |
| 3 | 工具执行 6 层管线 | 有 hook / HITL / policy / execute / wrap；**无前置 Schema 校验**；错误已回流模型 | ⚠️ 半齐 | **S2 补 Schema 层** |
| 4 | Transcript = Append-Only | Redis/DB **整表覆盖**；`_prepare_for_save` 剥 SYSTEM/TOOL/synthetic；`ChatRunRow` 是 run 级审计非 LLM transcript | ❌ 缺口 | **S4 事件化，不做 JSONL** |
| 5 | Compact + 状态重注入 | `HistoryCompactor` 有摘要；摘要标 `SYSTEM+synthetic` → **被 `_prepare_for_save` 丢掉**；跨轮靠 Tier-2 机会召回 | ⚠️ 有 bug | **S1 修根因** |
| 6 | Registry / Toolset / Model Tools | Registry+Composite 有；`_build_tool_definitions` 有；**无 Toolset 层**（仅有 `allowed_tools` 过滤） | ⚠️ 半齐 | **S3 引入 Toolset** |
| 7 | 声明式并发分区 | 同轮 `asyncio.gather` **全并行**；无 `isConcurrencySafe` / path-scoped | ❌ 缺口 | **S0 实现 partition** |
| 8 | Multi-Agent 隔离 | `ParallelAgentTool` 独立 SessionState+预算；`WorkflowClient` **共享 session** | ⚠️ 半齐 | **S3/后续修 Workflow 隔离** |

### 模式 1 — 已对齐（证据）

- `src/astracore/modules/chat/pipeline.py` → `ChatPipeline.stream`：`AsyncIterator[StreamEvent]`
- `src/astracore/modules/chat/application/tool_loop.py` → `execute_stream_with_tools`：内部 `while` + `yield`
- HTTP：`execute_run_loop` → `_broadcast_run_event` → SSE
- SDK：`AstraCoreClient.chat_stream` 直接 `async for pipeline.stream`

**判断**：博客最核心的架构决策你们已经做对了。第一版 weiyige 的「while+回调耦合」问题，AstraCore **不存在**。不要为了对齐博客再动这里。

### 模式 2 — Tool 协议缺口（证据）

`src/astracore/modules/tools/ports/tool.py` → `ToolDefinition`：

```python
name, description, parameters, requires_confirmation=False, metadata={}
```

缺失：`is_concurrency_safe` / `is_readonly` / `is_destructive` / `check_permissions` / `validate_input`。

已有近似：`requires_confirmation`（HITL）、`metadata.timeout_s` / `max_output_chars`、`ReadTrackedToolAdapter`（read-before-edit）。

### 模式 3 — 管线半齐（证据）

实际层序（`tool_loop._execute_one_tool`）：

1. LLM 参数 JSON 修复（adapter 层）
2. `before_tool` Hook（可 ShortCircuit）
3. HITL `requires_confirmation`
4. `PolicyEngine.check_security_policy`
5. `ReadTrackedToolAdapter` 业务装饰
6. `adapter.execute` + `after_tool` + `wrap_external`

**缺**：执行前 JSON Schema / 必填校验层。  
**已对齐**：异常 → `ToolExecutionResult(ok=False)` → 回流模型，不崩 loop。

配置债：`hitl.require_tool_approval` 文档声称门控确认工具，但 `tool_loop` 只看 `defn.requires_confirmation` + `hitl_callback is not None`；无 callback 时确认工具**静默执行**（fail-open）。

### 模式 4 — 非 Append-Only（证据）

| 层 | 形态 | 问题 |
|---|---|---|
| 短期上下文 | Redis SET + `ChatSessionRow.messages` 整表覆盖 | 崩溃窗口丢最后一写；非 append |
| 保存过滤 | `_prepare_for_save` 丢 SYSTEM/TOOL/synthetic | 工具轨迹不进 LLM 历史；compact 摘要也丢 |
| Run 审计 | `ChatRunRow`（user/assistant/thinking/tool_activity） | 有审计价值，但不是可重建 LLM messages 的 transcript |

### 模式 5 — Compact 有实现、回注有 bug（证据）

- `HistoryCompactor.maybe_compact`：摘要 → `Message(role=SYSTEM, metadata={synthetic, compacted})`
- `pipeline._prepare_for_save`：丢弃全部 SYSTEM / synthetic
- 摘要虽写 `MemoryType.SUMMARY`，下一轮短期历史**不保证**带上摘要

**根因一句话**：压缩把摘要放进了「保存时必删」的消息类别，导致跨轮 reinject 失效。

### 模式 6 — 缺 Toolset（证据）

- Registry：`NativeToolAdapter` + `MCPToolAdapter` + `CompositeToolAdapter`
- 过滤：`allowed_tools: frozenset`（`chat_context.py` / `tool_loop`）——能力有，但不是一等 Toolset 概念
- Model 投影：`_build_tool_definitions()`
- builtin 约 14 个 `register_tool` + MCP 动态发现 → 容易「超市迷路」

### 模式 7 — 全并行（证据）

```
tool_loop.py:665  asyncio.gather(*[_execute_one_tool(tc) for tc in response.tool_calls])
tool_loop.py:813  asyncio.create_task(...)  # 流式路径同样全并行
```

`ReadTrackedToolAdapter.execute_parallel` 故意串行，但 **tool_loop 不走 adapter.execute_parallel**，走自己的 gather → 读追踪的串行保护被绕过。

### 模式 8 — Subagent 好、Workflow 差（证据）

- 好：`ParallelAgentTool` — 新 `SessionState`、独立 `ToolLoopUseCase`、`_WORKER_MAX_ITERATIONS=15`、剥 `spawn_agents`
- 差：`WorkflowClient.run` — 多 task 共用 `workflow_session_id`，前序结果拼进下一 task，**共享短期记忆**

---

## 档位

- **选定**：设计卡 + large
- **理由**：改 `ToolDefinition` 公开接口；改工具调度语义（行为可见）；跨 chat/tools/memory/agent；需 Walking Skeleton + 垂直切片

## diff 预算（总量级，按切片再拆）

| 切片 | 文件数 | 行数 |
|---|---|---|
| S0 并发分区 | ~8–12 | ~400–700 |
| S1 Compact 回注 | ~4–6 | ~150–300 |
| S2 Schema + HITL 接线 | ~6–10 | ~300–500 |
| S3 Toolset | ~8–12 | ~400–600 |
| S4 Transcript 事件化 | ~10–15 | ~600–1000 |
| **合计** | **~40–55** | **~2k–3k** |

触及未列模块（frontend 大改、MCP 协议重写、Eval 框架）→ 停下回 planning。

---

## 代码级约束（命中项）

| 类 | 约束 | 检查方式 |
|---|---|---|
| **并发** | 含写工具的同轮调用不得与任意工具并行；只读且 `is_concurrency_safe` 才可并行 | RED：`test_partition_read_write_batches` |
| **可靠** | Compact 摘要必须出现在下一轮 `prepare→stream` 的 messages 前缀 | RED：compact 后 save/load，断言含摘要内容 |
| **安全** | 新字段默认 fail-closed；MCP 发现工具默认 `is_concurrency_safe=False` | 单测断言默认值；MCP 工具不显式声明则串行 |
| **兼容** | 旧 `register_tool(...)` 不传新字段仍可注册 | 现有 `make test` 全绿；新增字段有默认 |
| **可维护** | 分区逻辑只在 `tool_loop` 一处；禁止各工具内 if-else 并发 | grep `asyncio.gather` 在 tool 执行路径只出现在 partition 之后 |

---

## 子模式展开

### 候选方案（3 个）

#### 候选 A — 选择性吸收（推荐）

- **核心思路**：保持 AsyncGenerator + Ports；只补并发元数据/分区、Compact 回注、Schema 校验、Toolset；Transcript 在现有 SQL 上做 append 语义，不迁 JSONL。
- **适用前提**：继续做多用户 HTTP/SDK 产品，不是单机 CLI。
- **改动范围**：`tool.py`、`tool_loop.py`、`builtin.py`、`compactor.py`、`pipeline._prepare_for_save`、新 `toolset.py`、可选 `transcript` 事件表。
- **复用**：`allowed_tools`、`ChatRunRow.tool_activity`、`HistoryCompactor`、`ParallelAgentTool`、`HookRegistry`。
- **Pros**：风险可控；对齐真实痛点；与 2026-06-17 卡不冲突。
- **Cons**：不「看起来像 Claude Code」；Transcript 完整度分阶段才到位。
- **影响范围**：跨模块 + 公开接口（兼容扩展）。
- **diff**：~2–3k 行 / 5 切片。

#### 候选 B — 全量 Claude Code 化

- **核心思路**：本地 JSONL transcript、完整 Tool 协议含 render hooks、Coordinator/Swarm。
- **适用前提**：产品形态变成单机 coding agent CLI。
- **Pros**：叙事完整；审计天生。
- **Cons**：与 JWT/多租户/Redis/SSE 架构冲突；diff 爆炸；毁掉已有记忆双轨。
- **影响范围**：公开接口 + 持久化格式重写。
- **diff**：5k+ 行，多季度。

#### 候选 C — 只修 Compact bug + 文档对齐

- **核心思路**：只修 `_prepare_for_save` / 摘要角色；其余写进专业度路线「以后再说」。
- **Pros**：最小 diff。
- **Cons**：文件并发写乱序仍在；工具超市问题仍在；对标博客后最危险的洞不堵。
- **影响范围**：局部。
- **diff**：<300 行。

### Decision Drivers 评分

| Decision Driver | 权重 | A 选择性 | B 全量克隆 | C 最小修补 |
|---|---|---|---|---|
| 堵住数据损坏/上下文丢失 | High | 5 | 5 | 2 |
| 与现有 Ports/SSE/记忆架构契合 | High | 5 | 1 | 5 |
| diff 可控 / 可分片交付 | High | 4 | 1 | 5 |
| 多租户 SaaS 适配 | High | 5 | 1 | 5 |
| 长期可演进到更强审计 | Medium | 4 | 5 | 1 |
| **加权印象** | — | **强推** | 否 | 不够 |

### 推荐

**选 A。**  
理由：模式 1 已对齐，不必为叙事重写；模式 7/2 是生产数据损坏风险；模式 5 是已存在的确定性 bug；模式 4 的 JSONL 是 CLI 解，AstraCore 该做「SQL 上的 append-only 事件语义」。B 是换产品形态；C 是装瞎。

---

## Walking Skeleton + 垂直切片

### S0 — Walking Skeleton：声明式并发分区（端到端最薄）

| 字段 | 内容 |
|---|---|
| **范围** | `ToolDefinition` 加 3 字段 → `partition_tool_calls()` → `tool_loop` 流式/非流式改用分区 → builtin 给 FS/memory 写工具打标 → 1 个 RED 测试 |
| **占位** | path-scoped 细粒度冲突检测**不做**（整批按 `is_concurrency_safe`）；MCP 工具一律默认串行；render hooks 不做 |
| **diff** | ≤12 文件 / ≤700 行 |
| **验证** | `hatch run pytest tests/modules/chat/test_tool_partition.py -v` 绿；现有 tool_loop 测试绿 |
| **完成标志** | 模拟 `[read, read, write, read]` 得到 3 个 batch；write 与任何工具不同批并行 |

**结构改（先 commit）**：

1. `ToolDefinition` 增加：
   - `is_concurrency_safe: bool = False`
   - `is_readonly: bool = False`
   - `is_destructive: bool = False`
2. `MutableToolAdapter.register_tool` / Native / Composite / SDK 透传新参数（默认值保持兼容）
3. 新增 `src/astracore/modules/tools/application/partition.py`：`partition_tool_calls(defs, calls) -> list[list[ToolCall]]`

**算法（与博客一致，可测）**：

```
扫描 calls 顺序：
  - 连续 is_concurrency_safe==True 的 call 收进同一并行 batch
  - 遇到 False → 先 flush 并行 batch，再单独串行 batch
```

**行为改（后 commit）**：

1. `tool_loop` 非流式 / 流式：对每个 batch，safe batch 用 `gather`，unsafe batch 顺序 `await`
2. builtin 标注示例：
   - 只读检索类 → `is_readonly=True, is_concurrency_safe=True`
   - `delete_memory` / 调度取消更新 → `is_destructive=True, is_concurrency_safe=False`
   - MCP filesystem write / shell → 默认 False（不改 MCP 协议，靠默认）
3. `ReadTrackedToolAdapter`：文档标明 tool_loop 不再绕过串行语义

### S1 — Compact 摘要确定性回注

| 字段 | 内容 |
|---|---|
| **范围** | 改摘要消息形态 + `_prepare_for_save` 白名单 + 可选 load 时注入最新 SUMMARY |
| **根因** | 摘要用了 SYSTEM+synthetic，而保存路径删除这两类 |
| **修复方向** | 摘要改为 **USER 角色的合成消息对**（或 ASSISTANT 占位 + USER 摘要），`metadata={"compacted": True, "persist": True}`；`_prepare_for_save` 对 `compacted=True` **放行**；保持 `MemoryEngine.SUMMARY` 双写 |
| **状态重注入** | 摘要模板强制包含：任务进度 / 已确认决策 / pending 项（改 `_summarize` prompt） |
| **diff** | ≤6 文件 / ≤300 行 |
| **验证** | RED：触发 compact → save → load_short_term → 消息含 `【对话摘要】`；跨一轮 `prepare` 后 system/messages 仍可见摘要 |

**不选的做法**：改成 JSONL 侧链存摘要——过大，且不修短期上下文。

### S2 — Schema 校验层 + HITL 配置接线

| 字段 | 内容 |
|---|---|
| **范围** | 在 `_execute_one_tool` 最前增加 `validate_tool_arguments(defn, args)`；失败 → error tool_result；`require_tool_approval` 真正生效 |
| **层序目标** | Schema →（可选业务 validate）→ Hook → HITL → Policy → call |
| **HITL** | `requires_confirmation and (not hitl.enabled or not require_tool_approval)` 时的语义写进测试；无 callback 时：确认类工具返回「环境不支持审批」而非静默执行（fail-closed） |
| **diff** | ≤10 文件 / ≤500 行 |
| **验证** | 缺必填参数工具 → loop 继续且模型收到 Error；`require_tool_approval=false` 时确认工具不暂停 |

### S3 — Toolset 一等公民

| 字段 | 内容 |
|---|---|
| **范围** | 新 `Toolset`（名称 + tool name 集合 + 可选 profile）；`ChatContext` / `prepare` 绑定；`spawn_agents` worker 用子集 Toolset |
| **预设** | `default`（当前全量减去危险可选）、`readonly`、`memory_ops`、`scheduler_ops` |
| **与 skills** | Skill 加载不自动扩大 Toolset；扩大必须显式 |
| **diff** | ≤12 文件 / ≤600 行 |
| **验证** | readonly Toolset 下模型 schema 不含 write/delete；spawn worker 不含 `spawn_agents` |

### S4 — Transcript 事件化（非 JSONL 文件）

| 字段 | 内容 |
|---|---|
| **范围** | 引入 `TranscriptEvent` 表或扩展 `ChatRunRow` 为 append-only 事件流（user/assistant/tool_use/tool_result/compact）；短期上下文可由事件重建；保留 Redis 缓存作物化视图 |
| **明确不做** | 本地 `session.jsonl` 文件；替换 MemoryEngine |
| **与 `_prepare_for_save`** | 物化视图仍可过滤；**事件流全量保留**工具轨迹供审计/恢复 |
| **diff** | ≤15 文件 / ≤1000 行 |
| **验证** | kill 进程后按 conversation_id replay 事件 → 与崩溃前 messages 一致（除未 fsync 最后一条） |

**Workflow 隔离**（可挂 S3 尾或 S4）：`WorkflowClient` 每 task 新 `session_id`，只把结构化结果回传，禁止共享 short-term。

---

## 结构改 / 行为改拆分（Tidy First）

每切片固定顺序：

1. **结构改 commit**：加字段 / 加 `partition.py` / 加 Toolset 类型 / 加事件模型——行为与旧版一致（新字段默认值使调度仍全并行？**否**：S0 结构改只加字段与纯函数，**不改 gather**；下一 commit 再改调度）
2. **RED 测试 commit**
3. **行为改 commit**：接入调度 / 改 save 过滤 / 接线 HITL
4. **测试全绿 + `make check`**

---

## 调用方影响（公开接口）

`ToolDefinition` / `register_tool` grep 影响面：

| 文件 | 影响 |
|---|---|
| `modules/tools/ports/tool.py` | 定义处 |
| `infrastructure/tools/native.py` | 透传 |
| `infrastructure/tools/composite.py` | 透传 |
| `infrastructure/tools/mcp.py` | 发现时填默认 fail-closed |
| `modules/tools/builtin.py` | 逐工具标注 |
| `sdk/client.py` → `register_tool` | 增加可选 kwargs |
| `modules/chat/application/tool_loop.py` | 读字段做分区 |
| 测试：`tests/modules/tools/*`, `tests/modules/chat/test_tool_loop*` | 断言更新 |

抽样 3 个调用方：

1. `builtin.py` `delete_memory` — 加 `is_destructive=True`
2. `mcp.py` list_tools — 不传则默认不可并发（正确）
3. `sdk/client.py` register_tool — 旧调用方不传新参，行为在 S0 行为改后变为「默认串行」，属**有意收紧**

**兼容策略**：API 兼容（可编译/可注册）；调度语义在 S0 行为改后收紧——changelog 写明「未声明 concurrency_safe 的工具改为串行」。

---

## 失败模式与验证

| ID | 失败模式 | 级别 | 验证项 |
|---|---|---|---|
| F1 | 分区算法把 write 放进并行 batch | High | RED：`partition_tool_calls` 单测固定序列 |
| F2 | Compact 修了 save 但仍用 SYSTEM，被其他过滤路径丢掉 | High | 集成：save→load 全文断言 |
| F3 | Schema 校验过严，可选参数/MCP 额外字段全拒绝 | Medium | MCP 工具带 unknown field 仍执行（允许 additionalProperties） |
| F4 | Toolset 过小导致主聊天缺 `ask_user`/`load_skill` | Medium | default Toolset 快照测试含核心工具名集合 |
| F5 | S4 事件流与 Redis 物化视图不一致 | High | replay 测试 + 双读对比 |
| F6 | 默认全串行导致延迟上升被误认为回归 | Low | 基准：纯 readonly 多 call 仍并行；日志打 batch 信息 |

---

## 推荐与决策

### 推荐

按 **候选 A**、切片顺序 **S0 → S1 → S2 → S3 → S4** 推进。

### 为什么是这个顺序

1. **S0**：并行写文件/记忆是唯一「会弄脏用户数据」的洞，且改动局部、可测  
2. **S1**：已存在的确定性 bug，修完立刻改善长对话  
3. **S2**：提升 Agent 自愈；顺手修 HITL fail-open  
4. **S3**：降工具超市误选，服务 multi-agent  
5. **S4**：最大、最贵，放最后；前面不依赖完整 transcript

### 为什么不选其他

- **B**：把 SaaS 后端改成 CLI 存储模型，得不偿失  
- **C**：留下最高危并发问题

### 下一步实施边界（building 契约）

**做**：

- 仅 S0 开工前再开 implementation plan（`writing-plans`）
- 每切片独立 PR / 独立 `make test` + `make check`

**不做**：

- 不改 `ChatPipeline.stream` 的 AsyncGenerator 形态  
- 不引入本地 JSONL  
- 不在本卡做 Eval / 多 worker  
- 不改前端视觉体系（除非 HITL 文案）

### 重评估条件

- 产品确认要做「单机 CLI 优先」→ 重开 D2，考虑 JSONL sidechain  
- builtin+MCP 工具数稳定 >30 且误选率可测 → 提前 S3  
- 出现监管/审计硬需求 → 提前 S4

---

## 决策记录

| 项 | 内容 |
|---|---|
| **标题** | AstraCore 对标 Claude Code 8 模式：选择性吸收 |
| **日期** | 2026-08-04 |
| **上下文** | 博客 8 模式有源码级洞见；项目已有 AsyncGenerator 循环，不宜推倒重来 |
| **Drivers** | 数据安全 > 架构契合 > 可分片 > 多租户适配 |
| **候选** | A 选择性 / B 全量克隆 / C 最小修补 |
| **决策** | A；S0 并发分区为 Walking Skeleton |
| **正面后果** | 堵住写工具乱序；修好 compact 丢摘要；工具协议可演进 |
| **负面后果** | 未声明安全的工具变串行 → 部分场景变慢；需逐工具标注 |
| **重评估** | 见上节 |

---

## 附录：与博客「6 个坑」对照（本项目）

| 坑 | AstraCore 现状（收口后） | 动作 |
|---|---|---|
| 先做框架不做事 | 已有可用产品，不适用 | 无 |
| 上下文事后才想 | Compact 回注已修；transcript replay 可重建 | 保持回归测试 |
| 工具超市迷路 | Toolset 裁剪已落地 | 场景化继续扩集合 |
| 无后悔药 | HITL + `soft_exec` 预览；无完整 undo | 另案（事务/快照） |
| Prompt 当架构 | 核心在代码；skill/HITL 指南在 prompt | 保持 |
| 设计/迭代失衡 | 循环已统一；pipeline 编排仍可再瘦 | 按需拆 `application/` |

---

## 附录：模式对齐一览（收口后）

```
模式1 AsyncGenerator     ████████████  100%  不动
模式2 Tool 协议          ██████████░░   85%  安全字段+标注
模式3 执行管线           ███████████░   90%  Schema+HITL+soft_exec
模式4 Append transcript  █████████░░░   80%  SQL 事件+replay
模式5 Compact+Reinjection ██████████░░  85%  compacted 回注
模式6 三层工具           █████████░░░   80%  Toolset
模式7 声明式并发         █████████░░░   85%  safe+path-scoped
模式8 Multi-Agent        ███████░░░░░   65%  worker toolset/会话隔离续作
```
