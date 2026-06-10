# AstraCore AI

**企业级 Python AI 框架，基于能力模块化 + Clean Architecture 构建**

AstraCore AI 是一个生产级、可扩展的 AI 框架，基于能力模块化、Clean Architecture 与 Ports & Adapters 原则构建。它为 LLM、工具执行、记忆管理、RAG 和多 Agent 编排提供统一接口。

## 特性

- **能力模块化架构**：后端按 `modules/<capability>` 组织，前端按 `features/<capability>` 组织，业务边界在目录结构中可见
- **Clean Architecture**：能力模块内部保留 domain / application / ports 分层，基础设施实现统一放在 `infrastructure/`
- **多模型 Profile 支持**：通过 `config/config.yaml` 管理多个模型 profile，内置能力注册表自动推导 thinking/tools/temperature/anthropic_blocks
- **工具执行**：原生工具并行/串行调用，带安全白名单与 XSS 检测
- **MCP 工具集成**：通过 fastmcp 接入任意 MCP 服务器（内置 filesystem、shell，支持自定义）
- **健壮工具循环**：悬空 tool_use 清理、总结收尾兜底、空响应引导续接、单次工具超时隔离、中间轮旁白与最终答案自动分流
- **后台 Chat Run**：流式回答由后端后台任务驱动，SSE 仅负责订阅输出；刷新页面不会中断生成，重连后可恢复当前 run
- **Command + Pipeline 执行引擎**：`ChatPipeline` 作为 SDK 与 HTTP Service 的统一 chat 管道；`prepare()` 一次性完成所有 DB 查询与决策，返回不可变 `ChatContext`；`stream()` 纯执行，system prompt 始终注入，无分支歧义
- **记忆系统**：Redis 短期缓存 + SQLite 持久化兜底（重启恢复）+ 结构化 Memory Store（默认 `astracore.db`），Redis 不可用时自动降级到 SQLite
- **RAG 管道**：ChromaDB 向量搜索（幂等 upsert）、文档分块、引用支持
- **Skill 系统（Agent Skills 标准）**：Skill 作为 Claude 可按需加载的专业能力包（SKILL.md 格式），兼容 Agent Skills 开放标准；三层 System Prompt（身份层 + Skill 摘要清单 + 动态上下文）；Claude 通过 `load_skill` / `get_skill_reference` / `run_skill_script` 三个工具自主决策何时加载哪个 Skill，工具循环始终激活；支持多目录扫描（`skills.extra_dirs`）
- **并行多 Agent**：`spawn_agents` 工具将任务分解为 2–5 个独立子任务，Worker Agent 并发执行，前端实时展示各 Agent 进度；可通过 `agent.enable_spawn_agents` 配置开关；Worker 自动使用用户当前选择的模型 profile
- **策略引擎**：tenacity retry + asyncio timeout 实际生效，Token 预算 O(n) 截断
- **双形态交付**：SDK 嵌入 + FastAPI 服务 HTTP 访问，两者共享同一 `ChatPipeline` 执行引擎
- **前端 SPA 控制台**：React + Vite + Zustand 会话式 Playground，含模型 Profile 切换、Skill 管理、RAG 调试、系统运行参数配置
- **Hook/Callback 系统**：`HookRegistry` 提供 `before_llm`、`after_llm`、`before_tool`、`after_tool` 四个切入点；列表链式执行，钩子返回修改值即替换原值，返回 None 则透传；每个钩子异常独立捕获不影响主流程；支持同步与 async 钩子
- **Hook 短路（ShortCircuit）**：`before_llm` / `before_tool` 钩子可返回 `ShortCircuit(result=...)` 直接跳过 LLM 调用或工具执行，用于缓存命中、mock 注入、guardrail 拦截
- **熔断器（CircuitBreaker）**：三态（closed / open / half_open）状态机，连续失败达阈值后快速拒绝请求，等待 `recovery_time_s` 后探测恢复；通过 `PolicyEngine(circuit_breaker=...)` 接入 LLM 调用链路
- **Structured Output**：`LLMAdapter.generate(response_format=MyModel)` 强制 LLM 输出 Pydantic 模型对应的 JSON；Anthropic 走 tool_use 技巧，OpenAI 走 `json_schema` 响应格式；`MemoryEngine` 记忆抽取已切换为结构化输出，不再依赖 json_repair 兜底
- **轻量级链路追踪**：`Tracer` 通过 `register_hooks(registry)` 自动接入 HookRegistry，以 `Span` 数据类记录 LLM 调用与工具调用的时延、状态、属性，结构化 JSON 写入 DEBUG 日志，无 OTel 依赖
- **DAG 工作流引擎**：`NativeWorkflowOrchestrator` 实现真正的 DAG 执行——Kahn 算法拓扑排序 → 按层 `asyncio.gather` 并行执行；`AgentTask.depends_on` 声明依赖，`condition` 字段支持对 `task_results` 求值的条件跳过（SKIPPED 状态）；`TaskExecutor` 可注入任意异步函数，SDK 中默认注入 `ChatPipeline.execute`
- **SDK WorkflowClient**：`client.workflow.run(name, tasks)` 一行启动 DAG 工作流；前序任务结果自动作为上下文注入后续任务提示；每个任务可通过 `metadata` 独立覆盖 `model_profile`、`use_tools` 等参数
- **Agent Eval 评估框架**：`EvalRunner` 并发执行 `EvalCase` 列表，支持 LLM-as-judge 相关性评分（0-1）与工具调用顺序精确匹配；`EvalReport.summary()` 一键打印通过率；`python -m astracore.eval --cases cases.json` CLI 直接运行
- **安全基线**：CORS 环境变量白名单、输入验证预编译、敏感字段脱敏

