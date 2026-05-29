---
title: AstraCoreAI 对话流程与工具循环
category: astracore
tags: [ChatPipeline, ToolLoop, SSE, 流式输出, Command-Pipeline, 工具循环]
related: [astracore/intro, astracore/tool_system, astracore/skill_system]
---

# AstraCoreAI 对话流程与工具循环

## ChatPipeline：Command + Pipeline 模式

AstraCoreAI 的对话流程分为两个严格分离的阶段：

### prepare 阶段（决策）

`ChatPipeline.prepare()` 完成所有数据库查询和参数决策，返回不可变的 `ChatContext`：

- 加载用户设置（温度、Token 预算、上下文长度）
- 加载并路由技能（Skill），决策系统提示内容
- 决策模式：`normal`（直接 LLM）或 `tool_loop`（工具循环）
- 加载 LLM Profile 和工具列表

### stream 阶段（执行）

`ChatPipeline.stream()` 纯执行，不访问数据库：

- 每轮始终注入系统提示
- SSE 事件实时透传给前端（不缓冲）
- 工具调用结果通过 asyncio.Queue 并行执行后写回

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
| `skill_match` | 技能路由命中 |
| `agent_start/done` | 子 Agent 启动/完成 |
| `done` | 本轮对话结束 |

## 后台 Run 与刷新恢复

对话生成由后台 Task 驱动（`_ActiveRun`），前端通过 SSE 订阅事件流。刷新页面后可重新订阅，生成不会中断。
