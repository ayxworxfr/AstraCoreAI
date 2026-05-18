# 开发指南

## 目录

- [环境准备](#环境准备)
- [项目结构](#项目结构)
- [开发工作流](#开发工作流)
- [代码规范](#代码规范)
- [测试规范](#测试规范)
- [架构约定](#架构约定)
- [提交规范](#提交规范)
- [常见任务](#常见任务)

---

## 环境准备

**依赖要求**

- Python 3.11+
- Node.js 18+（前端开发 / filesystem MCP）
- Redis（可选，短期记忆；不可用时自动降级到 SQLite）

**初始化**

```bash
# 1. 克隆仓库
git clone https://github.com/astracore/astracore-ai.git
cd astracore-ai

# 2. 一键初始化（安装 Hatch + 后端依赖 + ChromaDB）
make setup

# 3. 复制并填写环境变量
cp .env.example .env
# 编辑 .env，至少填写 config/config.yaml 中 api_key_env 对应的密钥

# 4. 前端依赖（仅前端开发需要）
make fe-install
```

**日常启动**

```bash
make api      # 后端  http://127.0.0.1:8000
make fe-dev   # 前端  http://127.0.0.1:5173
```

---

## 项目结构

```
src/astracore/
├── core/
│   ├── domain/       # 纯领域模型 — 零外部依赖（Session、Message、ChatContext、Agent）
│   ├── application/  # 用例（ToolLoop、RAG、MultiAgent）
│   └── ports/        # 抽象接口（LLM、Memory、Retriever、Tool、Workflow）
├── adapters/         # 端口的具体实现（Anthropic、OpenAI、Redis、ChromaDB、MCP…）
├── mcp_servers/      # 内置 MCP 服务器实现
├── runtime/
│   ├── policy/       # PolicyEngine（retry / timeout）
│   ├── observability/
│   └── security/     # SecurityValidator
├── service/
│   ├── api/          # FastAPI 路由
│   ├── middleware/
│   ├── chat_pipeline.py   # 共享 chat 执行引擎（prepare + stream + execute）
│   ├── builtin_tools.py   # 内置工具注册
│   ├── skill_router.py    # Skill 自动路由
│   ├── seeds.py           # 内置 Skill 种子同步
│   └── prompt_utils.py    # 系统提示工具函数
└── sdk/
    ├── client.py          # AstraCoreClient + Conversation 门面
    ├── config.py          # YAML 配置模型
    └── model_capabilities.py

config/
├── config.yaml       # 本地开发结构化配置
├── config.example.yaml
└── config.docker.yaml

frontend/src/
├── components/       # React UI 组件
├── pages/            # 页面（Chat / RAG / Skills / System）
├── stores/           # Zustand 状态管理
├── services/         # API / SSE 通信
└── types/            # TypeScript 类型定义

tests/
├── unit/             # 单元测试（mock 外部依赖）
├── integration/      # 集成测试（真实 DB/Redis）
├── adapters/         # 适配器专项测试
└── conftest.py       # 共享 fixture
```

---

## 开发工作流

```bash
make test        # 运行全量测试（必须全部通过）
make lint        # ruff 静态检查
make type-check  # mypy 严格类型检查
make fmt         # 自动格式化（提交前运行）
make check       # lint + type-check 合并执行
```

提交前确保：

```
make fmt && make check && make test
```

三项全部无报错后再提交。

---

## 代码规范

### Python

- **行宽**：100 字符（ruff 强制）
- **格式化**：`ruff format`，不手动调整格式
- **import 顺序**：stdlib → 第三方 → 内部（`known-first-party = ["astracore"]`），由 ruff isort 自动管理
- **类型注解**：所有公开函数、方法必须有完整注解；mypy strict 模式通过为准
- **异步**：I/O 操作一律 `async/await`，避免在异步上下文中使用同步阻塞调用
- **异常**：领域层只抛出领域异常；适配层负责将外部异常转换为领域异常

### TypeScript / React

- **行宽**：100 字符
- **组件**：函数式组件 + hooks，不使用 class component
- **状态管理**：页面级状态用 Zustand store，组件内临时状态用 `useState`
- **类型**：所有 props 和 store 状态显式标注类型，禁止 `any`（ESLint 强制）
- **SSE 解析**：所有事件数据字段统一为 JSON，通过 `safeJson()` 解析，不直接操作原始字符串

---

## 测试规范

### 基本要求

- 新功能必须附带测试，核心路径覆盖率不低于现有水平
- 测试文件命名：`test_<模块名>.py`
- 使用 `pytest-asyncio`（`asyncio_mode = "auto"`），异步测试直接 `async def test_xxx`

### 单元测试

外部依赖（LLM API、Redis、数据库）全部 mock：

```python
from unittest.mock import AsyncMock, MagicMock

async def test_chat_use_case():
    mock_llm = AsyncMock()
    mock_llm.generate.return_value = LLMResponse(content="hello")
    use_case = ChatUseCase(llm_adapter=mock_llm, ...)
    ...
```

### 集成测试

放在 `tests/integration/`，可以连接真实 SQLite，但不依赖外部网络服务。CI 环境默认只跑单元测试。

### 运行指定测试

```bash
make test                              # 全量
hatch run pytest tests/unit/           # 仅单元测试
hatch run pytest tests/unit/test_tool_loop.py -k "test_truncate"  # 精确匹配
make test-cov                          # 带覆盖率报告
```

---

## 架构约定

### 依赖方向

```
domain ← application ← adapters ← service/sdk
```

- `domain` 层：**零外部依赖**，不能 import 任何第三方库
- `application` 层：只依赖 `domain` 和 `ports`，不依赖具体适配器
- `ports`（接口）由 `application` 定义，`adapters` 负责实现

违反依赖方向的 import 会被 mypy 和 code review 拒绝。

### SDK 与 Service 共享执行引擎

`service/chat_pipeline.py` 中的 `ChatPipeline` 是 SDK 与 HTTP Service 的统一 chat 执行引擎，采用 **Command + Pipeline** 模式：

- **`prepare()`**：一次性完成所有 DB 查询与业务逻辑决策（Skill 解析、系统提示拼装、温度解析、工具白名单计算），返回不可变的 `ChatContext`（`core/domain/chat_context.py`）
- **`stream(ctx)`**：纯执行阶段，消费 `ChatContext` 数据，零额外 DB 查询、零条件分支。内部路由到 `_stream_normal`（直接 LLM 调用）或 `_stream_tool_loop`（工具循环 + 总结兜底）
- **`execute(ctx)`**：`stream()` 的非流式封装，收集所有 `TEXT_DELTA` 返回完整文本

**HTTP Service**（`service/api/chat.py`）调用 `prepare()` 后把 `ChatContext` 交给后台任务，`_execute_run` 消费 `stream()` 输出的 `StreamEvent`，叠加 SSE 广播、run 状态追踪等 HTTP 专属逻辑。

**SDK**（`sdk/client.py`）在 `chat_stream()` 中调用 `prepare()` 后直接 yield `stream()` 的事件流，额外 yield `SKILL_MATCH` 事件（技能路由结果），MCP 生命周期在 `_start()` / `_stop()` 中管理。`Conversation` 门面封装了 `session_id` 自动管理和常用参数默认值，是推荐的多轮对话入口。

新增涉及对话管道的功能时，优先修改 `ChatPipeline`，不要在两端各自复制逻辑。

### 新增 LLM Profile

优先通过 `config/config.yaml` 增加 profile，而不是新增适配器：

1. 在 `llm.profiles` 添加稳定 `id`、展示 `label`、`provider`、`base_url`、`api_key_env`、`model`。
2. 在根目录 `.env` 填写 `api_key_env` 指向的真实密钥。
3. 如模型能力不在内置表中，先更新 `src/astracore/sdk/model_capabilities.py`。
4. 只有代理或模型行为与内置表不一致时，才在 YAML 的 `capabilities` 写局部覆盖。

### 新增 LLM 适配器

1. 在 `src/astracore/adapters/llm/` 新建文件，继承 `LLMAdapter`（`core/ports/llm.py`）。
2. 实现 `generate` 和 `generate_stream` 两个方法。
3. 扩展 `LLMProfileConfig.provider` 的枚举与 Service/SDK 的 adapter factory。
4. 补充 profile 配置加载、能力推导和适配器行为单元测试。

### 新增工具

**内置工具**（无需外部进程）：在 `src/astracore/service/builtin_tools.py` 注册。

**MCP 工具**：在 `config/config.yaml` 的 `mcp.servers` 中配置；类型为 `custom` 时提供 `name` / `command` / `args` / `env`。

### SSE 事件协议

所有后端 SSE 事件的 `data` 字段必须是合法 JSON 字符串：

| event | data 字段 |
|---|---|
| `conversation` | `{"session_id", "message", "created_at"}` |
| `run_state` | `ChatRunState` 完整快照（重连时用于恢复进度） |
| `thinking_start` | `{"round"}` |
| `thinking` | `{"text"}` |
| `thinking_stop` | `{"duration_ms"}` |
| `tool_start` | `{"tool", "tool_call_id", "input"}` |
| `tool_result` | `{"tool", "tool_call_id", "input", "result", "is_error", "duration_ms"}` |
| `message` | `{"text"}` |
| `done` | `{"conversation": {"title", "last_message_preview", "message_count", "updated_at"}}` |
| `error` | `{"message"}` |
| `auto_skills` | `{"anchor": "skill_name_or_null", "routed": ["name1", …]}` |
| `agent_start` | `{"agent_id", "task", "model"}` |
| `agent_message` | `{"agent_id", "text"}` |
| `agent_thinking` | `{"agent_id", "text"}` |
| `agent_tool_start` | `{"agent_id", "tool", "tool_call_id", "input"}` |
| `agent_tool_result` | `{"agent_id", "tool", "tool_call_id", "result", "is_error", "duration_ms"}` |
| `agent_done` | `{"agent_id", "duration_ms", "error"}` |

`done` 事件的 `conversation` 字段携带后端更新后的会话元数据，前端收到后直接同步本地状态，无需再发 PATCH 请求。如果会话行不存在（如纯 SDK 调用未创建 ConversationRow），该字段可能为 `null`。

前端统一通过 `chatService.ts` 中的 `safeJson()` 解析，新增事件类型须同步更新 `parseBlock` 和 `StreamHandlers` 类型定义。

### 并行多 Agent（spawn_agents）

`ParallelAgentTool`（`adapters/tools/parallel_agent.py`）实现 `spawn_agents` 工具，通过 `asyncio.Queue` 并发驱动最多 5 个 Worker Agent。

**关键设计约定：**

- **`is_timeout_managed` 协议**：`ParallelAgentTool.is_timeout_managed("spawn_agents")` 返回 `True`，`ToolLoopUseCase` 因此用 `contextlib.nullcontext()` 替代 `asyncio.timeout()`，避免外层超时误杀长时间运行的并行任务。所有自行管理超时的工具须遵循此协议。
- **`profile_id` 透传**：`ToolLoopUseCase` 通过 `context={"profile_id": self.profile_id}` 把当前用户选择的模型 profile 传给工具，`ParallelAgentTool` 据此为每个 Worker 创建同 profile 的 `LLMAdapter`（按 profile_id 缓存）。
- **取消传播**：外层 generator 收到 `CancelledError` 时，立即 cancel 所有 Worker asyncio.Task，再 `await asyncio.gather(return_exceptions=True)` 确保清理完成后再重新 raise。
- **开关控制**：`config.agent.enable_spawn_agents`，`False` 时 `builtin_tools.py` 不注册 `ParallelAgentTool`，LLM 不可见该工具。

### 工具结果截断

单次工具返回内容超过 `config/config.yaml` 中 `agent.max_tool_result_chars`（默认 20000 字符）时自动截断，并在末尾附加分页提示。在 `ToolLoopUseCase._truncate_tool_result` 中实现，勿在工具本身做截断。

---

## 提交规范

遵循 [Conventional Commits](https://www.conventionalcommits.org/)：

```
<type>(<scope>): <摘要>

[可选正文]
```

**type**

| type | 用途 |
|---|---|
| `feat` | 新功能 |
| `fix` | Bug 修复 |
| `refactor` | 重构（不改变外部行为） |
| `test` | 新增或修改测试 |
| `docs` | 文档变更 |
| `chore` | 构建、依赖、配置等杂项 |
| `perf` | 性能优化 |

**示例**

```
feat(tool-loop): add duration_ms to TOOL_RESULT event

Track wall-clock execution time per tool call and surface it in the
SSE tool_result payload, frontend badge, and popover.
```

```
fix(chat): handle empty summary fallback when context is too long
```

分支命名：`feat/<简短描述>`、`fix/<简短描述>`、`chore/<简短描述>`。

---

## 常见任务

### 清理与重置

```bash
make clean        # 清理 Python 缓存、日志、前端 node_modules
make clean-rag    # 清空 ChromaDB 向量数据库（需先停止 API）
make stop         # 停止 API（8000）和前端（5173）进程
```

### Docker 开发

```bash
make docker-build    # 构建镜像（自动预下载 ChromaDB 模型）
make docker-up       # 后台启动
make docker-logs     # 实时查看 app 日志
make docker-restart  # 热重启 app 容器（不重建）
make docker-down     # 停止
make docker-clean    # 停止并删除所有数据卷（⚠️ 不可逆）
```

### 数据库迁移

项目使用 Alembic 管理 SQLite/PostgreSQL schema，迁移文件位于 `src/astracore/adapters/db/migrations/`。新增模型字段后：

```bash
hatch run alembic revision --autogenerate -m "add xxx field"
hatch run alembic upgrade head
```

### 前端构建产物

```bash
make fe-build      # 输出到 frontend/dist/
make fe-preview    # 本地预览构建产物
```
