# Multi-Agent 并行执行设计方案

**版本**: 1.1  
**状态**: 已确认，待实现  
**作者**: AstraCoreAI Team

---

## 1. 背景与目标

### 1.1 问题

当前系统的 AI 处理模型是单线程串行的：无论需要查询多少个数据源，都由同一个 LLM 实例顺序执行。面对"对比 A 股和美股表现"这类多数据源查询，瓶颈不在工具执行，而在 **LLM 推理本身是串行的**。

现有能力边界：

| 能力 | 现状 |
|---|---|
| 工具级并发 | ✅ 已有（`execute_parallel()` via `asyncio.gather`） |
| 多 LLM 实例并行推理 | ❌ 不支持 |
| 子 Agent 独立上下文 | ❌ 不支持 |
| 子 Agent 流式进度可见 | ❌ 不支持 |

### 1.2 目标

实现 **Orchestrator-Worker 并行 Agent 模式**：

- Orchestrator（主控 LLM）负责任务理解、分解决策和结果综合
- 多个 Worker Agent 并发执行子任务，每个独立推理、独立使用工具
- 子 Agent 的实时进度对用户完全透明（流式可见）
- 不破坏现有任何功能，向后兼容

---

## 2. 整体架构

### 2.1 执行流程

```
用户消息: "对比今天 A 股和美股的表现"
        │
        ▼
┌─────────────────────────────┐
│   Orchestrator LLM           │  ← 使用用户选定的主模型
│   决定调用 spawn_agents 工具  │
└─────────────┬───────────────┘
              │ spawn_agents([taskA, taskB])
              ▼
┌─────────────────────────────────────────┐
│          ParallelAgentTool               │
│  asyncio.gather(agentA, agentB)         │
│                                          │
│  ┌── Worker Agent A ──┐  ┌── Worker B ──┐  │
│  │ task: 查 A 股数据   │  │ task: 查美股 │  │
│  │ model: haiku        │  │ model: haiku │  │
│  │ tools: [search,...]  │  │ tools: [...] │  │
│  │                     │  │              │  │
│  │ → search_astock ✓  │  │ → search_us ✓│  │
│  │ → 上证 3245...      │  │ → NASDAQ...  │  │
│  └────────────────────┘  └──────────────┘  │
│              两个 Agent 并发流式输出         │
└─────────────────────────────────────────┘
              │ 聚合结果
              ▼
┌─────────────────────────────┐
│   Orchestrator LLM           │
│   综合两份结果，流式输出最终  │
│   回答                        │
└─────────────────────────────┘
```

### 2.2 分层职责

| 层 | 组件 | 职责 |
|---|---|---|
| **编排层** | Orchestrator LLM | 任务理解、并行决策、结果综合 |
| **执行层** | ParallelAgentTool | 并发启动子 Agent、聚合结果、转发流式事件 |
| **Agent 层** | Worker Agent × N | 独立 LLM + ToolLoop + 隔离上下文 |
| **传输层** | SSE 协议扩展 | 带 `agent_id` 的子 Agent 事件流 |
| **展示层** | SubAgentPanel | 并排卡片展示每个 Agent 实时进度 |

---

## 3. 后端设计

### 3.1 StreamEventType 扩展

在 `core/ports/llm.py` 新增 6 种事件类型，现有 8 种完全不变：

```python
class StreamEventType(StrEnum):
    # 现有类型（不变）
    TEXT_DELTA       = "text_delta"
    THINKING_DELTA   = "thinking_delta"
    ROUND_START      = "round_start"
    THINKING_STOP    = "thinking_stop"
    TOOL_CALL        = "tool_call"
    TOOL_RESULT      = "tool_result"
    ERROR            = "error"
    DONE             = "done"

    # 新增：子 Agent 事件
    AGENT_START          = "agent_start"           # 子 Agent 启动
    AGENT_TEXT_DELTA     = "agent_text_delta"       # 子 Agent 文本增量
    AGENT_THINKING_DELTA = "agent_thinking_delta"   # 子 Agent 思考增量
    AGENT_TOOL_CALL      = "agent_tool_call"        # 子 Agent 工具调用
    AGENT_TOOL_RESULT    = "agent_tool_result"      # 子 Agent 工具结果
    AGENT_DONE           = "agent_done"             # 子 Agent 完成
```