## 测试状态

```
155 passed in the current local Hatch env
ruff: 0 errors             ✅
```

覆盖核心链路：SessionState、PolicyEngine、SecurityValidator、RAGPipeline、ToolLoopUseCase、LLM 适配器、HybridMemoryAdapter、MCP、Skill、Memory 自动抽取、流式会话安全等。

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
    │  策略    │      │   端口      │     │  运行时    │
    │  引擎    │      │  (适配器)   │     │ (可观测)   │
    └──────────┘      └─────┬──────┘     └───────────┘
                            │
          ┌─────────────────┼─────────────────┐
          │                 │                 │
     ┌────▼─────┐    ┌─────▼──────┐   ┌─────▼─────┐
     │   LLM    │    │   记忆      │   │   检索    │
     │  适配器   │    │   适配器    │   │   适配器   │
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

SDK 必须通过 `async with` 上下文管理器使用（MCP 工具等异步资源在此阶段初始化）。推荐通过 `client.conversation()` 创建会话对象，自动维护 `session_id` 和对话默认参数：

```python
import asyncio
from astracore.sdk import AstraCoreClient

async def main():
    # 默认读取 config/config.yaml，并通过 .env 中的 api_key_env 解析密钥
    async with AstraCoreClient() as client:
        # Conversation 门面：自动维护 session_id，参数一次配置多轮复用
        conv = client.conversation(use_tools=True, model_profile="claude-sonnet")

        # 同步对话
        result = await conv.send("你好，你是谁？")
        print(result.content)           # 回复文本

        # 流式对话（同一会话自动续接）
        async for chunk in conv.stream("讲一个故事"):
            print(chunk, end="", flush=True)

        # 需要工具/思考/技能路由等原始事件时，用 stream_events
        from astracore.shared.ports.llm import StreamEventType
        async for event in conv.stream_events("列出当前目录下的文件"):
            if event.event_type == StreamEventType.TOOL_CALL and event.tool_call:
                print(f"→ 调用工具: {event.tool_call.name}")
            elif event.event_type == StreamEventType.TEXT_DELTA:
                print(event.content, end="", flush=True)

asyncio.run(main())
```

需要跨函数共享会话或恢复已有会话时，传入 `session_id`：

```python
conv = client.conversation(session_id=existing_uuid)
```

低级 API（`client.chat()` / `client.chat_stream()`）仍可用于单次调用或需要精确控制 `session_id` 的场景。

### 基础用法 - 服务

```bash
# 启动 FastAPI 服务
make api

# 访问
# http://127.0.0.1:8000/docs
```

