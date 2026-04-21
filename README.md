# AstraCore AI

**企业级 Python AI 框架，基于 Clean Architecture 构建**

AstraCore AI 是一个生产级、可扩展的 AI 框架，基于 Clean Architecture + Ports & Adapters 原则构建。它为 LLM、工具执行、记忆管理、RAG 和多 Agent 编排提供统一接口。

## 特性

- **Clean Architecture**：Ports & Adapters 模式，Domain 层零外部依赖
- **多 Provider LLM 支持**：Anthropic Claude（流式 tool args 正确累积）、OpenAI GPT，易于扩展
- **工具执行**：原生工具并行/串行调用，带安全白名单与 XSS 检测
- **MCP 工具集成**：通过 fastmcp 接入任意 MCP 服务器（内置 filesystem、shell，支持自定义）
- **记忆系统**：Redis 短期（TTL 淘汰）+ SQLite 短期持久化（重启恢复）+ PostgreSQL 长期存储，Redis 不可用时自动降级到 SQLite
- **RAG 管道**：ChromaDB 向量搜索（幂等 upsert）、文档分块、引用支持
- **Skill 系统**：Skill 提示词管理（CRUD + 内置/自定义）、全局指令编辑、对话时动态切换激活 Skill
- **多 Agent 编排**：Planner/Executor/Reviewer 协作 + Workflow checkpoint 持久化
- **策略引擎**：tenacity retry + asyncio timeout 实际生效，Token 预算 O(n) 截断
- **双形态交付**：SDK 嵌入 + FastAPI 服务 HTTP 访问
- **前端 SPA 控制台**：React + Vite + Zustand 会话式 Playground，含 Skill 管理、RAG 调试、系统运行参数配置
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
    外部 APIs         Redis/SQLite        ChromaDB
```

## 快速开始

### 安装

```bash
# 使用 Hatch（推荐）
make setup

# 或手动
hatch env create
hatch run pip install -e ".[anthropic,openai,dev]"
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
# 启动 FastAPI 服务
make api

# 访问
# http://127.0.0.1:8000/docs
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
│   ├── tools/           # 工具执行与注册（native、MCP、composite）
│   ├── memory/          # HybridMemoryAdapter（Redis + SQLite 持久化）
│   ├── retrieval/       # ChromaDB 适配器（run_in_executor + upsert）
│   └── workflow/        # NativeWorkflowOrchestrator（Redis checkpoint）
├── mcp_servers/
│   └── shell_server.py  # 内置 MCP Shell Server（受控命令执行）
├── runtime/
│   ├── policy/          # PolicyEngine（tenacity retry + asyncio timeout）
│   ├── observability/   # 结构化日志、指标端口
│   └── security/        # SecurityValidator（XSS、长度、内容过滤）
├── service/
│   ├── api/             # FastAPI 路由（Chat、RAG、Skills、Settings、System）
│   └── middleware/      # HTTP 中间件
└── sdk/
    ├── client.py        # 主 SDK 客户端
    └── config.py        # Pydantic v2 SettingsConfigDict 配置

frontend/
├── src/app             # 应用入口与路由
├── src/pages           # Chat / RAG / Skills / System 页面
├── src/components      # 复用 UI 组件（chat / rag / skills / system）
├── src/stores          # Zustand（chatStore / skillStore / settingsStore）
└── src/services        # API、SSE、Skill 与系统信息通信
```

## 配置

创建 `.env` 文件（参考 `.env.example`）：

```bash
# LLM 配置（provider: anthropic | deepseek）
ASTRACORE__LLM__PROVIDER=anthropic
ASTRACORE__LLM__API_KEY=sk-ant-xxx
ASTRACORE__LLM__MODEL=claude-sonnet-4-6

# 记忆配置
ASTRACORE__MEMORY__REDIS_URL=redis://localhost:6379/0
ASTRACORE__MEMORY__DB_URL=sqlite+aiosqlite:///./astracore.db

# 检索配置
ASTRACORE__RETRIEVAL__COLLECTION_NAME=astracore
ASTRACORE__RETRIEVAL__PERSIST_DIRECTORY=./chroma_db

# CORS（生产环境）
ALLOWED_ORIGINS=http://localhost:5173,https://your-domain.com

# Tavily 联网搜索（可选）
TAVILY_API_KEY=tvly-xxx

# MCP 工具服务器（可选）
# filesystem — 读写本地文件，需 Node.js
# shell      — 在指定目录内执行受控 shell 命令
ASTRACORE__MCP__SERVERS='[{"type":"filesystem","paths":["D:/project"]},{"type":"shell","allow_dirs":["D:/project"]}]'
```

### MCP 服务器类型

| type | 说明 | 必填字段 |
|------|------|---------|
| `filesystem` | @modelcontextprotocol/server-filesystem，需 Node.js | `paths: list[str]` |
| `shell` | 内置受控 shell server | `allow_dirs: list[str]`，`timeout: float`（默认 30s） |
| `custom` | 任意外部 MCP 进程 | `name`, `command`, `args`, `env` |

## 开发

```bash
make setup        # 一键初始化环境
make api          # 启动后端服务（http://127.0.0.1:8000）
make fe-dev       # 启动前端服务（http://127.0.0.1:5173）
make test         # 运行测试
make test-cov     # 运行测试覆盖率
make lint         # ruff 检查
make type-check   # mypy 类型检查
make fmt          # 代码格式化
make clean        # 清理缓存
make clean-rag    # 清空 ChromaDB 数据
```

## 示例

- **基础对话**：`examples/basic_chat.py`
- **RAG 管道**：`examples/rag_example.py`
- **工具调用**：`examples/tool_calling.py`
- **多 Agent**：`examples/multi_agent.py`
- **服务运行**：`examples/run_service.py`
- **前端调试台**：`frontend/`

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
- **Web 框架**：FastAPI + uvicorn
- **数据验证**：Pydantic 2.x（SettingsConfigDict + discriminated union）
- **LLM Providers**：Anthropic Claude、OpenAI
- **MCP**：fastmcp（Model Context Protocol 工具集成）
- **存储**：Redis（短期记忆）、SQLite/aiosqlite（持久化）、PostgreSQL/asyncpg（长期存储）
- **向量数据库**：ChromaDB
- **策略**：tenacity、asyncio
- **测试**：pytest-asyncio（auto mode）、unittest.mock
- **前端**：React + Vite + TypeScript + Zustand

## 里程碑

- [x] M1：核心协议与最小 Provider + Tool 闭环
- [x] M2：记忆、预算、策略、可观测性
- [x] M3：RAG 与多 Agent 协作
- [x] M4：SDK + Service 打包与示例
- [x] M5：质量闭环 — 后端优化 ✅ 单元测试 99 个 ✅ Skill 系统 ✅ 记忆持久化 ✅ 系统配置 ✅ MCP 工具集成 ✅
- [ ] M6：可靠性与安全 — 熔断器、API Key 鉴权、限流
- [ ] M7：可观测与性能 — SLO/指标/压测基线
- [ ] M8：发布工程化 — 版本策略、回滚预案、运维文档

## 文件统计

- **55 个 Python 源模块**：覆盖 Domain / Application / Ports / Adapters / Runtime / Service / SDK 全栈
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