所有 `AGENT_*` 事件的 `metadata` 均包含 `agent_id` 字段用于前端区分。

### 3.2 ToolAdapter 接口扩展

在 `core/ports/tool.py` 为 `ToolAdapter` 增加可选的流式执行方法：

```python
class ToolAdapter(ABC):
    # 现有接口（不变）
    @abstractmethod
    async def execute(self, tool_name: str, arguments: dict) -> ToolResult: ...

    @abstractmethod
    def get_definitions(self) -> list[ToolDefinition]: ...

    # 新增：支持流式事件的工具重写此方法
    # 默认实现退化为调用 execute()，完全向后兼容
    async def execute_streaming(
        self,
        tool_name: str,
        arguments: dict,
    ) -> AsyncIterator[StreamEvent | ToolResult]:
        yield await self.execute(tool_name, arguments)
```

`ParallelAgentTool` 重写 `execute_streaming`，在执行过程中 yield `AGENT_*` 事件；普通工具无需任何改动。

### 3.3 ToolLoopUseCase 改造

`tool_loop.py` 的工具执行部分改为调用 `execute_streaming`：

```
旧逻辑:
  result = await tools.execute(name, args)
  yield TOOL_RESULT(result)

新逻辑:
  async for item in tools.execute_streaming(name, args):
      if isinstance(item, StreamEvent):
          yield item          # 透传子 Agent 的 AGENT_* 事件
      elif isinstance(item, ToolResult):
          result = item       # 收到最终结果，继续正常流程
          yield TOOL_RESULT(result)
          break
```

此改动对所有普通工具透明（默认实现直接返回 ToolResult），只有 `ParallelAgentTool` 会在中间 yield 额外事件。

### 3.4 ParallelAgentTool

新文件：`src/astracore/adapters/tools/parallel_agent.py`

#### AgentTask 数据结构

```python
@dataclass
class AgentTask:
    task: str                          # 子任务描述（Orchestrator 传给 Worker 的 prompt）
    agent_id: str = field(            # 自动生成的短 ID，用于前端区分
        default_factory=lambda: uuid4().hex[:8]
    )
    tools: list[str] | None = None    # None = 全量工具（除 spawn_agents 本身）
    model_profile: str | None = None  # None = 使用默认快速模型
    context: str | None = None        # 可选：Orchestrator 注入的背景摘要
```

#### 核心执行逻辑

```python
class ParallelAgentTool:
    """
    spawn_agents 工具实现。
    每个子任务获得独立的 LLMAdapter + ToolLoopUseCase + 空 SessionState。
    所有子 Agent 并发执行，事件实时通过 execute_streaming 转发给父级 ToolLoop。
    """

    async def execute_streaming(self, tool_name, arguments):
        tasks = [AgentTask(**t) for t in arguments["tasks"]]

        # 启动所有子 Agent，通过共享 asyncio.Queue 收集事件
        event_queue: asyncio.Queue = asyncio.Queue()
        results: dict[str, str] = {}

        async def run_agent(task: AgentTask):
            agent = self._build_agent(task)
            async for event in agent.stream(task.task):
                await event_queue.put((_AGENT_EVENT, task.agent_id, event))
            results[task.agent_id] = agent.get_result()
            await event_queue.put((_AGENT_DONE, task.agent_id, None))

        # 并发执行所有子 Agent
        agent_tasks = [asyncio.create_task(run_agent(t)) for t in tasks]
        pending = len(tasks)

        while pending > 0:
            kind, agent_id, payload = await event_queue.get()
            if kind == _AGENT_DONE:
                pending -= 1
            else:
                yield self._wrap_agent_event(agent_id, payload)

        await asyncio.gather(*agent_tasks)  # 确保所有 task 完成

        # 最终返回聚合结果给 Orchestrator
        yield ToolResult(content=self._format_results(tasks, results))

    def _build_agent(self, task: AgentTask):
        """为每个子任务创建隔离的 Agent 实例"""
        # - 独立 LLMAdapter（优先用 haiku，可被 task.model_profile 覆盖）
        # - 独立 ToolLoopUseCase（max_iterations=5）
        # - 全新 SessionState（不携带主会话历史）
        # - 可用工具：全量工具减去 spawn_agents（防递归，深度限制 = 1）
        ...

    def _format_results(self, tasks, results) -> str:
        """将多个子 Agent 结果格式化为 Orchestrator 易于综合的文本"""
        ...
```

