# AstraCore AI

**企业级 Python AI 框架，基于 Clean Architecture 构建**

AstraCore AI 是一个生产级、可扩展的 AI 框架，基于 Clean Architecture + Ports & Adapters 原则构建。它为 LLM、工具执行、记忆管理、RAG 和多 Agent 编排提供统一接口。

## 特性

- **Clean Architecture**：Ports & Adapters 模式，Domain 层零外部依赖
- **多模型 Profile 支持**：通过 `config/config.yaml` 管理多个模型 profile，内置能力注册表自动推导 thinking/tools/temperature/anthropic_blocks
- **工具执行**：原生工具并行/串行调用，带安全白名单与 XSS 检测
- **MCP 工具集成**：通过 fastmcp 接入任意 MCP 服务器（内置 filesystem、shell，支持自定义）
- **健壮工具循环**：悬空 tool_use 清理、总结收尾兜底、空响应引导续接、单次工具超时隔离、中间轮旁白与最终答案自动分流
- **记忆系统**：Redis 短期（TTL 淘汰）+ SQLite 短期持久化（重启恢复）+ PostgreSQL 长期存储，Redis 不可用时自动降级到 SQLite
- **RAG 管道**：ChromaDB 向量搜索（幂等 upsert）、文档分块、引用支持
- **Skill 系统**：Skill 提示词管理（CRUD + 内置/自定义）、全局指令编辑、对话时动态切换激活 Skill
- **多 Agent 编排**：Planner/Executor/Reviewer 协作 + Workflow checkpoint 持久化
- **策略引擎**：tenacity retry + asyncio timeout 实际生效，Token 预算 O(n) 截断
- **双形态交付**：SDK 嵌入 + FastAPI 服务 HTTP 访问
- **前端 SPA 控制台**：React + Vite + Zustand 会话式 Playground，含模型 Profile 切换、Skill 管理、RAG 调试、系统运行参数配置
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
async def main():
    # 默认读取 config/config.yaml，并通过 .env 中的 api_key_env 解析密钥
    config = AstraCoreConfig()
    client = AstraCoreClient(config)

    # 简单对话
    response = await client.chat("你好，你是谁？", model_profile="claude-sonnet")
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
    ├── client.py              # 主 SDK 客户端
    ├── config.py              # Pydantic v2 YAML 配置模型
    └── model_capabilities.py  # 内置模型能力注册表

config/
├── config.yaml          # 本地开发结构化配置
├── config.example.yaml  # 示例配置
└── config.docker.yaml   # Docker 部署配置

frontend/
├── src/app             # 应用入口与路由
├── src/pages           # Chat / RAG / Skills / System 页面
├── src/components      # 复用 UI 组件（chat / rag / skills / system）
├── src/stores          # Zustand（chatStore / skillStore / settingsStore）
└── src/services        # API、SSE、Skill 与系统信息通信
```

## 配置

结构化配置放在 `config/config.yaml`（可从 `config/config.example.yaml` 复制），`.env` 只放密钥：

```yaml
llm:
  default_profile: claude-sonnet
  profiles:
    - id: claude-sonnet
      label: Claude Sonnet
      provider: anthropic
      base_url: https://api.anthropic.com
      api_key_env: ANTHROPIC_API_KEY
      model: claude-sonnet-4-6

mcp:
  servers:
    - type: filesystem
      paths:
        - D:/project
    - type: shell
      allow_dirs:
        - D:/project
```

```bash
# .env
ANTHROPIC_API_KEY=sk-ant-xxx
TAVILY_API_KEY=tvly-xxx
```

模型能力（工具调用、深度思考、temperature、Anthropic block 回放）由 `src/astracore/sdk/model_capabilities.py` 内置表自动推导。只有代理或新模型行为与内置表不一致时，才需要在 YAML 中写 `capabilities` 覆盖。

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
- **数据验证**：Pydantic 2.x（YAML 配置模型 + discriminated union）
- **LLM Providers**：Anthropic Messages 协议、OpenAI 兼容协议（DeepSeek/GLM 等可通过 profile 接入）
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
- [x] M5：质量闭环 — 后端优化 ✅ 单元测试 99 个 ✅ Skill 系统 ✅ 记忆持久化 ✅ 系统配置 ✅ MCP 工具集成 ✅ 工具循环健壮性 ✅
- [ ] M6：可靠性与安全 — 熔断器、API Key 鉴权、限流
- [ ] M7：可观测与性能 — SLO/指标/压测基线
- [ ] M8：发布工程化 — 版本策略、回滚预案、运维文档

## 文件统计

- **55 个 Python 源模块**：覆盖 Domain / Application / Ports / Adapters / Runtime / Service / SDK 全栈
- **测试覆盖**：覆盖配置、LLM 适配器、应用用例、RAG、工具循环、运行时策略等核心链路
- **4 个完整示例**：可直接运行的用例
- **双形态交付**：SDK + Service 可用

## 许可证

MIT

## 贡献

查看 [CONTRIBUTING.md](./docs/CONTRIBUTING.md) 了解开发指南。

## 设计文档

- [AstraCore AI 设计文档](./docs/AstraCoreAI设计文档.md)
- [开发进度规划](./docs/开发进度规划.md)
- [工具循环踩坑记录](./docs/工具循环踩坑记录.md)