服务端 Chat 流式回答采用后台 run 模型：

- `POST /api/v1/chat/runs` 创建后台生成任务
- `GET /api/v1/chat/runs/{run_id}/stream` 订阅 SSE 输出
- `GET /api/v1/chat/sessions/{session_id}/runs/active` 查询会话中正在运行的任务
- `POST /api/v1/chat/runs/{run_id}/cancel` 手动取消生成

浏览器刷新只会断开 SSE 订阅，不会取消后端生成；页面恢复后会重新订阅 active run，完成后结果写入会话记忆。

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
├── app/
│   ├── factory.py       # FastAPI 应用工厂、生命周期、路由注册
│   └── middleware/      # HTTP 中间件
├── modules/
│   ├── agent/           # 多 Agent 编排领域、用例和 workflow port
│   ├── chat/            # Chat API、Conversation API、Pipeline、会话领域模型
│   ├── memory/          # 结构化 Memory API、领域模型、Engine、Store port
│   ├── projects/        # Project API
│   ├── rag/             # RAG API、检索领域模型、Pipeline、Retriever port、知识库文档
│   ├── settings/        # 用户设置 API
│   ├── skills/          # Skill API（CRUD）、内置 Skill 种子、Skill 工具适配器（load_skill / get_skill_reference / run_skill_script）
│   ├── system/          # Health / System API
│   └── tools/           # 内置工具注册和 Tool port
├── infrastructure/
│   ├── db/              # SQLAlchemy models / session
│   ├── llm/             # Anthropic、OpenAI 适配器
│   ├── memory/          # HybridMemoryAdapter、SQLMemoryStore
│   ├── retrieval/       # ChromaDB 适配器
│   ├── tools/           # native、MCP、composite、parallel agent 工具实现
│   └── workflow/        # NativeWorkflowOrchestrator（DAG 拓扑排序 + 并行执行）
├── mcp_servers/
│   ├── _base.py             # MCP server 公共基础（FastMCP 封装、路径规范化、输出截断）
│   ├── filesystem_server.py # 内置 Python filesystem server（读写/编辑/搜索/元数据，10 个工具）
│   └── shell_server.py      # 内置 MCP Shell Server（受控命令执行）
├── eval/
│   ├── dataset.py       # EvalCase 数据类
│   ├── report.py        # EvalResult / EvalReport
│   ├── runner.py        # EvalRunner（并发执行 + LLM-as-judge + 工具精确匹配）
│   └── __main__.py      # python -m astracore.eval CLI
├── shared/
│   ├── observability/   # 结构化日志、指标、HookRegistry（ShortCircuit 短路 + before/after_llm/tool）、Tracer（Span 链路追踪）
│   ├── policy/          # PolicyEngine（tenacity retry + asyncio timeout）、CircuitBreaker（熔断器）
│   ├── ports/           # 跨模块共享端口（LLM / response_format 结构化输出、Audit、Metrics）
│   ├── security/        # SecurityValidator（XSS、长度、内容过滤）
│   └── utils/           # 跨模块共享工具函数（json_utils：repair_json）
└── sdk/
    ├── client.py              # 主 SDK 客户端（AstraCoreClient + Conversation + WorkflowClient 门面）
    ├── config.py              # Pydantic v2 YAML 配置模型
    └── model_capabilities.py  # 内置模型能力注册表

config/
├── config.yaml          # 本地开发结构化配置
├── config.example.yaml  # 示例配置
└── config.docker.yaml   # Docker 部署配置

frontend/
├── src/app             # 应用根组件、路由、主题
├── src/features        # 按产品能力组织页面、组件、状态、服务和类型
├── src/layouts         # 跨页面布局
├── src/shared          # 跨 feature 复用组件、服务、类型和工具
└── src/main.tsx
```

## 配置

结构化配置放在 `config/config.yaml`（可从 `config/config.example.yaml` 复制），`.env` 只放密钥：

```yaml
llm:
  default_profile: claude-sonnet
  profiles:
    - id: claude-sonnet
      label: Claude Sonnet
      protocol: anthropic
      base_url: https://api.anthropic.com
      api_key_env: ANTHROPIC_API_KEY
      model: claude-sonnet-4-6