#### 工具定义（供 LLM 理解）

触发方式：**完全依赖 LLM 自主决策**，不在系统提示中添加硬性规则。通过清晰的工具描述让 LLM 自行判断何时使用并行。

```json
{
  "name": "spawn_agents",
  "description": "当任务需要同时从多个独立来源收集信息时，启动多个并行 Agent 分别执行，速度远快于串行。适用场景：对比分析、多数据源聚合、可拆解为相互独立的子问题。每个子 Agent 拥有完整工具访问权限，独立推理，结果返回后由你综合。",
  "input_schema": {
    "type": "object",
    "properties": {
      "tasks": {
        "type": "array",
        "minItems": 2,
        "maxItems": 5,
        "items": {
          "type": "object",
          "properties": {
            "task":    { "type": "string", "description": "子任务的完整描述，子 Agent 将以此为用户消息开始执行" },
            "context": { "type": "string", "description": "可选，传给子 Agent 的背景信息摘要（如主会话中的关键前提）" }
          },
          "required": ["task"]
        }
      }
    },
    "required": ["tasks"]
  }
}
```

> **注意**：`tools` 字段已从 task 中移除——Worker Agent 始终使用全量工具（减去 `spawn_agents` 本身）。工具白名单由系统强制控制，无需 Orchestrator 指定。

### 3.5 SSE 事件映射（chat.py 扩展）

在 `_execute_tool_run` 的事件处理逻辑中，新增 `AGENT_*` 事件的映射：

| StreamEventType | SSE event 名 | SSE data 字段 |
|---|---|---|
| `AGENT_START` | `agent_start` | `{agent_id, task, model}` |
| `AGENT_TEXT_DELTA` | `agent_message` | `{agent_id, text}` |
| `AGENT_THINKING_DELTA` | `agent_thinking` | `{agent_id, text}` |
| `AGENT_TOOL_CALL` | `agent_tool_start` | `{agent_id, tool, input}` |
| `AGENT_TOOL_RESULT` | `agent_tool_result` | `{agent_id, tool, result, is_error, duration_ms}` |
| `AGENT_DONE` | `agent_done` | `{agent_id, duration_ms, error?}` |

### 3.6 模型分配策略

| 角色 | 默认模型 | 覆盖方式 |
|---|---|---|
| Orchestrator | 用户在前端选定的主模型 | 不覆盖 |
| Worker Agent | `claude-haiku-4-5-20251001` | 每个 task 可通过 `model_profile` 字段指定 |
| 汇总阶段（Orchestrator 综合） | 同 Orchestrator | — |

**设计考量**：Worker Agent 默认用 Haiku 是因为它们执行的是具体的信息收集任务，推理复杂度有限，速度优先。Orchestrator 负责最终综合，使用主模型保证质量。

### 3.7 安全约束

- **递归限制**：Worker Agent 的可用工具列表中不包含 `spawn_agents`，禁止嵌套并行（深度上限 = 1）
- **并行数量限制**：`spawn_agents` 的 `tasks` 数组限制 2–5 个，防止资源滥用
- **超时**：每个 Worker Agent 整体超时 60s（独立于父级 ToolLoop 的工具超时）
- **上下文隔离**：Worker 拿不到主会话的历史消息，只能接收 Orchestrator 显式通过 `context` 字段传入的信息

---

## 4. 前端设计

### 4.1 类型定义扩展

在 `frontend/src/types/chat.ts` 新增：

