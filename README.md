# AstraCore AI

**企业级 Python AI 框架，基于 Clean Architecture 构建**

AstraCore AI 是一个生产级、可扩展的 AI 框架，基于 Clean Architecture + Ports & Adapters 原则构建。它为 LLM、工具执行、记忆管理、RAG 和多 Agent 编排提供统一接口。

## 特性

- **Clean Architecture**：Ports & Adapters 模式，Domain 层零外部依赖
- **多 Provider LLM 支持**：Anthropic Claude（流式 tool args 正确累积）、OpenAI GPT，易于扩展
- **工具执行**：并行/串行工具调用，带安全白名单与 XSS 检测
- **记忆系统**：Redis 短期（TTL 淘汰 + 容量上限）+ PostgreSQL 长期混合存储
- **RAG 管道**：ChromaDB 向量搜索（幂等 upsert）、文档分块、引用支持
- **多 Agent 编排**：Planner/Executor/Reviewer 协作 + Workflow checkpoint 持久化
- **策略引擎**：tenacity retry + asyncio timeout 实际生效，Token 预算 O(n) 截断
- **双形态交付**：SDK 嵌入 + FastAPI 服务 HTTP 访问
- **前端 SPA 控制台**：React + Vite + Zustand 会话式 Playground
- **安全基线**：CORS 环境变量白名单、输入验证预编译、敏感字段脱敏

## 测试状态

```
99 tests passed in 0.88s  ✅
ruff: 0 errors             ✅
```

覆盖 9 个核心模块：SessionState、PolicyEngine、SecurityValidator、RAGPipeline、ChatUseCase、ToolLoopUseCase、AnthropicAdapter、HybridMemoryAdapter、NativeWorkflowOrchestrator

## 架构

```
┌─────────────────────────────────────────────────────────┐
│                    客户端应用                            │
└────────────────┬────────────────────────┬───────────────┘
                 │                        │
          ┌──────▼──────┐         ┌──────▼──────┐
          │  SDK 客户端  │         │ FastAPI 服务│
          └──────┬──────┘         └──────┬──────┘
                 │                        │
                 └───────────┬────────────┘
                             │
                   ┌─────────▼─────────┐
                   │   应用层 (用例)    │
                   │   Use Cases       │
                   └─────────┬─────────┘
                             │
         ┌───────────────────┼───────────────────┐
         │                   │                   │
    ┌────▼─────┐      ┌─────▼──────┐     ┌─────▼─────┐
    │  策略    │      │   端口     │     │  运行时   │
    │  引擎    │      │ (适配器)   │     │ (可观测)  │
    └──────────┘      └─────┬──────┘     └───────────┘
                            │
          ┌─────────────────┼─────────────────┐
          │                 │                 │
     ┌────▼─────┐    ┌─────▼──────┐   ┌─────▼─────┐
     │   LLM    │    │   记忆     │   │   检索    │
     │  适配器  │    │  适配器    │   │  适配器   │
     └────┬─────┘    └─────┬──────┘   └─────┬─────┘
          │                │                 │
          ▼                ▼                 ▼
    外部 APIs         Redis/PG          ChromaDB
```

## 快速开始

### 安装

```bash
# 使用 Hatch（推荐）
hatch env create

# 或使用 pip
pip install -e ".[dev,anthropic,openai,vector]"
```

### 基础用法 - SDK

```python
import asyncio
from astracore.sdk import AstraCoreClient, AstraCoreConfig
from astracore.sdk.config import LLMConfig

async def main():
    config = AstraCoreConfig(
        llm=LLMConfig(
            provider="anthropic",
            api_key="your-api-key",
        )
    )

    client = AstraCoreClient(config)

    # 简单对话
    response = await client.chat("你好，你是谁？")
    print(response.content)

    # 流式对话
    async for event in client.chat_stream("讲一个故事"):
        if event.content:
            print(event.content, end="", flush=True)

asyncio.run(main())
```

### 基础用法 - 服务

```bash
# 运行 FastAPI 服务
python examples/run_service.py

# 访问 API（需设置 ASTRACORE__SERVICE__ALLOWED_ORIGINS）
curl -X POST http://localhost:8000/api/v1/chat \
  -H "Content-Type: application/json" \
  -d '{"message": "你好！", "stream": false}'
```

### 基础用法 - 前端 SPA

```bash
# 安装前端依赖
make fe-install

# 启动前端开发服务
make fe-dev

# 访问
# http://127.0.0.1:5173
```

## 项目结构

