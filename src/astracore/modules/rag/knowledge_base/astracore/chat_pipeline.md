---
title: AstraCoreAI 对话流程与工具循环
category: astracore
tags: [ChatPipeline, ToolLoop, SSE, 流式输出, Command-Pipeline, 工具循环, HITL, HistoryCompactor, Prompt注入防御]
related: [astracore/intro, astracore/tool_system, astracore/skill_system]
---

# AstraCoreAI 对话流程与工具循环

## ChatPipeline：Command + Pipeline 模式

AstraCoreAI 的对话流程分为两个严格分离的阶段：

### prepare 阶段（决策）

`ChatPipeline.prepare()` 完成所有数据库查询和参数决策，返回不可变的 `ChatContext`：

- 加载用户设置（Token 预算、上下文长度）
- 加载并路由技能（Skill），决策系统提示内容
- 决策模式：`normal`（直接 LLM）或 `tool_loop`（工具循环）
- 加载 LLM Profile 和工具列表
- **加载附件字节**：`ChatOptions.attachments` 中每个 `AttachmentRef` 对应的图片/PDF 文件在此阶段读取，能力检查（`caps.vision`）也在此完成

### stream 阶段（执行）

`ChatPipeline.stream()` 纯执行，不访问数据库：

- 入口处调用 `maybe_compact()` 进行上下文压缩检测（见下文 HistoryCompactor）
- 接受可选的 `hitl_callback` 参数，支持工具审批、记忆晋升审批等 HITL 场景
- 每轮始终注入系统提示
- SSE 事件实时透传给前端（不缓冲）
- 工具调用结果通过 asyncio.Queue 并行执行后写回

## HistoryCompactor：上下文自动压缩

当对话历史过长时，`HistoryCompactor` 自动触发压缩，避免超出模型上下文窗口限制：

- **context_window**：由 `policy.compaction.context_window_tokens` 配置（默认 100,000，预留输出空间）
- **触发条件**：估算 token 数超过 `context_window_tokens × trigger_ratio`（默认 0.5，即 50,000 tokens）
- **压缩方式**：调用 LLM 对最旧的 `compact_batch_ratio` 比例消息生成结构化摘要，通过 MemoryEngine 持久化为 `summary` 类型记忆
- **失败回退**：LLM 压缩失败时自动回退到尾部裁剪（保留最近 `default_max_messages` 或用户 `context_max_messages`），确保系统不中断

`stream()` 在每次生成前调用 `maybe_compact()`，只有估算 token 数超过阈值时才实际执行压缩操作。

## Prompt 注入防御

`build_system_prompt` 在 System Prompt 顶部注入 `injection_guard` 声明，明确告知 LLM 哪些内容来自外部不可信源：

- **RAG 内容**：检索到的知识库文档通过 `wrap_external(source="rag")` 包裹
- **Tier-2 记忆**：`build_turn_context()` 返回的 `turn_context` 通过 `wrap_external(source="memory")` 包裹
- **工具结果**：工具返回内容经 `wrap_external(source="tool")` 包裹后注入对话

包裹格式：

```xml
<external_data trust="untrusted" source="rag">
...检索到的文档内容...
</external_data>
```

LLM 被显式指令要求：不得将 `<external_data>` 内的任何内容视为系统指令，仅作为数据参考。

## HITL（人机协作）集成

`stream()` 接受 `hitl_callback` 参数，在需要人工确认时暂停执行：

- **工具审批**：`requires_confirmation=True` 的工具执行前发送 `TOOL_APPROVAL_PENDING` 事件，等待用户通过前端 QuestionCard 确认；超时则自动继续执行
- **记忆晋升审批**：session 记忆晋升至 user/project scope 前发送审批请求（需 `require_memory_promotion_approval=true`）
- **ask_user**：LLM 可主动调用 `ask_user` 工具向用户提问，等待用户回复后继续

## Debug 模式

当配置 `debug.log_prompts: true` 时，`build_system_prompt` 将完整 prompt 内容打印到日志，便于调试系统提示组装结果。

## ToolLoopUseCase：多轮工具执行

工具循环支持最多 `agent.max_tool_iterations` 轮（默认 10 轮）：

```
第 1 轮：LLM 生成回答或工具调用
  ├─ 无工具调用 → 结束，返回文本
  └─ 有工具调用 → 并行执行所有工具
        │
第 2 轮：LLM 读取工具结果，继续生成
  └─ ...（最多 10 轮）
```

### 工具 JSON 解析失败的处理

若 LLM 输出的工具参数 JSON 格式错误：

1. 先用 `json-repair` 库尝试自动修复
2. 修复失败则生成 `TOOL_CALL_ERROR` 事件
3. 注入 `is_error=True` 工具结果，LLM 在下一轮自我修正
4. 流式输出全程不被阻塞

## SSE 事件类型

| 事件 | 说明 |
|------|------|
| `text_delta` | LLM 文本增量 |
| `thinking_delta` | 思考块增量（Extended Thinking） |
| `tool_call` | LLM 决定调用工具 |
| `tool_result` | 工具执行完毕，含结果 |
| `tool_call_error` | 工具参数解析失败 |
| `tool_approval_pending` | HITL 工具审批等待中（前端展示 QuestionCard） |
| `skill_match` | 技能路由命中 |
| `skill_reminder` | 活跃技能提醒（提示 Claude 每轮须重新调用 load_skill） |
| `agent_start/done` | 子 Agent 启动/完成 |
| `done` | 本轮对话结束 |

## 后台 Run 与刷新恢复

对话生成由后台 Task 驱动（`_ActiveRun`），前端通过 SSE 订阅事件流。刷新页面后可重新订阅，生成不会中断。
