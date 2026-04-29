# 用 Clean Architecture 构建 AI 框架：从分层设计到生产落地

> 本文记录了从零搭建一套 AI 对话框架的架构设计过程，涵盖分层思路、策略引擎、记忆系统、工具循环、RAG、流式输出和前端状态管理，以及实际踩坑后的解决方案。

---

## 为什么 AI 应用需要认真对待架构

大多数 AI 应用原型很容易写：几行代码调 API，拿到回复，完成。但一旦进入生产，问题就来了：

- 今天用 Claude，明天要切 GPT，Provider 相关代码已经散落在各处
- 单元测试每次都要真实调用 LLM API，又慢又烧钱
- Redis 挂了，整个对话上下文全丢
- 工具调用偶尔返回异常，整个请求就卡死了
- Token 预算、重试、超时逻辑散落在各个模块，牵一发而动全身

这些问题的根源不是技术难度，而是**边界没有划清楚**：业务逻辑和外部依赖混在一起，任何一处变化都可能引发连锁反应。

---

## 一、分层架构：让内层对外层一无所知

面对上述问题，最有效的解法是 **Clean Architecture + Ports & Adapters**。核心思想只有一条：

> **依赖方向永远朝内，内层对外层一无所知。**

```
Domain（领域模型）          ← 零外部依赖
    ↑
Application（用例编排）     ← 只依赖 Domain 和接口
    ↑
Ports（适配器接口）         ← 定义"我需要什么能力"
    ↑
Adapters（具体实现）        ← Anthropic / OpenAI / Redis / ChromaDB
    ↑
Service / SDK（交付层）     ← FastAPI 路由、Python SDK
```

### Domain 层：零依赖的业务核心

这里只放纯粹的数据结构和业务规则，不 import 任何第三方库：

- **消息模型**：`Message`、`MessageRole`、`ToolCall`、`ToolResult`
- **会话模型**：`SessionState`、`ContextWindow`、`TokenBudget`
- **检索模型**：`RetrievalQuery`、`RetrievedChunk`、`Citation`
- **Agent 模型**：`AgentTask`、`AgentRole`、`AgentDecision`

Domain 层可以在任意环境运行，包括没有网络的单元测试。

### Application 层：业务流程的编排中心

这里定义系统能做什么，是框架行为的核心所在：

- `ChatUseCase`：普通对话，加载上下文 → 注入系统提示 → 流式生成 → 保存会话
- `ToolLoopUseCase`：工具调用循环，LLM 决策 → 调工具 → 回写结果 → 继续生成
- `RAGPipeline`：检索增强，向量召回 → 重排 → 引用注入 → 生成
- `AgentOrchestrationUseCase`：多 Agent 协作，Planner / Executor / Reviewer 分工

Application 层通过 Ports 接口调用外部能力，不知道底层用的是哪个 LLM 或哪个数据库。

### Ports 层：可替换的边界契约

Ports 是一组抽象接口，先定义协议，再接入实现：

| Port | 职责 |
|------|------|
| `LLMAdapter` | 文本生成（同步 + 流式） |
| `ToolAdapter` | 工具执行与定义查询 |
| `MemoryAdapter` | 短期 / 长期记忆读写 |
| `RetrieverAdapter` | 向量检索 |
| `WorkflowOrchestrator` | 工作流编排与 checkpoint |
| `AuditLogger` | 审计日志 |
| `MetricsReporter` | 指标上报 |

要换一套实现，只需新写一个 Adapter，其他层完全不动。

---

## 二、完整请求数据流

一次带工具的对话请求完整走向如下：

```
Client
  ↓  ChatRequest
FastAPI / SDK 入口
  ↓
ChatUseCase.execute_stream()
  ├─ PolicyEngine.applyPolicy()       ← Token 预算分配、上下文裁剪
  ├─ MemoryAdapter.load_short_term()  ← 加载历史消息（Redis → SQLite）
  ├─ RAGPipeline.retrieve()           ← 向量召回 + 引用注入（可选）
  └─ ToolLoopUseCase.execute_stream()
       ├─ LLMAdapter.generate_stream() → TEXT_DELTA / TOOL_CALL 事件
       ├─ ToolAdapter.execute()        ← 执行工具，asyncio.wait_for 隔离超时
       └─ 回写 ToolResult → 继续下一轮
  ↓  SSE 事件流
Client
```

