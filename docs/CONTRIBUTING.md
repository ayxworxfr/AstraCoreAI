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
│   ├── domain/       # 纯领域模型 — 零外部依赖
│   ├── application/  # 用例（Chat、ToolLoop、RAG、MultiAgent）
│   └── ports/        # 抽象接口（LLM、Memory、Retriever、Tool、Workflow）
├── adapters/         # 端口的具体实现（Anthropic、OpenAI 兼容接口、Redis、ChromaDB…）
├── mcp_servers/      # 内置 MCP 服务器实现
├── runtime/
│   ├── policy/       # PolicyEngine（retry / timeout）
│   ├── observability/
│   └── security/     # SecurityValidator
├── service/
│   ├── api/          # FastAPI 路由
│   └── middleware/
└── sdk/              # 对外 SDK 入口与 YAML 配置模型

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

`service/chat_orchestrator.py` 中的 `ChatOrchestrator` 是 SDK 与 HTTP Service 的统一 chat 执行引擎，包含：

- **LLM / ToolLoop 工厂**：按 profile 创建并缓存 `LLMAdapter`，每次调用创建 `ToolLoopUseCase`
- **提示词组装**：`build_system_prompt`（Skill + 全局指令 + RAG 三层）、`build_rag_context`
- **消息工具方法**：`strip_dangling_tool_calls`、`prepare_for_save`、`needs_summary_fallback`、`build_summary_fallback_messages`
- **核心流式方法**：`stream_normal`（普通对话）、`stream_with_tools`（工具循环）

**HTTP Service**（`service/api/chat.py`）在 `_execute_normal_run` / `_execute_tool_run` 中消费 orchestrator 输出的 `StreamEvent`，叠加 SSE 广播、run 追踪等 HTTP 专属逻辑。

**SDK**（`sdk/client.py`）在 `chat_stream` 中直接 yield orchestrator 的事件流，叠加 MCP 生命周期管理等 SDK 专属逻辑。

新增涉及对话管道的功能时，优先修改 `ChatOrchestrator`，不要在两端各自复制逻辑。

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
| `tool_start` | `{"tool", "input"}` |
| `tool_result` | `{"tool", "input", "result", "is_error", "duration_ms"}` |
| `message` | `{"text"}` |
| `done` | `{"conversation": {"title", "last_message_preview", "message_count", "updated_at"}}` |
| `error` | `{"message"}` |

`done` 事件的 `conversation` 字段携带后端更新后的会话元数据，前端收到后直接同步本地状态，无需再发 PATCH 请求。如果会话行不存在（如纯 SDK 调用未创建 ConversationRow），该字段可能为 `null`。

前端统一通过 `chatService.ts` 中的 `safeJson()` 解析，新增事件类型须同步更新 `parseBlock` 和 `StreamHandlers` 类型定义。

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
