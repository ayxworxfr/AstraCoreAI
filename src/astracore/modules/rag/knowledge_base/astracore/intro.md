---
title: AstraCoreAI 框架介绍
category: astracore
tags: [AstraCoreAI, 框架, Clean Architecture, FastAPI, Ports-Adapters]
related: [astracore/chat_pipeline, astracore/skill_system, astracore/tool_system, astracore/memory_system]
---

# AstraCoreAI 框架介绍

**AstraCoreAI** 是一套基于 **Clean Architecture + Ports & Adapters** 构建的企业级 Python AI 框架，目标是让业务团队快速复用统一 AI 能力，同时保持框架核心不被具体 Provider 绑定。

## 核心能力

- **多 LLM 支持**：Anthropic Claude、OpenAI GPT，通过统一 `LLMAdapter` 接口切换，无需修改业务代码
- **工具调用（Tool Use）**：支持原生 Python 工具和 MCP 协议工具，多轮自动执行
- **技能系统（Skills）**：Claude 可按需加载的专业能力包（Agent Skills 标准），三层 System Prompt + Claude 自主路由，无需服务端路由引擎
- **知识库检索（RAG）**：ChromaDB 向量存储，语义检索自动注入对话上下文
- **记忆系统（Memory）**：三层记忆架构，LLM 自动抽取并持久化关键信息
- **并行多 Agent**：主 Agent 可分解任务并发调度多个子 Agent，实时流式进度
- **流式输出（SSE）**：全链路实时流式输出，思考块（Extended Thinking）实时可见
- **认证与授权**：JWT Bearer Token，注册/登录/鉴权；admin/user 双角色，首个注册用户自动成为管理员
- **HITL（人机协作）**：工具执行审批、记忆晋升审批、ask_user 主动询问，超时后自动继续，前端通过 QuestionCard 展示等待确认
- **Prompt 注入防御**：外部数据（RAG 召回内容、Tier-2 记忆、工具结果）统一用 `<external_data trust="untrusted">` 标签包裹，System Prompt 顶部含显式注入声明，防止不可信数据劫持指令
- **上下文压缩**：Token 级自动压缩（`HistoryCompactor`），触发阈值为 context_window 的 50%，由 LLM 生成摘要并经 MemoryEngine 持久化；压缩失败时自动回退到尾部裁剪

## 架构分层

```
前端（React + Ant Design）
       │
FastAPI HTTP + SSE
       │
ChatPipeline（prepare 决策 + stream 执行）
       │
ToolLoopUseCase（多轮工具执行）
       │
LLMAdapter ──── ToolAdapter ──── MemoryAdapter ──── RetrieverAdapter
(Anthropic/     (Native/MCP/     (Redis+SQLite)      (ChromaDB)
 OpenAI)         Parallel)
```

## 双形态

- **HTTP Service**：FastAPI + SSE，前端通过标准 API 交互
- **SDK 嵌入**：`AstraCoreClient`，直接在 Python 应用中调用

```python
from astracore.sdk import AstraCoreClient

async with AstraCoreClient() as client:
    async for event in client.chat_stream("你好，AstraCoreAI！"):
        print(event)
```

## 扩展知识库

新增知识库文档只需在 `modules/rag/knowledge_base/` 对应子目录放置 `.md` 文件，重启后自动写入向量数据库。