```typescript
export type SubAgentActivity = {
  agentId: string;
  task: string;
  model?: string;
  status: 'running' | 'done' | 'error';
  thinkingBlocks: string[];       // 子 Agent 的思考内容（可折叠展示）
  toolActivity: ToolActivity[];   // 复用现有 ToolActivity 类型
  result?: string;                // 子 Agent 的最终输出（done 后填充）
  durationMs?: number;
  error?: string;
};

// ChatMessage 新增字段
export type ChatMessage = {
  // ... 现有字段不变 ...
  subAgents?: SubAgentActivity[];   // 新增：并行子 Agent 列表
};
```

### 4.2 chatService.ts 扩展

在 `parseBlock()` 中新增 6 种事件的解析分支，对应 6 个新回调：

```typescript
// StreamHandlers 新增回调
onAgentStart?:      (agentId: string, task: string, model?: string) => void;
onAgentMessage?:    (agentId: string, delta: string) => void;
onAgentThinking?:   (agentId: string, delta: string) => void;
onAgentToolStart?:  (agentId: string, tool: string, input: Record<string, unknown>) => void;
onAgentToolResult?: (agentId: string, tool: string, result: string, isError: boolean, durationMs: number) => void;
onAgentDone?:       (agentId: string, durationMs: number, error?: string) => void;
```

### 4.3 chatStore.ts 扩展

在 `resumeActiveRun` 中新增对应处理，均通过 `agentId` 定位到 `subAgents` 数组中的具体条目：

```
onAgentStart    → 在 message.subAgents 末尾追加新 SubAgentActivity（status: 'running'）
onAgentMessage  → 找到对应 agentId，追加 result 文本
onAgentThinking → 找到对应 agentId，追加 thinkingBlocks 最后一项
onAgentToolStart   → 找到对应 agentId，向其 toolActivity 追加 {done: false}
onAgentToolResult  → 找到对应 agentId，标记对应 toolActivity 条目 done=true
onAgentDone     → 找到对应 agentId，status 设为 'done' 或 'error'
```

### 4.4 SubAgentPanel 组件

新文件：`frontend/src/components/chat/SubAgentPanel.tsx`

#### 视觉设计

```
┌─ ⚡ 并行处理中 (2 个 Agent)  ─────────────────────────────────┐
│                                                               │
│  ┌── Agent 1 ──────────────────┐  ┌── Agent 2 ─────────────┐  │
│  │ 📋 查 A 股今日行情           │  │ 📋 查美股今日行情       │  │
│  │ ─────────────────────────  │  │ ──────────────────────  │  │
│  │ ▸ search_astock    ✓ 0.8s  │  │ ⟳ search_nasdaq        │  │
│  │ ▸ fetch_detail     ✓ 1.1s  │  │                         │  │
│  │                             │  │                         │  │
│  │ 上证指数 3,245 (+0.82%)    │  │ 🤔 分析中...            │  │
│  │ 深证成指 10,120 (+1.2%)   │  │                         │  │
│  └─────────────────────────── ┘  └─────────────────────────┘  │
│                                                               │
│  全部完成 · 共耗时 3.2s（串行约需 6.1s）                      │
└───────────────────────────────────────────────────────────────┘
```

#### 折叠行为

**SubAgentCard 跟随执行状态自动控制展开/折叠：**

| 时机 | 展开状态 |
|---|---|
| Agent 启动（status: running） | 自动展开 |
| Agent 完成（status: done/error） | 自动折叠 |
| 用户手动点击 | 切换，优先级高于自动行为 |

与现有 ThinkingBlock 的逻辑一致（`defaultActiveKey={streaming ? ['key'] : []}`），用户体验连贯。

#### 组件结构

```
SubAgentPanel
├── 标题栏：状态图标 + "并行处理中 (N 个 Agent)" + 全部完成后的耗时对比
└── 卡片区（flex wrap，自适应列数）
    └── SubAgentCard × N（Collapse 控制展开/折叠）
        ├── 任务描述（单行截断，hover 展示全文）
        ├── ToolActivityRow（复用现有组件）
        ├── 思考内容（可折叠，同 ThinkingBlock 样式）
        └── 结果预览（done 后显示，最多 3 行，点击展开）
```