其中 HTTP Service 通过后台 Chat Run 负责实际生成，SSE 只订阅 run 输出。用户不需要等工具全部执行完才看到响应，刷新页面也不会直接取消后端生成。

这里有一个很容易踩的坑：**订阅恢复不能等于重新生成**。浏览器刷新、React 开发模式下的 effect 重跑、发送流程和恢复流程并发，都可能让前端对同一个 `run_id` 发起两次 SSE 订阅。如果不去重，后端其实只生成了一次，但前端会把同一批事件渲染成两个一模一样的气泡。

---

## 三、双形态交付：SDK 和 HTTP Service 共享同一套逻辑

AI 框架通常有两种使用场景：嵌入到 Python 应用（SDK 调用），或作为独立服务提供 HTTP 接口。很多项目会把两套实现分开写，导致行为不一致。

更好的做法是：**SDK 和 HTTP Service 共享同一个 Application 层**，两者只是不同的入口。

```
Python SDK 调用              HTTP POST /api/v1/chat/runs
       ↓                               ↓
           ChatUseCase / ToolLoopUseCase / RAGPipeline
                                       ↓
                         SSE 订阅 /chat/runs/{run_id}/stream
```

`ChatUseCase` 改了一行，两种形态同时生效，维护成本只有一份。

---

## 四、策略层：把横切关注点集中起来

Token 预算、重试、超时、降级、安全校验……这些逻辑如果散落在各个 Adapter 里，改一个参数要找好几个地方，行为容易不一致。建议统一放在一个 `PolicyEngine`：

- **Token 预算分配**：为输入、输出、工具调用、记忆各自分配 token 上限，防止单项无限膨胀
- **上下文裁剪**：当历史消息超出预算时，从头裁剪保留最近的 N 条；裁剪算法要做到 O(n) 而非 O(n²)
- **重试与退避**：对 Provider 429 / 5xx 进行指数退避重试，区分可重试错误（限速、超时）和不可重试错误（参数错误）
- **超时控制**：LLM 调用和工具执行分别独立计时，互不影响
- **调用降级**：主模型不可用时自动切到经济模型
- **安全校验**：工具名称白名单、输入内容 XSS 检测、敏感字段脱敏

一个血泪教训：早期的重试逻辑是**死代码**——函数签名接收 retry 参数，但内部实现并没有真正接入重试库。这种假象的可靠性比没有更危险，真出了问题根本不会触发重试。写完策略逻辑一定要验证它实际生效了。

---

## 五、记忆系统：为降级而设计

AI 对话最常见的设计是"Redis 存上下文"，问题在于这是单点——Redis 一挂，上下文全丢。更健壮的做法是两层降级：

```
短期记忆（热路径）
  Redis     ← 主力，TTL 自动淘汰，配置容量上限，速度最快
    ↓ 不可用时自动切换，不再尝试 Redis
  SQLite    ← 持久化层，重启后从这里恢复历史消息

长期记忆（冷路径）
  PostgreSQL ← 会话摘要、用户偏好、关键事件（异步写入）
```

**Redis 失败处理**：第一次遇到 Redis 异常时，标记 `_redis_disabled = True`，后续所有操作直接走 SQLite，不再尝试连接 Redis，避免每次请求都卡在 Redis 超时上。

**SQLite 的职责不是替代 Redis**，而是保证重启恢复。每次 `save_short_term` 都同时写 Redis 和 SQLite，读取时优先读 Redis，Redis 没有才回退到 SQLite。

**特意不引入内存字典兜底**，是为了保证多进程部署下没有进程内状态，避免多实例之间数据不一致。

