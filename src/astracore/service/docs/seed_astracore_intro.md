---
title: AstraCore AI 框架介绍
---

# AstraCore AI 框架介绍

**AstraCore AI** 是一个基于**整洁架构（Clean Architecture）**原则构建的企业级 Python AI 框架。

## 核心特性

- 🤖 **多 LLM 支持**：Anthropic Claude、OpenAI GPT，通过适配器模式统一接口
- 🔍 **内置 RAG**：ChromaDB 向量检索，支持文档索引与语义搜索
- ⚡ **原生异步**：全链路 `asyncio`，高并发无阻塞
- 🧩 **插件化架构**：Port/Adapter 模式，底层实现可灵活替换
- 💾 **多种记忆**：支持 Redis、PostgreSQL 等持久化后端

## 架构分层

```
┌─────────────────────────────┐
│        前端 / API 层         │  FastAPI + React
├─────────────────────────────┤
│        应用服务层            │  ChatService, RAGPipeline
├─────────────────────────────┤
│        领域核心层            │  Domain Models, Ports
├─────────────────────────────┤
│        基础设施适配层         │  Anthropic, ChromaDB, Redis
└─────────────────────────────┘
```

## 快速开始

```python
from astracore.sdk import AstraCoreClient, AstraCoreConfig

config = AstraCoreConfig(...)
client = AstraCoreClient(config)
response = await client.chat("你好，AstraCore！")
```