#### 状态视觉规则

| 状态 | 卡片边框色 | 标题图标 |
|---|---|---|
| running | `colorWarningBorder` | `LoadingOutlined` spin |
| done | `colorSuccessBorder` | `CheckOutlined` |
| error | `colorErrorBorder` | `CloseCircleOutlined` |

### 4.5 ChatMain.tsx 集成

在 `AssistantContent` 组件中，在 `ToolActivityRow` 之后、`MarkdownContent` 之前插入：

```tsx
{message.subAgents && message.subAgents.length > 0 && (
  <SubAgentPanel agents={message.subAgents} />
)}
```

---

## 5. 变更文件清单

### 后端（9 个文件）

| 文件 | 操作 | 说明 |
|---|---|---|
| `core/ports/llm.py` | 改 | 新增 6 种 `AGENT_*` StreamEventType |
| `core/ports/tool.py` | 改 | `ToolAdapter` 新增 `execute_streaming` 默认方法 |
| `core/application/tool_loop.py` | 改 | 工具执行改为调用 `execute_streaming`，透传 `AGENT_*` 事件 |
| `adapters/tools/parallel_agent.py` | **新建** | `ParallelAgentTool` 完整实现 |
| `adapters/tools/composite.py` | 改 | `CompositeToolAdapter` 实现 `execute_streaming`，路由到子适配器 |
| `adapters/tools/native.py` | 改 | `NativeToolAdapter` 实现 `execute_streaming` 默认行为 |
| `service/builtin_tools.py` | 改 | 注册 `spawn_agents` 工具 |
| `service/api/chat.py` | 改 | `_execute_tool_run` 新增 `AGENT_*` 事件的 SSE 映射 |
| `service/chat_orchestrator.py` | 改 | 系统提示中加入 `spawn_agents` 使用指引 |

### 前端（5 个文件）

| 文件 | 操作 | 说明 |
|---|---|---|
| `src/types/chat.ts` | 改 | 新增 `SubAgentActivity` 类型，`ChatMessage` 增加 `subAgents` 字段 |
| `src/services/chatService.ts` | 改 | `parseBlock` 新增 6 种 Agent 事件解析，`StreamHandlers` 增加回调 |
| `src/stores/chatStore.ts` | 改 | `resumeActiveRun` 新增 Agent 事件处理逻辑 |
| `src/components/chat/SubAgentPanel.tsx` | **新建** | 并行 Agent 进度面板组件 |
| `src/components/chat/ChatMain.tsx` | 改 | `AssistantContent` 集成 `SubAgentPanel` |

---

## 6. 已确认的设计决策

| # | 问题 | 决策 | 说明 |
|---|---|---|---|
| 6.1 | spawn_agents 触发方式 | **LLM 自主决策** | 不加系统提示规则，通过清晰的工具描述引导 LLM 自行判断 |
| 6.2 | 子 Agent 递归 | **禁止（深度限制 = 1）** | Worker 工具集强制排除 `spawn_agents`，防止递归失控 |
| 6.3 | 卡片折叠行为 | **运行中展开，完成后折叠** | 与 ThinkingBlock 行为一致，用户可手动覆盖 |
| 6.4 | Worker 工具集 | **全量工具减去 spawn_agents** | 系统强制控制，Orchestrator 无需指定白名单 |

---

## 7. 性能预期

以"查 A 股 + 查美股"为例（每次查询含 2 次工具调用 + 1 次 LLM 推理）：

| 指标 | 串行（现在） | 并行（方案实现后） |
|---|---|---|
| 总耗时 | ~12s（6s × 2） | ~6s（最慢 Worker 决定） |
| LLM API 并发数 | 1 | 3（2 Worker + 1 Orchestrator 综合） |
| Token 消耗 | 不变 | 略增（子 Agent 有独立 system prompt） |

实际加速比取决于子任务的均匀程度，最坏情况（子任务串行依赖）退化为串行速度但不会更慢。