**上下文窗口管理**：对话轮次多了之后，历史消息会撑爆模型的上下文窗口。需要在策略层做截断：按 token 预算从头裁剪历史消息，保留最近的 N 条（可通过 `context_max_messages` 配置）。这个值不同任务场景需要不同调整，不能硬编码。

---

## 六、工具循环：比你想象的复杂得多

LLM 工具调用看起来很简单：模型说要调工具 → 执行工具 → 把结果给模型 → 继续生成。但在生产中，每一步都可能出问题。以下是实际踩过的全部坑：

### 坑 1：Shell 命令失败不报错

**现象**：执行出错的 shell 命令（退出码非 0）返回 `isError=False`，LLM 以为成功继续往下走。

**根因**：工具处理函数直接 `return` 输出，没有区分成功与失败。

**解法**：退出码非 0 时抛出异常，由框架自动将响应标记为 `isError=True`：

```python
if exit_code != 0:
    raise RuntimeError(output)
return output
```

### 坑 2：出错后上下文丢失

**现象**：请求处理中途报错后，用户发送的消息和 AI 的部分回复不会被保存，下次对话"失忆"。

**根因**：状态保存只在成功路径执行，异常路径直接 raise 出去了。

**解法**：用 `try...finally` 确保无论成功还是异常都保存会话状态。

### 坑 3：悬空的 tool_use 导致 API 400

**现象**：工具执行中途异常后，再次请求报错 `tool_use ids were found without tool_result blocks`。

**根因**：session 里留下了 `ASSISTANT(tool_calls)` 消息，后面没有对应的 `TOOL(tool_results)`。Anthropic API 不允许这种不完整序列。

**解法**：在所有保存操作之前统一清理尾部悬空的 tool_use：

```python
def _strip_dangling_tool_calls(messages):
    msgs = list(messages)
    while msgs and msgs[-1].role == ASSISTANT and msgs[-1].tool_calls:
        msgs.pop()
    return msgs
```

这个清理要在 `finally` 块里执行，覆盖正常结束和异常中断两种情况。

### 坑 4：最后一轮 LLM 还在调工具

**现象**：达到最大轮次后产生空响应，工具循环没有给出最终答案。

**根因**：最后一轮仍传入工具定义，LLM 产生 `tool_use` 块，但循环 `break` 后工具未执行，session 末尾是一条无结果的工具调用。

**解法**：最后一轮传 `tools=None`，强制 LLM 只输出文本：

```python
is_last = (iterations == self.max_iterations)
tools_for_llm = None if is_last else tool_definitions
```

如果设置最大轮次为 0，表示不限制轮次，由 LLM 自行决定何时停止（适合深度探索任务，注意 token 消耗）。

### 坑 5：超时机制误杀长时间工具

**现象**：文件系统搜索整个项目目录耗时超过 180s，被流式空闲超时中断。

**根因**：在流式层设置了"相邻 SSE 事件间隔超过阈值则取消请求"的空闲超时。MCP 工具执行期间没有流式输出，触发了这个机制。

**解法**：移除流式层的空闲超时，只在单个工具调用级别设置 `asyncio.wait_for`（可配置，默认 120s）。超时时返回错误 `ToolResult` 给 LLM，让它换参数重试，而不是中断整个循环：

```python
try:
    exec_result = await asyncio.wait_for(
        self.tools.execute(tool_name, arguments),
        timeout=self.tool_timeout_s
    )
except asyncio.TimeoutError:
    tool_results.append(ToolResult(..., is_error=True, content="[超时]..."))
    continue  # 不中断循环，继续下一个工具
```

### 坑 6：工具循环结束但没有最终回答

**现象**：工具执行完毕，用户看到空消息。

**根因**：工具轮结束时，session 末尾是 `TOOL` 消息或空 `ASSISTANT` 消息，LLM 没有机会输出总结。

**解法**：检测到此状态时，触发一次无工具的"总结收尾"补充调用，让 LLM 基于所有工具结果给出最终答案：