```
src/astracore/
├── core/
│   ├── domain/          # 纯领域模型（Session、Message、Agent、Workflow）
│   ├── application/     # 用例（Chat、RAG、ToolLoop、MultiAgent）
│   └── ports/           # 适配器接口（LLM、Memory、Retriever、Tool、Workflow）
├── adapters/
│   ├── llm/             # Anthropic（流式累积）、OpenAI 适配器
│   ├── tools/           # 工具执行与注册
│   ├── memory/          # HybridMemoryAdapter（Redis TTL + PostgreSQL 长期）
│   ├── retrieval/       # ChromaDB 适配器（run_in_executor + upsert）
│   └── workflow/        # NativeWorkflowOrchestrator（Redis checkpoint）
├── runtime/
│   ├── policy/          # PolicyEngine（tenacity retry + asyncio timeout）
│   ├── observability/   # 结构化日志、指标端口
│   └── security/        # SecurityValidator（XSS、长度、内容过滤）
├── service/
│   ├── api/             # FastAPI 路由（Chat、RAG、System）
│   └── middleware/      # HTTP 中间件
└── sdk/
    ├── client.py        # 主 SDK 客户端
    └── config.py        # Pydantic v2 SettingsConfigDict 配置

frontend/
├── src/app             # 应用入口与路由
├── src/pages           # Chat / RAG / System 页面
├── src/components      # 复用 UI 组件
├── src/stores          # Zustand 会话与系统状态
└── src/services        # API 与 SSE 通信
```

## 示例

- **基础对话**：`examples/basic_chat.py`
- **RAG 管道**：`examples/rag_example.py`
- **工具调用**：`examples/tool_calling.py`
- **多 Agent**：`examples/multi_agent.py`
- **服务运行**：`examples/run_service.py`
- **前端调试台**：`frontend/`

## 配置

创建 `.env` 文件：

```bash
# LLM 配置
ANTHROPIC_API_KEY=sk-ant-xxx
OPENAI_API_KEY=sk-xxx

# 记忆配置
ASTRACORE__MEMORY__REDIS_URL=redis://localhost:6379/0
ASTRACORE__MEMORY__POSTGRES_URL=postgresql+asyncpg://localhost/astracore

# 检索配置
ASTRACORE__RETRIEVAL__COLLECTION_NAME=astracore
ASTRACORE__RETRIEVAL__PERSIST_DIRECTORY=./chroma_db

# 安全配置（生产环境必填）
ASTRACORE__SERVICE__ALLOWED_ORIGINS=http://localhost:5173,https://your-domain.com
```

## 开发

```bash
# 安装开发依赖
hatch env create

# 运行测试
hatch run test

# 运行测试和覆盖率
hatch run test-cov

# 类型检查
hatch run type-check

# 代码检查（ruff，当前 0 errors）
hatch run lint

# 代码格式化
hatch run format
```

## 核心设计原则

1. **框架优先**：所有能力作为可复用接口暴露
2. **端口优先**：先定义契约，再实现
3. **策略集中化**：预算、重试、超时统一在策略引擎管理
4. **双形态交付**：SDK 和 Service 共享同一应用层
5. **可演进编排**：默认 Native，可适配 LangGraph

## 技术栈

- **语言**：Python 3.11+
- **项目管理**：Hatch
- **架构**：Clean Architecture + Ports & Adapters
- **Web 框架**：FastAPI
- **数据验证**：Pydantic 2.x（SettingsConfigDict + StrEnum）
- **LLM Providers**：Anthropic Claude、OpenAI
- **存储**：Redis（aioredis）、PostgreSQL（SQLAlchemy 2.x async）
- **向量数据库**：ChromaDB
- **策略**：tenacity、asyncio
- **测试**：pytest-asyncio（auto mode）、unittest.mock
- **前端**：React + Vite + TypeScript + Zustand

## 里程碑

- [x] M1：核心协议与最小 Provider + Tool 闭环
- [x] M2：记忆、预算、策略、可观测性
- [x] M3：RAG 与多 Agent 协作
- [x] M4：SDK + Service 打包与示例
- [ ] M5（进行中）：质量闭环 — 后端优化 ✅ 单元测试 99 个 ✅ 集成测试待建立
- [ ] M6：可靠性与安全 — 熔断器、API Key 鉴权、限流
- [ ] M7：可观测与性能 — SLO/指标/压测基线
- [ ] M8：发布工程化 — 版本策略、回滚预案、运维文档

## 文件统计

- **54 个 Python 源模块**：覆盖 Domain / Application / Ports / Adapters / Runtime / Service / SDK 全栈
- **10 个测试文件，99 个测试**：单元测试全部通过（0.88s）
- **4 个完整示例**：可直接运行的用例
- **双形态交付**：SDK + Service 可用

## 许可证

MIT

## 贡献

查看 [CONTRIBUTING.md](./docs/CONTRIBUTING.md) 了解开发指南。

## 设计文档

- [AstraCore AI 设计文档](./docs/AstraCoreAI设计文档.md)
- [开发进度规划](./docs/开发进度规划.md)