agent:
  max_tool_result_chars: 20000  # 单次工具返回最大字符数，超出自动截断并附分页提示
  max_tool_iterations: 10       # 工具调用最大轮次，0 = 不限制
  tool_timeout_s: 120           # 单次工具调用超时（秒）
  enable_spawn_agents: true     # 是否开启并行多 Agent；false 则不暴露 spawn_agents 工具

retrieval:
  collection_name: astracore
  persist_directory: ./chroma_db
  # embedding_model: all-MiniLM-L6-v2          # 默认，英文场景
  # embedding_model: paraphrase-multilingual-MiniLM-L12-v2  # 中文/多语言场景

mcp:
  servers:
    - type: filesystem
      paths:
        - D:/project
    - type: shell
      allow_dirs:
        - D:/project

skills:
  extra_dirs: []             # 额外的 skill 目录，支持绝对路径或 ~/xxx
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
| `filesystem` | 内置 Python filesystem server，无需 Node.js；提供 read_file / write_file / edit_file / list_directory / search_files 等 10 个工具 | `paths: list[str]` |
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

所有示例均通过 `AstraCoreClient` SDK 直接运行，无需先启动 HTTP 服务：

- **基础对话**：`python examples/basic_chat.py` — 同步/流式对话、会话续接
- **工具调用**：`python examples/tool_calling.py [--web]` — 工具事件流、自定义工具注册
- **RAG 管道**：`python examples/rag_example.py` — 文档索引、向量检索、RAG 增强对话
- **结构化记忆**：`python examples/memory_example.py` — 手动 CRUD、Project 绑定、自动记忆提取
- **并发会话**：`python examples/multi_agent.py` — asyncio.gather 并发多会话
- **Skill + 工具**：`python examples/skill_with_tools.py [skill名] [--web]` — Skill 绑定与工具联动
- **服务运行**：`python examples/run_service.py [--port 8080] [--reload]` — 启动 FastAPI HTTP 服务
- **前端调试台**：`frontend/`

### Hook + Tracing 用法

```python
from astracore.sdk import AstraCoreClient
from astracore.shared.observability.hooks import HookRegistry
from astracore.shared.observability.tracing import Tracer

registry = HookRegistry()

# 注册自定义钩子（观察 / 修改）
def log_llm_call(payload):
    print(f"[before_llm] model={payload.model} messages={len(payload.messages)}")
    # 返回 None 不修改，返回新 payload 则替换

registry.before_llm.append(log_llm_call)

# 自动追踪（Span JSON 写入 DEBUG 日志）
tracer = Tracer(session_id="my-session")
tracer.register_hooks(registry)

async with AstraCoreClient(hooks=registry) as client:
    result = await client.chat("你好")
    print(result.content)
```

### DAG Workflow 用法

```python
from astracore.sdk import AstraCoreClient
from astracore.modules.agent.domain import AgentTask, AgentRole

async with AstraCoreClient() as client:
    t1 = AgentTask(role=AgentRole.EXECUTOR, description="搜索 Python asyncio 最佳实践")
    t2 = AgentTask(
        role=AgentRole.EXECUTOR,
        description="基于搜索结果，写一份 500 字技术总结",
        depends_on=[t1.task_id],  # 等 t1 完成后执行
    )
    t3 = AgentTask(
        role=AgentRole.REVIEWER,
        description="审校总结，给出改进意见",
        depends_on=[t2.task_id],
        condition="len(task_results) >= 2",  # 条件不满足则跳过
    )

    state = await client.workflow.run(
        "asyncio-research",
        [t1, t2, t3],
        use_tools=True,
    )
    print(state.result)         # {"completed_tasks": 3, "skipped_tasks": 0}
    print(state.task_results)   # {task_id: result_text, ...}
```

## 核心设计原则

1. **能力边界优先**：新业务代码先归入 `modules/<capability>` / `features/<capability>`，不按 controller/service/store 这类技术层横向堆放
2. **端口优先**：先定义契约，再在 `infrastructure/` 中实现
3. **策略集中化**：预算、重试、超时统一在策略引擎管理
4. **双形态交付**：SDK 和 HTTP Service 共享同一 ChatPipeline
5. **可演进编排**：默认 Native，可适配 LangGraph