```python
def _needs_summary_fallback(messages):
    last = visible_messages[-1]
    return (last.role == TOOL or
            (last.role == ASSISTANT and not last.content.strip()))
```

### 坑 7：上下文爆炸导致 API 400

**现象**：3 轮工具调用后产生空回复或 API 报错。

**根因**：单轮可能包含多个工具调用（如 `directory_tree × 5 + read_file × 4`），每条结果最多 20K 字符，单轮 tool 结果轻松超过 100K 字符，远超上下文窗口限制。

**解法**：对单条工具结果设置截断上限（建议 6000 字符），超出时自动截断并附加分页提示：

```
[内容已截断，原始长度 XXXXX 字符。
如需查看更多，请使用 offset/page 参数重新调用工具。]
```

这会引导 LLM 主动使用分页参数，而不是一次性读取所有内容。这是防止上下文爆炸的核心配置参数。

### 坑 8：规划文字被当成最终回答

**现象**：工具模式下，LLM 在调工具前的规划文字（"我先搜索一下目录结构……"）出现在最终回答里。

**根因**：流式路径把所有 `TEXT_DELTA` 都直接作为 `message` 事件推给前端。

**解法**：缓冲所有 `TEXT_DELTA`，遇到 `TOOL_CALL` 事件时才确认本轮是工具轮，将缓冲内容转为 `thinking` 事件（折叠展示）；循环结束后缓冲里剩下的才是最终回答：

```
TEXT_DELTA 缓冲中...
  遇到 TOOL_CALL → 将缓冲发为 thinking 事件（后续 TEXT_DELTA 也是 thinking）
循环结束 → 缓冲剩余内容发为 message（最终答案）
```

---

## 七、流式输出：SSE 协议设计

SSE（Server-Sent Events）比 WebSocket 更适合 AI 对话的单向推送场景：更轻量、自动重连、基于 HTTP，穿防火墙无障碍。

事件类型需要覆盖完整的状态机，尤其是工具模式下的多轮结构：

| 事件 | data 字段 | 含义 |
|------|-----------|------|
| `conversation` | `{session_id, message, created_at}` | 首帧，绑定会话上下文 |
| `thinking_start` | `{round}` | 新一轮 LLM 生成开始，携带轮次编号 |
| `thinking` | `{text}` | 中间规划文字，前端折叠展示 |
| `thinking_stop` | `{duration_ms}` | 本轮 LLM 生成结束，携带耗时 |
| `tool_start` | `{tool, input}` | 工具开始执行，携带入参 |
| `tool_result` | `{tool, input, result, is_error, duration_ms}` | 工具执行完毕，携带结果和耗时 |
| `message` | `{text}` | 最终回答文本增量 |
| `done` | `{}` | 流结束 |
| `error` | `{message}` | 错误信息 |

**所有事件的 data 字段统一为 JSON**，不要裸字符串和 JSON 混用。前端用一个统一的 `safeJson()` 函数处理所有事件，扩展新事件类型不会破坏已有解析逻辑。

**流式热路径不要每帧写库**。`message`、`thinking`、`tool_result` 这类事件频率很高，如果每个 token 都落 SQLite，会明显拖慢响应，甚至让前端一直停在处理中。当前做法是运行时保存在进程内 run 状态里，SSE 重连先拿内存快照；结束、取消或失败时，再一次性写入最终状态和消息历史。

**工具耗时的价值**：`tool_start` 和 `tool_result` 都携带时间信息，前端可以展示每个工具的实际执行耗时，方便用户判断哪个工具慢，也方便开发调试。

---

## 八、RAG 系统：索引与检索链路

### 索引管道

```
文本输入 → 清洗 → 切块 → 向量化 → ChromaDB 入库（upsert）
```

入库使用 `upsert` 而非 `add`，保证幂等性——重复索引同一文档不会报错，也不会产生重复条目。

ChromaDB 的 Python SDK 是同步 API，在 async 应用里需要用 `run_in_executor` 包裹所有 DB 操作，否则会阻塞事件循环。

### 检索链路

