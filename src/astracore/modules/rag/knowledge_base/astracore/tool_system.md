---
title: AstraCoreAI 工具系统（Tools）
category: astracore
tags: [Tool, MCP, 工具调用, spawn_agents, 并行Agent, asyncio, HITL, 工具审批]
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
| `search_knowledge_base(query)` | 语义检索内置知识库，返回最相关文档片段 |
| `load_skill(skill_id)` | 加载 Skill 的完整 instructions、引用列表和脚本列表 |
| `get_skill_reference(skill_id, file)` | 读取 Skill references/ 目录下的参考文档内容 |
| `run_skill_script(skill_id, script, args)` | 在 Skill scripts/ 目录内安全执行脚本（防路径穿越，30s 超时） |
| `ask_user(question, options?)` | 主动向用户提问并等待回复（HITL inline question） |
| `spawn_agents(tasks)` | 并行启动 2-5 个子 Agent 执行子任务 |
| `recall_memory(query)` | 语义检索记忆库，返回相关记忆条目 |
| `save_memory(subject, content, type, scope)` | 手动创建一条记忆（LLM 直接写入，不经自动抽取） |
| `delete_memory(memory_id)` | 删除指定记忆（**已标记 `requires_confirmation=True`**，需 HITL 审批） |
| `compact_memory(session_id)` | 手动触发当前会话的历史压缩，生成摘要记忆 |

`spawn_agents` 可通过 `agent.enable_spawn_agents: false` 关闭。

## HITL 工具审批

标记了 `requires_confirmation=True` 的工具在执行前会暂停，触发人机协作流程：

1. 工具循环发送 `TOOL_APPROVAL_PENDING` SSE 事件，携带工具名称、参数预览
2. 前端展示 **QuestionCard**，用户可查看详情后点击「批准」或「拒绝」
3. 批准 → 继续执行工具；拒绝 → 注入拒绝错误结果，LLM 在下一轮感知并调整策略
4. 超时（默认 60s）→ 自动批准并继续执行

目前已标记 `requires_confirmation=True` 的工具：

| 工具 | 理由 |
|------|------|
| `delete_memory` | 删除记忆不可逆，需用户确认 |

如需对自定义工具启用审批，在注册时设置 `requires_confirmation=True` 即可。

## MCP 工具

通过 Model Context Protocol 接入外部工具，支持三类服务器：

| 类型 | 说明 |
|------|------|
| `filesystem` | 内置 Python filesystem server（`mcp_servers/filesystem_server.py`），无需 Node.js；提供 read_file / write_file / edit_file / search_files 等 10 个工具 |
| `shell` | 内置受控 shell server，在 allow_dirs 白名单内执行命令 |
| `custom` | 自定义外部 MCP 进程，需配置 name/command/args/env |

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
- **单工具超时**：`policy.timeout.tool_timeout_s`（默认 120s，0 = 不限制）
- **结果截断**：`agent.max_tool_result_chars` 防止超长输出撑爆上下文
- **JSON 修复**：工具参数解析失败时自动尝试修复，彻底失败则降级为错误结果让 LLM 自愈

## 自定义工具

实现 `ToolAdapter` 接口，在应用工厂中注册即可接入工具循环。
