---
title: AstraCoreAI 工具系统（Tools）
category: astracore
tags: [Tool, MCP, 工具调用, spawn_agents, 并行Agent, asyncio]
related: [astracore/intro, astracore/chat_pipeline, ai-basics/agent_intro]
---

# AstraCoreAI 工具系统（Tools）

AstraCoreAI 支持两类工具：原生 Python 工具和 MCP 协议工具，通过统一的 `ToolAdapter` 接口管理。

## 内置工具

| 工具 | 说明 |
|------|------|
| `get_current_time()` | 获取当前时间（含时区） |
| `calculate(expr)` | 安全数学求值（AST 白名单，无 eval） |
| `web_search(query)` | 联网搜索（Tavily 优先，降级 DuckDuckGo） |
| `get_skill_reference(skill_id, ref_title)` | 获取技能关联的参考资料 |
| `spawn_agents(tasks)` | 并行启动 2-5 个子 Agent 执行子任务 |

`spawn_agents` 可通过 `agent.enable_spawn_agents: false` 关闭。

## MCP 工具

通过 Model Context Protocol 接入外部工具，支持三类服务器：

| 类型 | 说明 |
|------|------|
| `filesystem` | 文件系统访问（@modelcontextprotocol/server-filesystem）|
| `shell` | 命令行执行（AstraCore 内置 shell server）|
| `custom` | 自定义外部 MCP 进程 |

MCP 服务器在 `config.yaml` 的 `mcp.servers` 中配置。

## 并行子 Agent（spawn_agents）

`spawn_agents` 工具允许主 Agent 将复杂任务分解并并发执行：

```
主 Agent
  ├─ spawn_agents([任务1, 任务2, 任务3])
  │     │
  │     ├─ Worker Agent 1（独立 LLM + Tool Loop）
  │     ├─ Worker Agent 2（独立 LLM + Tool Loop）
  │     └─ Worker Agent 3（独立 LLM + Tool Loop）
  │     └─ 汇聚结果 → 主 Agent 综合输出
```

每个 Worker 有独立的 SessionState，继承父 Agent 的 LLM profile，最多执行 5 轮工具调用。前端以折叠卡片形式展示各 Worker 实时进度。

## 工具执行策略

- **并行执行**：同一轮 LLM 调用的多个工具并发执行（asyncio.Queue）
- **单工具超时**：`agent.tool_timeout_s`（默认 120s）
- **结果截断**：`agent.max_tool_result_chars` 防止超长输出撑爆上下文
- **JSON 修复**：工具参数解析失败时自动尝试修复，彻底失败则降级为错误结果让 LLM 自愈

## 自定义工具

实现 `ToolAdapter` 接口，在应用工厂中注册即可接入工具循环。