```
用户查询 → 向量化 → 向量召回（top-k） → 重排 → 拼装引用注入系统提示
```

每段检索结果附带来源信息（文档标题 / source_id），注入系统提示时格式化为：

```
[来源: 文档标题]
内容片段

---

[来源: 另一文档]
另一内容片段
```

LLM 在生成回答时会引用这些来源，前端可以展示引用标注。`top_k` 不硬编码，通过运行时参数配置，不同场景需要不同的检索深度。

### 三层系统提示组合

RAG 上下文、Skill 提示词、全局指令三者按优先级顺序拼接注入：

```
[Skill 专属提示词]       ← 当前激活 Skill 的角色定义
---
[全局指令]               ← 用户配置的所有对话追加指令
---
[RAG 检索上下文]         ← 本次查询相关的文档片段
```

这三层都是动态的：Skill 可以按对话切换，全局指令可以实时修改，RAG 上下文每次查询都重新检索。每次请求都重新组装，不缓存，保证始终使用最新配置。

---

## 九、Skill 系统：提示词的运行时管理

硬编码系统提示词是最常见的反模式——每次要调整角色定义都需要改代码重新部署。

更好的做法是把提示词作为数据管理：

- **Skill 提示词库**：支持 CRUD，内置 Skill 标记为不可删除
- **默认 Skill**：用户可设置全局默认，每次新建对话自动激活
- **会话级切换**：每个对话可以独立指定 Skill，不影响其他对话
- **运行时参数**：Temperature、RAG top_k、上下文长度等存储在键值表，按请求读取，**不需要重启**即时生效

运行时参数不重启生效这个细节很重要。生产环境中频繁重启是高风险操作，参数调整应该尽量不依赖重启。

---

## 十、多 Agent 协作：最小角色集与断点恢复

多 Agent 系统不需要一开始就设计很多角色。**Planner / Executor / Reviewer 三个角色就能覆盖大多数任务场景**：

```
用户请求
  → Planner：理解需求，拆分子任务
  → Executor：逐个执行子任务，使用工具
  → Reviewer：检查结果质量，决定是否需要重试
  → 输出最终结果
```

**工作流 checkpoint 持久化**：每个节点完成后保存快照到 Redis，进程崩溃或重启后可以从上次完成的节点续跑，不需要从头开始。这对长时间运行的任务（几分钟甚至几十分钟）至关重要。

**人工审批节点**：高风险操作（比如写入文件、发送消息）执行前可以暂停等待人工确认，通过 API 回调恢复执行。

**与 LangGraph 的兼容策略**：编排逻辑通过 `WorkflowOrchestrator` 接口抽象，默认实现是 Native Orchestrator，后续可以新增 LangGraph Orchestrator，Application 层不需要改动，通过配置开关切换。

---

## 十一、前端状态管理：按职责拆分 Store

AI 对话前端的状态比普通应用复杂：多会话并行、流式消息实时更新、工具调用动态展示……所有状态塞进一个 Store 很快就会乱套。

### 三个 Store 各司其职

**会话与消息 Store**：
- 会话列表（元数据）+ 消息分桶（按 conversationId 分别存储）
- 流式状态绑定会话 ID：`streamingConversationId`
- `isStreaming` 全局锁，生成中不允许切换会话

**Skill 与运行时参数 Store**：
- Skill 列表、当前激活 Skill ID
- Temperature、上下文长度等运行时参数从后端拉取

**应用设置 Store**：
- 只管主题偏好（light / dark），持久化到 localStorage

### 关键状态管理规则

**消息只写当前激活会话**。流式增量必须携带 `conversationId` 校验，防止切换会话时增量写错地方：

```typescript
// 更新消息时始终校验 ID，不依赖"当前"活跃 ID
const msgs = (s.messagesByConversation[activeConversationId] ?? []).map(m =>
    m.id === assistantId ? { ...m, ...patch } : m
)
```

**取消流式后立即收敛状态**。AbortController 取消后，把 `status: 'streaming'` 的消息改为 `done`，把未完成的工具调用也标为完成，防止 spinner 永久残留。

