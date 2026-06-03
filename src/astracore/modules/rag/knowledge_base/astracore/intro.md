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