## 技术栈

- **语言**：Python 3.11+
- **项目管理**：Hatch
- **架构**：能力模块化 + Clean Architecture + Ports & Adapters
- **Web 框架**：FastAPI + uvicorn
- **数据验证**：Pydantic 2.x（YAML 配置模型 + discriminated union）
- **LLM Providers**：Anthropic Messages 协议、OpenAI 兼容协议（DeepSeek/GLM 等可通过 profile 接入）
- **MCP**：fastmcp（Model Context Protocol 工具集成）
- **存储**：Redis（短期缓存，可选）、SQLite/aiosqlite（默认持久化）；保留 asyncpg 依赖用于后续 PostgreSQL 部署扩展
- **向量数据库**：ChromaDB
- **策略**：tenacity、asyncio
- **测试**：pytest-asyncio（auto mode）、unittest.mock
- **前端**：React + Vite + TypeScript + Zustand

## 里程碑

- [x] M1：核心协议与最小 Provider + Tool 闭环
- [x] M2：记忆、预算、策略、可观测性
- [x] M3：RAG 与多 Agent 协作
- [x] M4：SDK + Service 打包与示例
- [x] M5：质量闭环 — 后端优化 ✅ 单元测试 131 个 ✅ Skill 系统 ✅ 记忆持久化 ✅ Memory 自动抽取 ✅ 系统配置 ✅ MCP 工具集成 ✅ 工具循环健壮性 ✅ 后台 Chat Run ✅ SDK/Service 代码去重（ChatPipeline 统一执行）✅ SDK 全功能对齐 ✅ Skill 路由（off/vector/llm）✅ 多目录 Skill 扫描 ✅ 主/副技能 UI 区分 ✅ 并行多 Agent（spawn_agents）✅ Command + Pipeline 模式重构 ✅ Conversation 门面（多轮会话自动管理 session_id）✅ SKILL_MATCH 事件（SDK 技能路由透传）✅ Hook/Callback 系统（before/after_llm/tool 四切入点）✅ 轻量级 Span 链路追踪（无 OTel 依赖）✅ DAG 工作流引擎（拓扑排序 + 层级并行 + 条件跳过）✅ SDK WorkflowClient ✅
- [x] M5+：Hook ShortCircuit 短路拦截 ✅ CircuitBreaker 熔断器（三态状态机 + PolicyEngine 集成）✅ Structured Output（LLMAdapter response_format + Anthropic tool_use + OpenAI json_schema + MemoryEngine 切换）✅ Agent Eval 评估框架（EvalRunner + LLM-as-judge + 工具精确匹配 + JSON 报告 + CLI）✅ Skill 系统重设计（Agent Skills 标准：三层 System Prompt + Claude 自主路由 + load_skill/get_skill_reference/run_skill_script 工具 + 废弃 SkillRouter）✅
- [ ] M6：可靠性与安全 — API Key 鉴权、限流
- [ ] M7：可观测与性能 — SLO/指标/压测基线
- [ ] M8：发布工程化 — 版本策略、回滚预案、运维文档

## 文件统计

- **Python 源模块**：覆盖 app / modules / infrastructure / shared / sdk 全栈
- **测试覆盖**：155 个测试，覆盖配置、LLM 适配器、应用用例、RAG、工具循环、运行时策略、Skill、Memory、MCP、流式会话安全等核心链路
- **7 个完整示例**：可直接通过 SDK 运行，无需 HTTP 服务
- **双形态交付**：SDK + Service 共享同一 ChatPipeline 执行引擎

## 许可证

AstraCoreAI 使用 [PolyForm Noncommercial License 1.0.0](./LICENSE)。
个人学习、研究和非商业用途可按许可证使用；商业用途需要单独授权，查看 [Commercial Licensing](./COMMERCIAL.md)。

## 贡献

查看 [CONTRIBUTING.md](./docs/CONTRIBUTING.md) 了解开发指南。

## 设计文档

- [AstraCore AI 设计文档](./docs/AstraCoreAI设计文档.md)
- [开发进度规划](./docs/开发进度规划.md)