**同一个 run 只能订阅一次**。前端用 `subscribedRunIds` 记录正在订阅的 `run_id`。如果恢复流程已经接管某个 run，发送流程就清理自己乐观插入的临时消息，避免同一个回答出现两个一模一样的气泡。

**工具调用每次独立存储**。同一工具被调用多次时分别展示，不做聚合——聚合会丢失每次调用的参数差异。每个 `ToolActivity` 条目独立记录 `input`、`result`、`isError`、`durationMs`。

**分页加载历史消息**。历史消息不在 localStorage 持久化，按需从后端拉取（`offset` / `limit`），向上滚动时追加加载更早的消息。用 IntersectionObserver 监听顶部哨兵元素，而不是监听 `scrollTop < 80`——后者在已经滚到顶部时不会再触发。

---

## 十二、几个容易被忽视的工程细节

**会话删除要同步清理后端状态。** 前端删除会话时，同步调用后端接口清理对应的 Redis / SQLite 记录。否则下次用相同 session_id 发请求，会读到已删除会话的历史消息，产生"幽灵上下文"。

**首条消息自动设标题。** 新会话的第一条用户消息截断到固定长度作为标题，不需要用户手动命名。

**Anthropic 流式 tool args 的特殊处理。** Anthropic 流式 API 返回工具参数时，是以 `input_json_delta` 事件分块推送的——不是一次完整的 JSON，而是 JSON 字符串的片段。必须在客户端累积所有片段后拼接，再做 `JSON.parse`。如果直接解析每一帧，会因为 JSON 不完整而报错，工具参数始终是 `{}`。

**`finally` 块保存状态。** 对话和工具循环的状态保存必须在 `finally` 块里执行，无论成功还是异常都要保存。只在成功路径保存会导致出错时用户消息丢失，下次对话从上上条开始——用户体验非常差。

**配置驱动优先，不要硬编码行为。** 关键参数（Token 截断、工具轮次、超时时长、检索 top_k、上下文长度）都应该通过环境变量配置，不同部署环境有不同需求：

```env
ASTRACORE__AGENT__MAX_TOOL_RESULT_CHARS=6000   # 核心防爆参数，建议 6000
ASTRACORE__AGENT__MAX_TOOL_ITERATIONS=10       # 工具循环最大轮次，0 为不限
ASTRACORE__AGENT__TOOL_TIMEOUT_S=120           # 单次工具超时
```

---

## 十三、测试策略

测试分三个层次，各有侧重：

**单元测试（Domain + Application）**：mock 掉所有 Ports，测试业务逻辑。这一层运行最快，不需要任何外部服务：

- Domain 规则：Token 预算计算、上下文裁剪算法正确性
- PolicyEngine：retry 逻辑、超时行为是否真正生效（不能是死代码）
- ChatUseCase / ToolLoopUseCase：各种异常路径的处理

**合约测试（Adapters）**：验证每个 Adapter 对 Port 接口的兼容性。换实现时只需补充对应的合约测试。

**集成测试（端到端链路）**：连接真实的 SQLite / ChromaDB，验证完整的 Chat + Tool + Memory + RAG 链路。这一层不跑在 CI 主流程里（避免依赖外部服务），但在发布前必须执行。

---

## 十四、未来方向

当前实现的一些方案是"能用但不够优雅"的折衷，有几个方向值得继续深入。

### 上下文管理：从截断到压缩

现在的上下文管理是简单粗暴的"从头裁剪"——超出 N 条就把最早的消息删掉。这会丢失对话的早期关键信息，比如用户在第一条消息里说了"我是后端开发，不要给我解释基础概念"，裁掉之后 AI 就忘了。

更好的方案是**分层压缩**：

```
最近 K 轮（原文保留，保证细节准确）
  +
中间段（摘要压缩，保留关键事件和结论）
  +
早期（只保留开头系统提示和用户背景信息）
```

触发时机：上下文接近预算阈值时，自动对中间段做一次摘要调用，用摘要替换原始消息序列。这样既控制了长度，又不完全丢失历史信息。

更进一步的方向是**语义感知裁剪**：不按轮次裁，而是按信息密度裁——重复的闲聊轮可以优先压缩，包含代码或具体数据的轮次优先保留。这需要对每条消息做轻量的重要性评分，成本比纯截断高，但效果好得多。

### Skill 系统：从手动维护到自动演化

当前的 Skill 是完全手动管理的：用户写提示词，测试效果，手动调整。规模大了之后这套流程很难维持。

**自动沉淀 Skill**。当用户在某类对话里反复给出相似的补充指令（"回答简短一点"、"用中文回答"），系统可以识别这个模式，提示用户是否要把这条指令固化成 Skill 的一部分。本质上是把隐式偏好显式化。

**Skill 效果评分**。对每次使用 Skill 的对话收集反馈信号——用户是否继续追问（可能说明回答不到位）、是否点了重新生成、对话是否在几轮内自然结束（可能说明任务完成得好）。把这些信号聚合成 Skill 的质量分。

**低分 Skill 淘汰机制**。长期低分且低使用频率的 Skill 自动进入"待归档"状态，提示用户确认是否删除。避免 Skill 库越积越大，里面全是没人用的废弃提示词。

**Skill 版本管理**。修改 Skill 时保留历史版本，可以回滚到效果更好的旧版本。对于团队共享的 Skill，可以做 A/B 测试：一半请求用旧版，一半用新版，对比效果再决定是否推全。

### 工具调用：从串行到并行

当前的工具循环是串行的——一个工具执行完才开始下一个。如果 LLM 在一轮里决定调用三个相互独立的工具（比如同时搜索三个关键词），串行执行会浪费大量时间。

LLM 在一次响应里可以返回多个 `tool_use` 块，没有依赖关系的工具完全可以并发执行：

```
LLM 决策 → [tool_A, tool_B, tool_C]
                ↓ asyncio.gather
           并发执行三个工具
                ↓
           汇总所有 ToolResult → 继续下一轮
```

最保守的策略是：同一轮的工具全部并发，不同轮之间保持串行（后一轮依赖前一轮的结果）。这个改动不需要修改 Ports 接口，只改 `ToolLoopUseCase` 内部实现即可。

### 记忆系统：从会话级到用户级

当前的记忆是按 session 存储的，跨会话之间没有关联。用户在 A 对话里告诉 AI 自己的偏好，切到 B 对话后 AI 不记得。

更完整的记忆体系需要引入**用户级记忆层**：

- 跨会话的用户偏好（语言风格、专业背景、常用工具）自动提炼并持久化
- 每次对话开始时，把用户级记忆注入系统提示
- 用户可以查看和编辑自己的"记忆档案"，删除过时或错误的条目

这本质上是在做一个轻量的"用户画像"，让 AI 真正记住用户是谁，而不是每次都从零开始。

---

## 总结

构建一个生产可用的 AI 框架，技术难度本身不高，难在**把边界想清楚**：

- 谁负责业务逻辑，谁负责外部 I/O —— 依赖方向朝内，内层零感知
- 哪些状态需要持久化，哪些可以丢 —— 宁可多存一层，不要单点依赖
- 异常情况下如何降级，而不是直接崩溃 —— 每个外部调用都要有 fallback
- 策略（重试、超时、截断）放在哪里统一管理 —— 集中治理，不要散落

把这些问题想清楚之后，代码写起来反而很顺。大多数工程事故不是因为技术不够，而是某个边界情况没有被考虑到。工具循环那 8 个坑，每一个本质上都是"某处没有处理边界情况"。

当前这些设计都是"能用"的折衷方案，未来方向已经很清晰：上下文从截断走向压缩、Skill 从手动维护走向自动演化、工具从串行走向并行、记忆从会话级走向用户级。每一步都不是推翻重来，而是在现有的 Ports & Adapters 边界内换一个更好的实现。
